"""MCP server entry point — expose report-skill operations as MCP tools.

Any MCP-compatible client (Claude Desktop, Continue, Cursor, custom LLM
agents) can connect via stdio and call these tools to read/write
ReportArchive reports. The dispatchers wrap the same functions the CLI
uses, so behavior is identical across CLI / Claude Code skill / MCP.

Usage from a client config (Claude Desktop example, mcp.json):

    {
      "mcpServers": {
        "report-skill": {
          "command": "report-skill-mcp",
          "env": {
            "REPORT_API_PASSWORD": "<service-account-password>",
            "REPORT_API_EMAIL": "bot@reportskill.app"
          }
        }
      }
    }

Authentication: the server reads `.env` via the same resolution chain as
the CLI (REPORT_SKILL_ENV env var, then `<cwd>/.env`, then
`%LOCALAPPDATA%/report-skill/.env` on Windows or `~/.report-skill/.env`).
Override via env vars in the MCP config when launching from another
machine. The `SKILL_LLM_PROVIDER` env decides which LLM (or bridge) the
LLM-using tools call.
"""
from __future__ import annotations

# Force UTF-8 streams (Korean labels, en-/em-dashes, etc. on cp949 consoles).
# Must run BEFORE rich imports inside any sibling module.
import sys as _sys_init
try:
    _sys_init.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    _sys_init.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, OSError):
    pass

import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from report_skill import (
    catalog as catalog_mod,
    examples as examples_mod,
    orchestrator,
    report_builder,
    report_ops,
    schemas,
    tags as tags_mod,
    template_suggest,
    tier as tier_mod,
    upload_chain,
    widget_suggest,
)
from report_skill.adapters import ADAPTERS
from report_skill.client import ApiError, AuthorLockedError, ReportArchiveClient

# v0.5.2 — typed RA-stable exception subclasses (A2). Imported defensively so
# the MCP server still loads if client.py is older. Each subclass surfaces a
# structured `{error: <code>, reason, code, report_id}` payload via call_tool
# instead of the generic {error: "API error", ...} envelope.
try:
    from report_skill.client import (
        CompositeRevisionConflict,
        FinalizedReadOnlyError,
        LockHeldByOtherError,
        LockNotHeldError,
        NoEditPermissionError,
        OutOfWorkspaceScopeError,
        RevisionMismatchError,
    )
except ImportError:  # pragma: no cover — older client.py without typed subclasses
    CompositeRevisionConflict = None  # type: ignore[assignment,misc]
    FinalizedReadOnlyError = None  # type: ignore[assignment,misc]
    LockHeldByOtherError = None  # type: ignore[assignment,misc]
    LockNotHeldError = None  # type: ignore[assignment,misc]
    NoEditPermissionError = None  # type: ignore[assignment,misc]
    OutOfWorkspaceScopeError = None  # type: ignore[assignment,misc]
    RevisionMismatchError = None  # type: ignore[assignment,misc]

SERVER_NAME = "report-skill"

# v0.5.1 — 13 optional fields shared by report_create / report_update.
# Three related-info + ten page-level. Pass-through to builder / report_ops
# without any per-field plumbing — keeps schema, dispatcher and downstream
# signatures synchronized (one source of truth).
_REPORT_PASS_THROUGH = (
    "collab_workspace_slugs",
    "entity_ids",
    "report_type_id",
    "page_width_px",
    "page_gap_px",
    "page_blend_blocks",
    "page_slide_guide",
    "page_slide_ratio",
    "page_slide_ratio_custom_w",
    "page_slide_ratio_custom_h",
    "page_rich_text_prefix_d0",
    "page_rich_text_prefix_d1",
    "page_rich_text_prefix_d2",
    # v0.5.2 — C5: lifecycle close date (ReportRead.closed_at, Optional[date]).
    "closed_at",
)

# Shared JSON-schema fragment for the 13 fields (RA-exact constraints —
# ranges from backend ReportCreate/ReportUpdate, slide_ratio enum verbatim).
_REPORT_EXTRA_PROPS: dict = {
    "collab_workspace_slugs": {
        "type": "array",
        "items": {"type": "string"},
        "description": "협업 부서 워크스페이스 슬러그 목록 (빈 배열 = 전체 해제)",
    },
    "entity_ids": {
        "type": "array",
        "items": {"type": "integer"},
        "description": "엔티티 태그 id 목록 (빈 배열 = 전체 해제)",
    },
    "report_type_id": {
        "type": "integer",
        "description": "report_types FK; null/omit = no tag",
    },
    "page_width_px": {"type": "integer", "minimum": 320, "maximum": 3000},
    "page_gap_px": {"type": "integer", "minimum": 0, "maximum": 200},
    "page_blend_blocks": {"type": "boolean"},
    "page_slide_guide": {"type": "boolean"},
    "page_slide_ratio": {
        "type": "string",
        "enum": ["16:9", "4:3", "16:10", "custom"],
    },
    "page_slide_ratio_custom_w": {"type": "integer", "minimum": 1, "maximum": 10000},
    "page_slide_ratio_custom_h": {"type": "integer", "minimum": 1, "maximum": 10000},
    "page_rich_text_prefix_d0": {"type": "string", "maxLength": 8},
    "page_rich_text_prefix_d1": {"type": "string", "maxLength": 8},
    "page_rich_text_prefix_d2": {"type": "string", "maxLength": 8},
    # v0.5.2 — C5: closed_at is Optional[date] (ISO YYYY-MM-DD) — pass null
    # to clear the field server-side once the builder/ops _UNSET sentinel
    # plumbing lands (C7). String "YYYY-MM-DD" or explicit null both accepted.
    "closed_at": {
        "type": ["string", "null"],
        "description": "ISO date (YYYY-MM-DD) to mark the report closed; null clears",
    },
}


# --------------------------------------------------------------------------- #
# Tool catalog
# --------------------------------------------------------------------------- #
def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> Tool:
    return Tool(
        name=name,
        description=description,
        inputSchema={
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
    )


TOOLS: list[Tool] = [
    # ---- read-only / introspection -------------------------------------- #
    _tool("ping", "Verify the ReportArchive API + service account credentials work.", {}),

    _tool(
        "templates_list",
        "List all available ReportArchive templates (id, name, category, block types).",
        {"category": {"type": "string", "description": "optional category filter"}},
    ),
    _tool(
        "templates_show",
        "Inspect one template — block ids + widget types + key props.",
        {
            "template_id": {"type": "string"},
            "version": {"type": "integer", "minimum": 1},
        },
        ["template_id"],
    ),
    _tool(
        "templates_suggest",
        "Recommend templates that match the given text (keyword scoring + optional LLM tie-break).",
        {
            "text": {"type": "string", "description": "user's natural-language description"},
            "top_k": {"type": "integer", "default": 3, "minimum": 1, "maximum": 8},
            "use_llm": {"type": "string", "enum": ["auto", "never", "always"], "default": "auto"},
        },
        ["text"],
    ),
    _tool(
        "widgets_catalog",
        "Return the cached widget catalog (33 widget types, props_schema + content_schema + hash).",
        {"widget_type": {"type": "string", "description": "optional — return only this widget"}},
    ),
    _tool(
        "widgets_suggest_extras",
        "Detect data shapes in text that warrant visual widgets (chart, milestone, pie, etc).",
        {
            "text": {"type": "string"},
            "max_extras": {"type": "integer", "default": 5, "minimum": 1, "maximum": 10},
            "use_llm": {"type": "string", "enum": ["auto", "never", "always"], "default": "auto"},
        },
        ["text"],
    ),
    _tool(
        "report_show",
        "Fetch a report and return its metadata + per-block summary.",
        {
            "report_id": {"type": "integer"},
            "page_index": {"type": "integer", "description": "optional — show only this page"},
        },
        ["report_id"],
    ),
    _tool(
        "report_lock_status",
        "Check the author-lock state of a report. Returns "
        "author_lock_enabled / author_lock_reason / author_lock_set_at "
        "from the Report record. Read-only — does NOT toggle the lock "
        "(service accounts are not owners, so they cannot SET the lock).",
        {"report_id": {"type": "integer"}},
        ["report_id"],
    ),
    _tool(
        "examples_status",
        "Counts of widget examples that are ok / stale / missing / orphan vs the cached catalog.",
        {},
    ),
    _tool("tier_show", "Active S/M/W tier + policy knobs (blocks_per_call, repair passes, etc).", {}),

    # ---- write — single-call ops ---------------------------------------- #
    _tool(
        "report_create",
        "Create a new report. Required: template_id and a blocks dict keyed by block_id. "
        "Each block's value can be a string, list, or dict — adapter normalizes it.",
        {
            "template_id": {"type": "string"},
            "blocks": {"type": "object", "description": "{block_id: anything-ish}"},
            "extra_blocks": {
                "type": "array",
                "description": "ad-hoc visual blocks not in the template",
                "items": {"type": "object"},
            },
            "title": {"type": "string"},
            "report_date": {"type": "string", "description": "ISO YYYY-MM-DD"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "phase": {"type": "string", "enum": ["drafting", "reviewing", "finalized"]},
            "lifecycle": {"type": "string", "enum": ["single_shot", "ongoing"]},
            "status": {"type": "string",
                       "description": "LEGACY — use `phase` instead. Auto-mapped: "
                                      "draft→drafting, in_progress→reviewing, completed→finalized."},
            "allow_failures": {"type": "boolean", "default": False},
            "mount_to": {"type": "array", "items": {"type": "string"},
                         "description": "after create, auto-mount onto these board workspace(s)"},
            **_REPORT_EXTRA_PROPS,
        },
        ["template_id", "blocks", "title"],
    ),
    _tool(
        "report_update",
        "Patch specific blocks of an existing report. Other blocks preserved. Auto edit-lock. "
        "CR-11: new extras added via extra_blocks are auto-merged into the page's "
        "blocks_order so they actually render. Pass an explicit blocks_order to override.",
        {
            "report_id": {"type": "integer"},
            "blocks": {"type": "object"},
            "extra_blocks": {"type": "array", "items": {"type": "object"}},
            "blocks_order": {"type": "array", "items": {"type": "string"},
                             "description": "explicit per-page render order; "
                                            "overrides the auto-merge of new extras"},
            "title": {"type": "string"},
            "phase": {"type": "string", "enum": ["drafting", "reviewing", "finalized"]},
            "lifecycle": {"type": "string", "enum": ["single_shot", "ongoing"]},
            "status": {"type": "string", "description": "LEGACY alias for phase"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "page_index": {"type": "integer", "default": 0},
            # v0.5.2 — A6: mirror report_create's failed_count gate so partial
            # patches don't silently land. Set true to PATCH anyway.
            "allow_failures": {"type": "boolean", "default": False},
            **_REPORT_EXTRA_PROPS,
        },
        ["report_id"],
    ),
    _tool(
        "report_revise",
        "LLM-driven block-level revision of an existing report. For each target "
        "block: fetch current content, prompt the LLM with (current + instruction "
        "+ schema), validate, PATCH only blocks whose content actually changed. "
        "CR-1 scoped_content protection applies — blocks not in block_ids "
        "(and not changed when revise_all=true) are left untouched on the server. "
        "Either block_ids or revise_all must be set. Requires an LLM provider "
        "(ANTHROPIC_API_KEY / OPENAI_API_KEY / OLLAMA_BASE_URL / bridge).",
        {
            "report_id": {"type": "integer"},
            "instruction": {"type": "string",
                            "description": "natural-language revision instruction"},
            "block_ids": {"type": "array", "items": {"type": "string"},
                          "description": "block ids on the page to revise"},
            "revise_all": {"type": "boolean", "default": False,
                           "description": "revise every filled block on the page (LLM may "
                                          "return some unchanged; patch contains only diffs)"},
            "page_index": {"type": "integer", "default": 0},
            "dry_run": {"type": "boolean", "default": False,
                        "description": "return the patch JSON without PATCHing"},
            "max_tokens": {"type": "integer", "default": 800},
        },
        ["report_id", "instruction"],
    ),
    _tool(
        "report_append",
        "Merge incoming content into existing blocks via per-widget append strategy "
        "(milestone dedupe by date+label, table append rows, bulleted_list dedupe, etc). "
        "Optimistic concurrency: retries on revision_mismatch.",
        {
            "report_id": {"type": "integer"},
            "blocks": {"type": "object", "description": "{block_id: anything-ish to merge}"},
            "page_index": {"type": "integer", "default": 0},
            "max_retries": {"type": "integer", "default": 3},
        },
        ["report_id", "blocks"],
    ),
    _tool(
        "report_add_page",
        "Append a new page to an existing report. New template allowed. "
        "CR-11: the new page's blocks_order is auto-computed (heading extras top, "
        "filled template blocks middle, non-heading extras bottom) so empty "
        "template blocks stay hidden. Pass explicit blocks_order to override.",
        {
            "report_id": {"type": "integer"},
            "template_id": {"type": "string"},
            "blocks": {"type": "object"},
            "name": {"type": "string"},
            "extra_blocks": {"type": "array", "items": {"type": "object"}},
            "blocks_order": {"type": "array", "items": {"type": "string"},
                             "description": "explicit per-page render order; "
                                            "overrides the CR-2/CR-8 auto-compute"},
            # v0.5.2 — A6: mirror report_create's failed_count gate so a page
            # with broken blocks isn't appended silently.
            "allow_failures": {"type": "boolean", "default": False},
        },
        ["report_id", "template_id", "blocks"],
    ),
    _tool(
        "report_delete",
        "DELETE a report. Requires confirm=true to actually delete.",
        {
            "report_id": {"type": "integer"},
            "confirm": {"type": "boolean", "description": "must be true; safety guard"},
        },
        ["report_id", "confirm"],
    ),
    _tool(
        "report_mount",
        "Publish (mount) a report to one or more org board workspaces. New reports "
        "live in the author's personal workspace by default; mounting is the deliberate "
        "publish step. Idempotent — already-mounted boards are silently skipped.",
        {
            "report_id": {"type": "integer"},
            "workspace_slugs": {"type": "array", "items": {"type": "string"},
                                "minItems": 1, "description": "target board slug(s)"},
            "edit_policy": {"type": "string",
                            "enum": ["default", "owner_only", "coauthor"],
                            "default": "default"},
            "note": {"type": "string"},
            "folder_id": {"type": "integer", "description": "org folder within target workspace"},
        },
        ["report_id", "workspace_slugs"],
    ),
    _tool(
        "report_unmount",
        "Remove a report's mount from one workspace board.",
        {
            "report_id": {"type": "integer"},
            "workspace_slug": {"type": "string"},
        },
        ["report_id", "workspace_slug"],
    ),
    _tool(
        "report_mounts",
        "List all workspace boards a report is currently mounted on.",
        {"report_id": {"type": "integer"}},
        ["report_id"],
    ),
    _tool(
        "report_milestone_add",
        "Add ONE milestone event to a report. Auto-detects the milestone block.",
        {
            "report_id": {"type": "integer"},
            "date": {"type": "string", "description": "ISO date or Korean ('8월 1일')"},
            "label": {"type": "string"},
            "status": {"type": "string", "enum": ["pending", "done", "delayed"]},
            "note": {"type": "string"},
            "block_id": {"type": "string"},
            "page_index": {"type": "integer", "default": 0},
        },
        ["report_id", "date", "label"],
    ),
    _tool(
        "report_milestone_remove",
        "Remove milestone events by matching (date, label?).",
        {
            "report_id": {"type": "integer"},
            "date": {"type": "string"},
            "label": {"type": "string"},
            "block_id": {"type": "string"},
            "page_index": {"type": "integer", "default": 0},
        },
        ["report_id", "date"],
    ),
    _tool(
        "file_upload",
        "Upload a local file to /api/files. Returns {file_id, filename, mime_type, size} "
        "ready for embedding in media-type widgets (image, video, attachment, cad_3d, html_embed).",
        {"path": {"type": "string", "description": "absolute path to a local file"}},
        ["path"],
    ),

    # ---- maintenance ---------------------------------------------------- #
    _tool(
        "catalog_sync",
        "Re-fetch /api/widgets + run the bridge, recompute hashes, and report any diff.",
        {},
    ),
    _tool(
        "examples_mine_from_report",
        "Overwrite each widget's example expected_content with real content from a live report.",
        {
            "report_id": {"type": "integer"},
            "overwrite_stale": {"type": "boolean", "default": True},
            "dry_run": {"type": "boolean", "default": False},
        },
        ["report_id"],
    ),
    _tool(
        "tier_set",
        "Persist a tier override (S=premium, M=mid, W=weak local 8B).",
        {"tier": {"type": "string", "enum": ["S", "M", "W"]}},
        ["tier"],
    ),

    # ---- offline export / import (server-less workflow) ----------------- #
    _tool(
        "catalog_sync_templates",
        "Cache every template's full body to .skill-cache/templates/ — required "
        "once (while online) before any offline report_export.",
        {},
    ),
    _tool(
        "report_export",
        "Normalize a draft into a ReportCreate payload JSON file without POSTing. "
        "Use when ReportArchive is unreachable; later call report_import to publish. "
        "Requires catalog_sync + (when offline=true) catalog_sync_templates.",
        {
            "template_id": {"type": "string"},
            "blocks": {"type": "object"},
            "extra_blocks": {"type": "array", "items": {"type": "object"}},
            "title": {"type": "string"},
            "report_date": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "out_path": {"type": "string", "description": "absolute path to write the payload JSON"},
            "offline": {"type": "boolean", "default": False,
                        "description": "fall back to cached templates if API unreachable"},
            "allow_failures": {"type": "boolean", "default": False},
        },
        ["template_id", "blocks", "title", "out_path"],
    ),
    _tool(
        "report_import",
        "Import a previously-saved payload OR bundle.zip. Auto-detects from "
        "the file extension: .zip routes through the bundle path (re-upload "
        "files, swap file_ids, POST), .json POSTs verbatim.",
        {"payload_path": {"type": "string",
                          "description": "absolute path to the payload .json OR bundle .zip"}},
        ["payload_path"],
    ),
    _tool(
        "report_dump",
        "Pack a report + every referenced media file into a portable bundle.zip "
        "so it can be replayed on a different ReportArchive instance via "
        "report_import. Self-contained: payload.json + files/manifest.json + "
        "files/<file_id> bytes.",
        {
            "report_id": {"type": "integer"},
            "out_path": {"type": "string",
                         "description": "where to write bundle.zip "
                                        "(default: cwd/bundle-report-<id>.zip)"},
        },
        ["report_id"],
    ),
    # ---- mention resolvers (LLM → mention://… id lookup) ------------- #
    _tool(
        "reports_search",
        "Resolve a free-text reference (e.g. '지난 주 백엔드 주간보고') to candidate "
        "report ids the LLM can plug into a `mention://report/<id>?ws=<slug>` link. "
        "Wraps GET /api/reports/linkable; the adapter applies NFKC-normalized "
        "case-insensitive substring matching over title + owner_name + "
        "mount_workspaces[].name and ranks exact > title-substring > owner/mount > "
        "recency. Never returns body content — call report_show after id resolution "
        "if needed.",
        {
            "q": {"type": "string",
                  "description": "search keyword (title / owner / mount); NFKC + case-insensitive substring"},
            "workspace_slug": {"type": "string",
                               "description": "restrict to reports whose home workspace_slug exactly matches"},
            "owner_name": {"type": "string",
                           "description": "restrict to reports whose owner_name contains this substring"},
            "mount_slug": {"type": "string",
                           "description": "restrict to reports mounted on this board workspace slug"},
            "date_from": {"type": "string",
                          "description": "ISO date — only reports with report_date >= this"},
            "date_to": {"type": "string",
                        "description": "ISO date — only reports with report_date <= this"},
            "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 50},
        },
        [],
    ),
    _tool(
        "workspaces_list",
        "List department/workspace candidates the LLM can resolve to a "
        "`mention://dept/<slug>` target. Wraps GET /api/workspaces. Filters by "
        "kind (default 'org' — personal leaks user names, virtual owns no data) "
        "and optional case-insensitive substring match over name+slug. Preserves "
        "the server's (sort_order, slug) ordering for deterministic output.",
        {
            "q": {"type": "string",
                  "description": "case-insensitive substring over name+slug"},
            "kind": {"type": "string",
                     "enum": ["all", "org", "personal", "virtual"],
                     "default": "org",
                     "description": "default 'org' is the only mention-eligible class"},
        },
        [],
    ),
    _tool(
        "entity_types_list",
        "List the entity-axis catalog (~7 rows: model_name, customer_name, etc.) "
        "so the LLM knows which `axis=<slug>` values are valid before searching "
        "entities. Wraps GET /api/entity-types. Cheap and stable — adapter caches "
        "the result per-process so chained entity lookups only hit the network once.",
        {},
        [],
    ),
    _tool(
        "entities_list",
        "Resolve a free-text reference (e.g. 'HFP-X1', '현대모비스') to candidate "
        "entity ids for a `mention://entity/<id>?axis=<slug>` link. Wraps "
        "GET /api/entities?type_id=&q=&include_deprecated=&limit=. Accepts either "
        "`axis` (entity_type slug — resolved to type_id via cached entity_types_list) "
        "or `type_id` directly; type_id wins when both given. Excludes deprecated by "
        "default. Hard-caps limit at 200.",
        {
            "q": {"type": "string",
                  "description": "substring over value/code/description"},
            "axis": {"type": "string",
                     "description": "entity-type slug (e.g. 'model_name'); resolved to type_id via entity_types_list"},
            "type_id": {"type": "integer",
                        "description": "entity_type id (overrides axis when both given)"},
            "include_deprecated": {"type": "boolean", "default": False},
            "limit": {"type": "integer", "default": 50, "minimum": 1, "maximum": 200},
        },
        [],
    ),

    # ---- v0.5.0 — copy / link / report-types ---------------------------- #
    _tool(
        "report_copy",
        "Duplicate an existing report. POST /api/reports/{id}/copy. New copy "
        "lands in the caller's personal workspace. mode=content keeps just the "
        "block content; mode=full (default) also copies tags / related-info.",
        {
            "report_id": {"type": "integer"},
            "title": {"type": "string", "description": "title for the new copy"},
            "mode": {"type": "string", "enum": ["content", "full"], "default": "full"},
            "folder_id": {"type": "integer", "description": "optional destination folder"},
        },
        ["report_id", "title"],
    ),
    _tool(
        "report_add_link",
        "Attach a related-report link from one report to another. "
        "POST /api/reports/{id}/links. kind is required by the backend; "
        "defaults to 'related' at the client layer.",
        {
            "report_id": {"type": "integer", "description": "source report id"},
            "to_report_id": {"type": "integer", "description": "target report id"},
            "kind": {"type": "string", "default": "related",
                     "description": "link kind (e.g. 'related', 'follow_up')"},
            "label": {"type": "string", "description": "optional short note (<=200 chars)"},
            "direction": {
                "type": "string",
                "enum": ["outgoing", "incoming"],
                "default": "outgoing",
                "description": "'outgoing' (default — report_id → to_report_id) or "
                               "'incoming' (server swaps from/to so the link points the other way)",
            },
        },
        ["report_id", "to_report_id"],
    ),
    _tool(
        "report_types_list",
        "List the report-type catalog. GET /api/report-types. Returns the "
        "official + unofficial types so callers can map a name to report_type_id.",
        {},
        [],
    ),

    # ---- v0.5.0 — publish / unpublish ----------------------------------- #
    _tool(
        "report_publish",
        "Mark a report finalized — phase=finalized + fan-out notifications to "
        "every mounted board. POST /api/reports/{id}/publish. Owner-only.",
        {"report_id": {"type": "integer"}},
        ["report_id"],
    ),
    _tool(
        "report_unpublish",
        "Revert a finalized report back to drafting. POST /api/reports/{id}/unpublish. "
        "Owner-only. Idempotent when already drafting.",
        {"report_id": {"type": "integer"}},
        ["report_id"],
    ),

    # ---- v0.5.0 — folders + mount config -------------------------------- #
    _tool(
        "folders_list",
        "List folders for a workspace board. GET /api/folders?workspace_slug=. "
        "Pass an org slug for that board's folders, or 'personal-<user_id>' for "
        "a user's personal folders. Omit workspace_slug to get the caller's own "
        "personal folders. Side effect: server may auto-create defaults.",
        {"workspace_slug": {"type": "string"}},
        [],
    ),
    _tool(
        "report_mount_set_folder",
        "Move a mounted report into (or out of) a folder on a board. "
        "PUT /api/mounts/{rid}/{slug}/folder. folder_id null clears the folder.",
        {
            "report_id": {"type": "integer"},
            "workspace_slug": {"type": "string"},
            "folder_id": {"type": "integer",
                          "description": "destination folder id; omit or null to clear"},
        },
        ["report_id", "workspace_slug"],
    ),
    _tool(
        "report_mount_set_edit_policy",
        "Set the edit policy for a mount. PUT /api/mounts/{rid}/{slug}/edit-policy. "
        "Owner-only. Valid policies: default, owner_only, coauthor.",
        {
            "report_id": {"type": "integer"},
            "workspace_slug": {"type": "string"},
            "edit_policy": {"type": "string",
                            "enum": ["default", "owner_only", "coauthor"]},
        },
        ["report_id", "workspace_slug", "edit_policy"],
    ),

    # ---- v0.5.0 — template scope ---------------------------------------- #
    _tool(
        "template_set_scope",
        "Set a template's owner workspace scope. PATCH /api/templates/{id}/scope. "
        "Empty list / omitted = 전사 (global). Manager-only; cannot edit global "
        "templates here. Metadata-only — no version bump.",
        {
            "template_id": {
                "type": "string",
                "description": "template slug (e.g. 'engineering-rca')",
            },
            "owner_workspace_slugs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "workspace slugs that own the template; empty/omitted = global",
            },
        },
        ["template_id"],
    ),

    # ---- v0.5.0 — presets ----------------------------------------------- #
    _tool(
        "presets_list",
        "List presets visible to the caller's workspace tree. GET /api/presets. "
        "Optionally narrow by template_id.",
        {"template_id": {"type": "string",
                         "description": "optional — only presets for this template"}},
        [],
    ),
    _tool(
        "preset_create",
        "Snapshot a report as a reusable preset. POST /api/presets. "
        "owner_workspace_slugs null/empty = 전사 (global preset).",
        {
            "report_id": {"type": "integer", "description": "source report id"},
            "name": {"type": "string"},
            "owner_workspace_slugs": {
                "type": "array", "items": {"type": "string"},
                "description": "workspace slugs that may use the preset; omit for global",
            },
            # v0.5.2 — D1: optional human-readable description (PresetCreate.description).
            "description": {
                "type": "string",
                "description": "optional preset description shown in the preset picker",
            },
        },
        ["report_id", "name"],
    ),
    _tool(
        "report_new_from_preset",
        "Instantiate a new report from a preset. POST /api/presets/{id}/new-report. "
        "Lands in the caller's personal workspace. title defaults to preset name.",
        {
            "preset_id": {"type": "integer"},
            "title": {"type": "string", "description": "optional override; default = preset name"},
            "folder_id": {"type": "integer", "description": "optional destination folder"},
        },
        ["preset_id"],
    ),
    _tool(
        "preset_delete",
        "Delete a preset. DELETE /api/presets/{id}. Creator-only (or system admin).",
        {"preset_id": {"type": "integer"}},
        ["preset_id"],
    ),

    # ---- v0.5.0 — composites -------------------------------------------- #
    _tool(
        "composite_get",
        "Fetch a composite report. GET /api/composites/{id}. Returns metadata, "
        "summary_widgets, and the items list.",
        {"composite_id": {"type": "integer"}},
        ["composite_id"],
    ),
    _tool(
        "composite_summary_set",
        "Replace a composite's summary_widgets. PATCH /api/composites/{id}. "
        "Pass expected_revision for optimistic concurrency. Body sends only "
        "summary_widgets (+ expected_revision when given) to keep the PATCH narrow.",
        {
            "composite_id": {"type": "integer"},
            "summary_widgets": {
                "type": "array", "items": {"type": "object"},
                "description": "list of widget dicts (same grammar as report extras)",
            },
            "expected_revision": {"type": "integer", "minimum": 1,
                                  "description": "optimistic-concurrency guard"},
        },
        ["composite_id", "summary_widgets"],
    ),
    _tool(
        "composites_submittable_for",
        "List composites the given report can be submitted to. "
        "GET /api/composites/submittable-for/{report_id}. Each row includes "
        "already_item / already_pending flags so callers can hide dupes.",
        {"report_id": {"type": "integer"}},
        ["report_id"],
    ),
    _tool(
        "composites_requests_list",
        "List submission requests on a composite. "
        "GET /api/composites/{id}/requests. status_filter defaults to 'pending' "
        "when omitted.",
        {
            "composite_id": {"type": "integer"},
            "status_filter": {"type": "string",
                              "description": "e.g. pending|accepted|rejected|withdrawn"},
        },
        ["composite_id"],
    ),
    _tool(
        "composites_submit",
        "Submit a report to a composite for inclusion. "
        "POST /api/composites/{id}/requests. Requires can_read on the report.",
        {
            "composite_id": {"type": "integer"},
            "report_id": {"type": "integer"},
            "note": {"type": "string", "description": "optional note (<=1000 chars)"},
        },
        ["composite_id", "report_id"],
    ),
    _tool(
        "composites_request_accept",
        "Accept a pending composite submission. "
        "POST /api/composites/{id}/requests/{request_id}/accept. Composite owner only.",
        {
            "composite_id": {"type": "integer"},
            "request_id": {"type": "integer"},
        },
        ["composite_id", "request_id"],
    ),
    _tool(
        "composites_request_reject",
        "Reject a pending composite submission. "
        "POST /api/composites/{id}/requests/{request_id}/reject. Composite owner only. "
        "Backend currently ignores reason — kept for forward-compat.",
        {
            "composite_id": {"type": "integer"},
            "request_id": {"type": "integer"},
            "reason": {"type": "string", "description": "optional rejection note"},
        },
        ["composite_id", "request_id"],
    ),
    _tool(
        "composites_request_withdraw",
        "Withdraw a pending composite submission. "
        "POST /api/composites/{id}/requests/{request_id}/withdraw. Requester self / "
        "composite owner / system admin.",
        {
            "composite_id": {"type": "integer"},
            "request_id": {"type": "integer"},
        },
        ["composite_id", "request_id"],
    ),

    # ---- v0.6.0 — composites body editing ------------------------------ #
    _tool(
        "composite_create",
        "Create a new composite report. POST /api/composites. `kind` is the "
        "CompositeKind enum value (e.g. 'recurring' | 'theme'). `view_mode` "
        "defaults to 'single'. Pass `items` (each with exactly one of "
        "ref_report_id / ref_composite_id) to seed the body at creation "
        "time, or skip it and use `composite_items_set` later.",
        {
            "title": {"type": "string"},
            "kind": {"type": "string",
                     "description": "CompositeKind enum value"},
            "view_mode": {"type": "string", "default": "single",
                          "description": "single | two_col | list"},
            "period_date": {"type": "string",
                            "description": "ISO YYYY-MM-DD"},
            "workspace_slug": {"type": "string",
                               "description": "owner workspace; defaults to active"},
            "description": {"type": "string", "default": ""},
            "two_col_view": {"type": "boolean",
                             "description": "legacy alias of view_mode"},
            "summary_widgets": {"type": "array", "items": {"type": "object"}},
            "items": {"type": "array", "items": {"type": "object"}},
        },
        ["title", "kind"],
    ),
    _tool(
        "composite_update",
        "Update a composite's top-level fields. PATCH /api/composites/{id}. "
        "Only the supplied fields are sent. Pass `expected_revision` for "
        "optimistic concurrency (409 CompositeRevisionConflict on mismatch).",
        {
            "composite_id": {"type": "integer"},
            "title": {"type": "string"},
            "view_mode": {"type": "string"},
            "description": {"type": "string"},
            "two_col_view": {"type": "boolean"},
            "period_date": {"type": ["string", "null"],
                            "description": "ISO date or null to clear"},
            "summary_widgets": {"type": "array", "items": {"type": "object"}},
            "expected_revision": {"type": "integer", "minimum": 1},
        },
        ["composite_id"],
    ),
    _tool(
        "composite_items_set",
        "Replace a composite's items list. PATCH /api/composites/{id} with "
        "`items` set. Each item supplies exactly one of "
        "ref_report_id / ref_composite_id, optional note + display_column + "
        "group_name. Pass `expected_revision` for optimistic concurrency.",
        {
            "composite_id": {"type": "integer"},
            "items": {"type": "array", "items": {"type": "object"},
                      "description": "ordered replacement list"},
            "expected_revision": {"type": "integer", "minimum": 1},
        },
        ["composite_id", "items"],
    ),
    _tool(
        "composite_delete",
        "Delete a composite. DELETE /api/composites/{id}. Owner / sys admin only.",
        {"composite_id": {"type": "integer"}},
        ["composite_id"],
    ),
    _tool(
        "composite_publish",
        "Publish a composite. POST /api/composites/{id}/publish. Owner only; "
        "for recurring composites freezes each item's content into snapshot. "
        "Idempotent.",
        {"composite_id": {"type": "integer"}},
        ["composite_id"],
    ),
    _tool(
        "composite_unpublish",
        "Unpublish a composite. POST /api/composites/{id}/unpublish. Owner "
        "only; clears published_at + per-item snapshots so the composite "
        "returns to live-fetch + editable mode. Idempotent.",
        {"composite_id": {"type": "integer"}},
        ["composite_id"],
    ),

    # ---- v0.6.0 — report activities timeline --------------------------- #
    _tool(
        "report_activities",
        "Fetch a report's activity timeline (lifecycle, lock, edit events). "
        "GET /api/reports/{id}/activities. Cursor pagination via `before_id` "
        "(pass the smallest id of the previous page). Public-only viewers "
        "receive an empty list per backend policy.",
        {
            "report_id": {"type": "integer"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200,
                      "default": 20},
            "before_id": {"type": "integer", "minimum": 1,
                          "description": "cursor — pass smallest id from prev page"},
        },
        ["report_id"],
    ),

    # ---- v0.6.0 — notifications inbox ---------------------------------- #
    _tool(
        "notifications_list",
        "List the caller's notification inbox. GET /api/notifications. "
        "Returns `{items, unread_count}`. Pass `unread_only=true` to filter, "
        "`before_id` for pagination.",
        {
            "unread_only": {"type": "boolean", "default": False},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200,
                      "default": 50},
            "before_id": {"type": "integer", "minimum": 1},
        },
        [],
    ),
    _tool(
        "notifications_unread_count",
        "Return only the unread-notification badge count for the caller. "
        "GET /api/notifications/unread-count.",
        {},
        [],
    ),
    _tool(
        "notification_mark_read",
        "Mark one notification as read. PATCH /api/notifications/{id}/read. "
        "Idempotent.",
        {"notification_id": {"type": "integer"}},
        ["notification_id"],
    ),
    _tool(
        "notifications_mark_all_read",
        "Mark every unread notification as read. POST "
        "/api/notifications/mark-all-read. Returns the number of rows flipped.",
        {},
        [],
    ),
]


# --------------------------------------------------------------------------- #
# Tool dispatchers
# --------------------------------------------------------------------------- #
def _text(payload: Any) -> list[TextContent]:
    if isinstance(payload, str):
        return [TextContent(type="text", text=payload)]
    return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]


def _do_ping(_args: dict) -> Any:
    from report_skill import __version__
    with ReportArchiveClient() as c:
        env = c.login()
    return {"status": "ok", "version": __version__,
            "logged_in_as": env.get("email"), "user_id": env.get("user_id")}


def _do_templates_list(args: dict) -> Any:
    cat = args.get("category")
    with ReportArchiveClient() as c:
        items = c.fetch_templates()
    if cat:
        cat_lc = cat.lower()
        items = [t for t in items if str(t.get("category", "")).lower() == cat_lc]
    return [{
        "template_id": t.get("template_id") or t.get("id"),
        "name": t.get("name"),
        "category": t.get("category"),
        "block_count": len((t.get("schema") or {}).get("blocks") or []),
        "widget_types": sorted({b.get("type") for b in (t.get("schema") or {}).get("blocks") or []
                                if isinstance(b, dict)}),
    } for t in items]


def _do_templates_show(args: dict) -> Any:
    with ReportArchiveClient() as c:
        return c.fetch_template(args["template_id"], args.get("version"))


def _do_templates_suggest(args: dict) -> Any:
    suggestions = template_suggest.suggest_templates(
        args["text"],
        top_k=int(args.get("top_k", 3)),
        use_llm=args.get("use_llm", "auto"),
    )
    return [{
        "template_id": s.template_id,
        "template_name": s.template_name,
        "score": s.score,
        "confidence": s.confidence,
        "matched_keywords": s.matched_keywords,
        "llm_reasoning": s.llm_reasoning,
    } for s in suggestions if s.template_id]


def _do_widgets_catalog(args: dict) -> Any:
    snap = schemas.load()
    wtype = args.get("widget_type")
    if wtype:
        w = snap["widgets"].get(wtype)
        if not w:
            raise ValueError(f"widget '{wtype}' not in cached catalog")
        return w
    return {
        "fetched_at": snap.get("fetched_at"),
        "schema_version": snap.get("schema_version"),
        "widget_count": len(snap.get("widgets") or {}),
        "widget_types": sorted((snap.get("widgets") or {}).keys()),
    }


def _do_widgets_suggest_extras(args: dict) -> Any:
    extras = widget_suggest.suggest_extras(
        args["text"],
        max_extras=int(args.get("max_extras", 5)),
        use_llm=args.get("use_llm", "auto"),
    )
    return [{
        "suggested_id": s.suggested_id,
        "widget_type": s.widget_type,
        "props": s.props,
        "input": s.input,
        "confidence": s.confidence,
        "matched_pattern": s.matched_pattern,
        "source": s.source,
    } for s in extras]


def _do_report_show(args: dict) -> Any:
    with ReportArchiveClient() as c:
        report = report_ops.fetch_report(c, int(args["report_id"]))
    page_index = args.get("page_index")
    summary: dict = {
        "id": report.get("id"),
        "title": report.get("title"),
        # v0.5.2 — H1: ReportRead exposes `phase` (drafting|reviewing|finalized),
        # not `status`. The old `report.get("status")` always returned None.
        "phase": report.get("phase"),
        "lifecycle": report.get("lifecycle"),
        "revision": report.get("revision"),
        "report_date": report.get("report_date"),
        "closed_at": report.get("closed_at"),
        "tags": report.get("tags"),
        "page_count": len(report.get("pages") or []),
        # v0.5.1 — surface author-lock so callers can decide whether a
        # write call would be rejected before issuing it.
        "author_lock_enabled": bool(report.get("author_lock_enabled")),
    }
    pages = []
    page_iter = (report.get("pages") or [])
    targets = [page_index] if page_index is not None else list(range(len(page_iter)))
    for i in targets:
        if 0 <= i < len(page_iter):
            p = page_iter[i]
            pages.append({
                "index": i,
                "template_id": p.get("template_id"),
                "name": p.get("name"),
                "content_block_ids": sorted((p.get("content") or {}).keys()),
                "extra_blocks": [{"id": eb.get("id"), "type": eb.get("type")}
                                 for eb in (p.get("extra_blocks") or []) if isinstance(eb, dict)],
            })
    summary["pages"] = pages
    return summary


def _do_report_lock_status(args: dict) -> Any:
    """Return the author-lock projection of a report.

    Read-only diagnostic. Service accounts cannot set the lock (owner-only
    on the RA side), so there is no companion `report_lock_set` tool.
    """
    rid = int(args["report_id"])
    with ReportArchiveClient() as c:
        info = c.fetch_report_lock_status(rid)
    return {
        "report_id": rid,
        "author_lock_enabled": bool(info.get("author_lock_enabled")),
        "author_lock_reason": info.get("author_lock_reason"),
        "author_lock_set_at": info.get("author_lock_set_at"),
    }


def _do_examples_status(_args: dict) -> Any:
    s = examples_mod.status()
    return {"ok": s.ok, "stale": s.stale, "missing": s.missing, "orphan": s.orphan,
            "counts": {"ok": len(s.ok), "stale": len(s.stale),
                       "missing": len(s.missing), "orphan": len(s.orphan)}}


def _do_tier_show(_args: dict) -> Any:
    profile = tier_mod.current_tier()
    policy = tier_mod.policy()
    return {
        "tier": profile.tier, "source": profile.source, "notes": profile.notes,
        "policy": {
            "blocks_per_call": policy.blocks_per_call,
            "max_repair_passes": policy.max_repair_passes,
            "enable_llm_retry": policy.enable_llm_retry,
            "max_llm_retries": policy.max_llm_retries,
            "aggressive_enum_match": policy.aggressive_enum_match,
            "fallback_chain_depth": policy.fallback_chain_depth,
        },
    }


def _do_tier_set(args: dict) -> Any:
    profile = tier_mod.set_tier(args["tier"], source="mcp", notes="set via MCP")
    return {"tier": profile.tier, "source": profile.source, "persisted": True}


def _do_catalog_sync(_args: dict) -> Any:
    snapshot, diff = catalog_mod.sync()
    return {
        "widget_count": len(snapshot.widgets),
        "added": diff.added,
        "removed": diff.removed,
        "modified": [{"type": t, "old_hash": oh, "new_hash": nh} for t, oh, nh in diff.modified],
        "unchanged_count": len(diff.unchanged),
    }


def _do_examples_mine_from_report(args: dict) -> Any:
    """Inline minimal version of cli_examples mine — same logic, no UI."""
    snapshot = schemas.load()
    widgets = snapshot.get("widgets", {}) or {}
    overwrite = bool(args.get("overwrite_stale", True))
    dry = bool(args.get("dry_run", False))
    with ReportArchiveClient() as c:
        report = c.get(f"/reports/{args['report_id']}")
        templates_cache: dict = {}
        candidates = []
        for page in report.get("pages") or []:
            key = (page["template_id"], page["template_version"])
            tpl = templates_cache.get(key)
            if tpl is None:
                try:
                    tpl = c.fetch_template(*key)
                except ApiError:
                    tpl = {"schema": {"blocks": []}}
                templates_cache[key] = tpl
            btypes = {b["id"]: b["type"] for b in tpl.get("schema", {}).get("blocks", [])
                      if isinstance(b, dict)}
            for eb in page.get("extra_blocks") or []:
                if isinstance(eb, dict) and eb.get("id"):
                    btypes[eb["id"]] = eb.get("type")
            for bid, ctn in (page.get("content") or {}).items():
                wtype = btypes.get(bid)
                if wtype and isinstance(ctn, dict):
                    candidates.append((wtype, ctn, bid))
    s = examples_mod.status()
    actions = []
    for wtype, content, bid in candidates:
        entry = widgets.get(wtype)
        if entry is None:
            actions.append({"widget": wtype, "block": bid, "action": "skipped (not in cache)"})
            continue
        if wtype in s.ok and not overwrite:
            actions.append({"widget": wtype, "block": bid, "action": "skipped (already ok)"})
            continue
        if dry:
            actions.append({"widget": wtype, "block": bid, "action": "would write"})
            continue
        current = examples_mod.load_example(wtype) or {}
        body = {"input": current.get("input"), "expected_content": content,
                "notes": f"mined from report {args['report_id']} (block={bid})"}
        examples_mod.write_example(wtype, body, entry["hash"])
        actions.append({"widget": wtype, "block": bid, "action": "wrote"})
    return {"actions": actions, "dry_run": dry}


# ---- write ops ------------------------------------------------------------- #
def _normalize_and_upload(c: ReportArchiveClient, tpl: dict,
                         blocks_input: dict, extras_input: list,
                         snapshot: dict) -> orchestrator.NormalizeResult:
    block_types = {b["id"]: b["type"]
                   for b in (tpl.get("schema") or {}).get("blocks") or []
                   if isinstance(b, dict)}
    blocks_input, extras_input, _ = upload_chain.preupload_for_draft(
        client=c, blocks_input=blocks_input, block_types=block_types,
        extras_input=extras_input,
    )
    return orchestrator.normalize_report(
        tpl, blocks_input, snapshot, extra_blocks_input=extras_input,
    )


def _do_report_create(args: dict) -> Any:
    snap = schemas.load()
    with ReportArchiveClient() as c:
        tpl = c.fetch_template(args["template_id"])
        result = _normalize_and_upload(c, tpl, args["blocks"],
                                       args.get("extra_blocks") or [], snap)
        if result.failed_count > 0 and not args.get("allow_failures"):
            return {"error": "block validation failed",
                    "failed": result.failed_count,
                    "blocks": [{"id": b.block_id, "type": b.widget_type,
                                "status": b.status, "detail": b.detail}
                               for b in result.blocks if b.status == "failed"]}
        derived_tags = args.get("tags") or tags_mod.infer_tags(
            title=args["title"], body_text="", max_tags=5,
        )
        # v0.5.1 — forward the 13 optional related-info + page-level fields.
        extra_kwargs = {k: args[k] for k in _REPORT_PASS_THROUGH if k in args}
        payload = report_builder.build_create_payload(
            tpl, result.content,
            title=args["title"],
            report_date=args.get("report_date"),
            phase=args.get("phase"),
            lifecycle=args.get("lifecycle"),
            status=args.get("status"),  # legacy → mapped to phase by builder
            tags=derived_tags,
            extra_blocks=result.extra_blocks,
            **extra_kwargs,
        )
        created = c.create_report(payload)
        # Optional auto-mount to org boards
        mount_results = None
        if args.get("mount_to") and created.get("id") is not None:
            mount_results = report_ops.mount_report(
                c, int(created["id"]),
                workspace_slugs=list(args["mount_to"]),
            )
    out = {
        "id": created.get("id"), "title": created.get("title"),
        "revision": created.get("revision"),
        "view_url": f"http://localhost:3001/reports/{created.get('id')}",
        "blocks": [{"id": b.block_id, "status": b.status} for b in result.blocks],
    }
    if mount_results is not None:
        out["mounts"] = mount_results
        out["mount_count"] = len(mount_results)
    return out


def _do_report_update(args: dict) -> Any:
    snap = schemas.load()
    rid = int(args["report_id"])
    page_index = int(args.get("page_index", 0))
    with ReportArchiveClient() as c:
        existing = report_ops.fetch_report(c, rid)
        pages = existing.get("pages") or []
        if page_index >= len(pages):
            raise IndexError(f"page_index {page_index} out of range ({len(pages)} pages)")
        page = pages[page_index]
        tpl = c.fetch_template(page["template_id"], page["template_version"])
        tpl_blocks = (tpl.get("schema") or {}).get("blocks") or []
        tpl_ids = {b.get("id") for b in tpl_blocks if isinstance(b, dict)}
        existing_extras = page.get("extra_blocks") or []
        extras_by_id = {b.get("id"): b for b in existing_extras if isinstance(b, dict)}
        # Synthesize extras for any draft.blocks key that matches an existing extra.
        synth_extras: list[dict] = list(args.get("extra_blocks") or [])
        draft_blocks = dict(args.get("blocks") or {})
        for bid in list(draft_blocks):
            if bid in tpl_ids:
                continue
            if bid in extras_by_id:
                eb = extras_by_id[bid]
                synth_extras.append({"id": bid, "type": eb.get("type"),
                                     "props": eb.get("props", {}),
                                     "input": draft_blocks.pop(bid)})
        result = _normalize_and_upload(c, tpl, draft_blocks, synth_extras, snap)
        # v0.5.2 — A6: mirror _do_report_create's failed_count gate so a
        # partial patch with failed blocks doesn't ship to the server unless
        # the caller explicitly opts in via allow_failures=true.
        if result.failed_count > 0 and not args.get("allow_failures"):
            return {"error": "block validation failed",
                    "failed": result.failed_count,
                    "blocks": [{"id": b.block_id, "type": b.widget_type,
                                "status": b.status, "detail": b.detail}
                               for b in result.blocks if b.status == "failed"]}
        existing_extra_ids = {b.get("id") for b in existing_extras if isinstance(b, dict)}
        new_extras = [e for e in result.extra_blocks if e.get("id") not in existing_extra_ids]
        # v0.5.1 — forward the 13 optional related-info + page-level fields.
        extra_kwargs = {k: args[k] for k in _REPORT_PASS_THROUGH if k in args}
        updated = report_ops.update_blocks(
            c, rid,
            page_index=page_index,
            block_patches=result.content,
            add_extra_blocks=new_extras,
            # CR-11 — explicit override; when None, update_blocks auto-merges
            # new extras into the page's existing blocks_order.
            blocks_order=args.get("blocks_order"),
            title=args.get("title"),
            phase=args.get("phase"),
            lifecycle=args.get("lifecycle"),
            status=args.get("status"),
            tags=args.get("tags"),
            **extra_kwargs,
        )
    return {"id": updated.get("id"), "revision": updated.get("revision"),
            "view_url": f"http://localhost:3001/reports/{updated.get('id')}",
            "blocks": [{"id": b.block_id, "status": b.status} for b in result.blocks]}


def _do_report_append(args: dict) -> Any:
    with ReportArchiveClient() as c:
        updated = report_ops.append_to_blocks(
            c, int(args["report_id"]),
            block_appends=args["blocks"],
            page_index=int(args.get("page_index", 0)),
            max_retries=int(args.get("max_retries", 3)),
        )
    return {"id": updated.get("id"), "revision": updated.get("revision"),
            "view_url": f"http://localhost:3001/reports/{updated.get('id')}"}


def _do_report_add_page(args: dict) -> Any:
    snap = schemas.load()
    rid = int(args["report_id"])
    with ReportArchiveClient() as c:
        tpl = c.fetch_template(args["template_id"])
        result = _normalize_and_upload(c, tpl, args["blocks"],
                                       args.get("extra_blocks") or [], snap)
        # v0.5.2 — A6: mirror _do_report_create's failed_count gate so the
        # new page isn't appended with broken blocks unless explicitly allowed.
        if result.failed_count > 0 and not args.get("allow_failures"):
            return {"error": "block validation failed",
                    "failed": result.failed_count,
                    "blocks": [{"id": b.block_id, "type": b.widget_type,
                                "status": b.status, "detail": b.detail}
                               for b in result.blocks if b.status == "failed"]}
        updated = report_ops.add_page(
            c, rid,
            template_id=tpl["template_id"],
            template_version=tpl["version"],
            name=args.get("name"),
            content=result.content,
            extra_blocks=result.extra_blocks,
            # CR-11 — pass the fetched template so add_page can auto-compute
            # blocks_order, and any explicit override from the caller.
            template=tpl,
            blocks_order=args.get("blocks_order"),
        )
    return {"id": updated.get("id"), "pages": len(updated.get("pages") or []),
            "view_url": f"http://localhost:3001/reports/{updated.get('id')}"}


def _do_report_delete(args: dict) -> Any:
    if not args.get("confirm"):
        raise ValueError("confirm must be true to actually delete")
    with ReportArchiveClient() as c:
        report_ops.delete_report(c, int(args["report_id"]))
    return {"deleted": True, "id": int(args["report_id"])}


def _do_report_mount(args: dict) -> Any:
    rid = int(args["report_id"])
    with ReportArchiveClient() as c:
        created = report_ops.mount_report(
            c, rid,
            workspace_slugs=list(args["workspace_slugs"]),
            edit_policy=args.get("edit_policy", "default"),
            note=args.get("note", ""),
            folder_id=args.get("folder_id"),
        )
    return {
        "report_id": rid,
        "new_mounts": created,
        "new_mount_count": len(created),
        "note": "Already-mounted boards are silently skipped — new_mounts may be empty.",
    }


def _do_report_unmount(args: dict) -> Any:
    with ReportArchiveClient() as c:
        report_ops.unmount_report(c, int(args["report_id"]), args["workspace_slug"])
    return {"unmounted": True, "report_id": int(args["report_id"]),
            "workspace_slug": args["workspace_slug"]}


def _do_report_mounts(args: dict) -> Any:
    with ReportArchiveClient() as c:
        mounts = report_ops.list_mounts(c, int(args["report_id"]))
    return {"report_id": int(args["report_id"]), "mounts": mounts, "count": len(mounts)}


def _do_report_milestone_add(args: dict) -> Any:
    rid = int(args["report_id"])
    item: dict = {"date": args["date"], "label": args["label"]}
    if args.get("status"):
        item["status"] = args["status"]
    if args.get("note"):
        item["note"] = args["note"]
    page_index = int(args.get("page_index", 0))
    with ReportArchiveClient() as c:
        target_bid = args.get("block_id")
        if not target_bid:
            report = report_ops.fetch_report(c, rid)
            target_bid = _find_milestone_block(report, page_index, c)
            if not target_bid:
                raise ValueError(f"no milestone block on page {page_index}; pass block_id")
        updated = report_ops.append_to_blocks(
            c, rid,
            block_appends={target_bid: {"items": [item]}},
            page_index=page_index,
        )
    items = ((updated.get("pages") or [{}])[page_index].get("content") or {})\
        .get(target_bid, {}).get("items") or []
    return {"block_id": target_bid, "items_count": len(items),
            "revision": updated.get("revision")}


def _do_report_milestone_remove(args: dict) -> Any:
    rid = int(args["report_id"])
    page_index = int(args.get("page_index", 0))
    match: dict = {"date": args["date"]}
    if args.get("label"):
        match["label"] = args["label"]
    with ReportArchiveClient() as c:
        target_bid = args.get("block_id")
        if not target_bid:
            report = report_ops.fetch_report(c, rid)
            target_bid = _find_milestone_block(report, page_index, c)
            if not target_bid:
                raise ValueError(f"no milestone block on page {page_index}")
        updated, n = report_ops.remove_items(
            c, rid, block_id=target_bid, page_index=page_index, match=match,
        )
    return {"block_id": target_bid, "removed": n, "revision": updated.get("revision")}


def _find_milestone_block(report: dict, page_index: int, c: ReportArchiveClient) -> str | None:
    pages = report.get("pages") or []
    if page_index >= len(pages):
        return None
    page = pages[page_index]
    for eb in (page.get("extra_blocks") or []):
        if isinstance(eb, dict) and eb.get("type") == "milestone" and eb.get("id"):
            return eb["id"]
    try:
        tpl = c.fetch_template(page["template_id"], page["template_version"])
    except ApiError:
        return None
    for b in (tpl.get("schema") or {}).get("blocks") or []:
        if isinstance(b, dict) and b.get("type") == "milestone":
            return b.get("id")
    return None


def _do_file_upload(args: dict) -> Any:
    with ReportArchiveClient() as c:
        meta = c.upload_file(args["path"])
    return {
        "file_id": meta.get("file_id") or meta.get("id"),
        "filename": meta.get("filename"),
        "mime_type": meta.get("mime_type"),
        "size": meta.get("size"),
    }


# ---- offline export / import ---------------------------------------------- #
def _do_catalog_sync_templates(_args: dict) -> Any:
    with ReportArchiveClient() as c:
        cached, errors = catalog_mod.sync_templates(c)
    return {"cached": cached, "errors": errors,
            "cache_dir": str(catalog_mod.TEMPLATES_CACHE_DIR)}


def _do_report_export(args: dict) -> Any:
    """Normalize a draft + write the ReportCreate payload to disk."""
    from pathlib import Path as _Path
    snap = schemas.load()
    offline = bool(args.get("offline", False))
    out_path = _Path(args["out_path"])
    with ReportArchiveClient() as c:
        tpl = c.fetch_template(args["template_id"], allow_cache=offline)
        block_types = {b["id"]: b["type"]
                       for b in (tpl.get("schema") or {}).get("blocks") or []
                       if isinstance(b, dict)}
        blocks_input = args.get("blocks", {})
        extras_input = args.get("extra_blocks") or []
        if not offline:
            blocks_input, extras_input, _ = upload_chain.preupload_for_draft(
                client=c, blocks_input=blocks_input, block_types=block_types,
                extras_input=extras_input,
            )
        result = orchestrator.normalize_report(
            tpl, blocks_input, snap, extra_blocks_input=extras_input,
        )
    if result.failed_count > 0 and not args.get("allow_failures"):
        return {"error": "block validation failed",
                "failed": result.failed_count,
                "blocks": [{"id": b.block_id, "status": b.status, "detail": b.detail}
                           for b in result.blocks if b.status == "failed"]}
    payload = report_builder.build_create_payload(
        tpl, result.content,
        title=args["title"],
        report_date=args.get("report_date"),
        tags=args.get("tags"),
        extra_blocks=result.extra_blocks,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "exported": True,
        "out_path": str(out_path),
        "block_count": len(result.content),
        "extra_count": len(result.extra_blocks),
        "size_bytes": out_path.stat().st_size,
        "note": f"later, call report_import with payload_path={out_path}",
    }


def _do_report_revise(args: dict) -> Any:
    """LLM-driven block-level revision of an existing report (CR-1 protected)."""
    from report_skill import examples as examples_mod
    from report_skill import llm as llm_mod
    from report_skill import prompt as prompt_mod
    from report_skill.llm import LLMError
    from report_skill.adapters.base import NormalizeError

    if not llm_mod.is_configured():
        raise RuntimeError(
            "no LLM provider configured (set ANTHROPIC_API_KEY / OPENAI_API_KEY / "
            "OLLAMA_BASE_URL / SKILL_LLM_PROVIDER=bridge)"
        )

    rid = int(args["report_id"])
    instruction = args["instruction"]
    block_ids = list(args.get("block_ids") or [])
    revise_all = bool(args.get("revise_all", False))
    page_index = int(args.get("page_index", 0))
    dry_run = bool(args.get("dry_run", False))
    max_tokens = int(args.get("max_tokens", 800))

    if not block_ids and not revise_all:
        raise ValueError("either block_ids or revise_all is required")

    snap = schemas.load()
    with ReportArchiveClient() as c:
        existing = report_ops.fetch_report(c, rid)
        pages = existing.get("pages") or []
        if page_index >= len(pages):
            raise IndexError(f"page_index {page_index} out of range ({len(pages)} pages)")
        page = pages[page_index]
        tpl = c.fetch_template(page["template_id"], page["template_version"])
        tpl_blocks = (tpl.get("schema") or {}).get("blocks") or []
        tpl_by_id = {b["id"]: b for b in tpl_blocks if isinstance(b, dict)}
        current_content = page.get("content") or {}

        if revise_all:
            block_ids = [bid for bid in tpl_by_id
                         if bid in current_content and current_content[bid]]
        unknown = [bid for bid in block_ids if bid not in tpl_by_id]
        if unknown:
            raise ValueError(f"unknown block id(s) on page {page_index}: {unknown}")

        provider = llm_mod.get_provider()
        patch: dict = {}
        results = []
        for bid in block_ids:
            bdef = tpl_by_id[bid]
            wtype = bdef["type"]
            schema = schemas.content_schema(snap, wtype)
            props = schemas.resolved_props(bdef, snap)
            example = examples_mod.load_example(wtype)
            cur = current_content.get(bid) or {}

            spec = prompt_mod.BlockSpec(
                block_id=bid, widget_type=wtype, props=props, content_schema=schema,
            )
            messages = prompt_mod.build_block_revise_prompt(
                spec, current_content=cur, revision_instruction=instruction,
                example=example.get("expected_content") if example else None,
            )
            try:
                reply = provider.generate(messages, max_tokens=max_tokens, json_mode=True)
                parsed = llm_mod.extract_json(reply)
            except LLMError as e:
                results.append({"block_id": bid, "status": "llm_error", "detail": str(e)})
                continue
            if parsed is None:
                results.append({"block_id": bid, "status": "no_json"})
                continue
            adapter = orchestrator.ADAPTERS.get(wtype)
            if adapter is None:
                results.append({"block_id": bid, "status": "no_adapter", "widget": wtype})
                continue
            try:
                normalized = adapter.normalize(parsed, props)
            except (NormalizeError, Exception) as e:
                results.append({"block_id": bid, "status": "validation_failed", "detail": str(e)})
                continue
            if normalized == cur:
                results.append({"block_id": bid, "status": "unchanged"})
                continue
            patch[bid] = normalized
            results.append({"block_id": bid, "status": "revised"})

        if not patch:
            return {"id": rid, "patched": [], "results": results, "note": "no blocks changed"}

        if dry_run:
            return {"id": rid, "dry_run": True, "patch": patch, "results": results}

        updated = report_ops.update_blocks(
            c, rid, page_index=page_index, block_patches=patch,
        )
    return {
        "id": updated.get("id"),
        "title": updated.get("title"),
        "revision": updated.get("revision"),
        "patched_blocks": list(patch.keys()),
        "results": results,
        "view_url": f"http://localhost:3001/reports/{updated.get('id')}",
    }


def _do_report_import(args: dict) -> Any:
    """Import a previously-saved payload OR bundle.zip (auto-detected)."""
    from pathlib import Path as _Path
    p = _Path(args["payload_path"])
    if not p.is_file():
        raise FileNotFoundError(f"payload not found: {p}")
    if p.suffix.lower() == ".zip":
        from report_skill import bundle as bundle_mod
        with ReportArchiveClient() as c:
            created = bundle_mod.import_bundle(c, p)
        return {
            "id": created.get("id"),
            "title": created.get("title"),
            "revision": created.get("revision"),
            "view_url": f"http://localhost:3001/reports/{created.get('id')}",
            "mode": "bundle",
        }
    payload = json.loads(p.read_text(encoding="utf-8"))
    with ReportArchiveClient() as c:
        created = c.create_report(payload)
    return {
        "id": created.get("id"),
        "title": created.get("title"),
        "revision": created.get("revision"),
        "view_url": f"http://localhost:3001/reports/{created.get('id')}",
        "mode": "json",
    }


def _do_report_dump(args: dict) -> Any:
    """Pack a report + referenced files into a bundle.zip."""
    from pathlib import Path as _Path
    from report_skill import bundle as bundle_mod
    rid = int(args["report_id"])
    out = _Path(args.get("out_path") or f"bundle-report-{rid}.zip")
    with ReportArchiveClient() as c:
        summary = bundle_mod.pack_report_bundle(c, rid, out)
    return {**summary, "bundle_path": str(out)}


# ---- mention resolvers ------------------------------------------------ #
import unicodedata as _unicodedata


def _nfkc_lower(s: str) -> str:
    """NFKC-normalize + casefold for substring matching that's stable
    across half/full-width digits and Korean composed/decomposed forms."""
    return _unicodedata.normalize("NFKC", s or "").casefold()


def _do_reports_search(args: dict) -> Any:
    """Resolve free-text → report_id candidates for mention://report/<id>."""
    q = _nfkc_lower(args.get("q") or "")
    ws_exact = args.get("workspace_slug")
    owner_sub = _nfkc_lower(args.get("owner_name") or "")
    mount_exact = args.get("mount_slug")
    date_from = args.get("date_from")
    date_to = args.get("date_to")
    limit = max(1, min(int(args.get("limit") or 20), 50))

    with ReportArchiveClient() as c:
        pool = c.fetch_linkable_reports()

    def row_haystacks(r: dict) -> tuple[str, str, list[str]]:
        title = r.get("title") or ""
        owner = r.get("owner_name") or ""
        mounts = [(m.get("name") or "") for m in (r.get("mount_workspaces") or [])
                  if isinstance(m, dict)]
        return title, owner, mounts

    def passes_filters(r: dict) -> bool:
        if ws_exact and (r.get("workspace_slug") or "") != ws_exact:
            return False
        if mount_exact:
            mounts_slugs = {(m.get("slug") or "") for m in (r.get("mount_workspaces") or [])
                            if isinstance(m, dict)}
            if mount_exact not in mounts_slugs:
                return False
        if owner_sub:
            if owner_sub not in _nfkc_lower(r.get("owner_name") or ""):
                return False
        if date_from and (r.get("report_date") or "") < date_from:
            return False
        if date_to and (r.get("report_date") or "") > date_to:
            return False
        return True

    scored: list[tuple[int, str, dict]] = []
    for r in pool:
        if not passes_filters(r):
            continue
        title, _owner, mounts = row_haystacks(r)
        title_n = _nfkc_lower(title)
        rank = 99  # default = recency only
        if q:
            if title_n == q:
                rank = 0
            elif q in title_n:
                rank = 1
            elif q in _nfkc_lower(r.get("owner_name") or ""):
                rank = 2
            elif any(q in _nfkc_lower(m) for m in mounts):
                rank = 3
            else:
                continue
        scored.append((rank, r.get("report_date") or "", r))

    # Sort: rank ascending, then recency desc (later dates first).
    scored.sort(key=lambda t: (t[0], _neg_date(t[1])))
    out: list[dict] = []
    for _rank, _dt, r in scored[:limit]:
        out.append({
            "report_id": r.get("id"),
            "title": r.get("title"),
            "owner_name": r.get("owner_name"),
            "report_date": r.get("report_date"),
            "workspace_slug": r.get("workspace_slug"),
            "mount_workspace_names": [m.get("name") for m in (r.get("mount_workspaces") or [])
                                      if isinstance(m, dict) and m.get("name")],
            "phase": r.get("phase"),
            "report_type_name": r.get("report_type_name"),
        })
    return out


def _neg_date(s: str) -> str:
    """Sort helper — invert lexical date ordering so recency sorts ahead."""
    # Easiest stable way: invert each char. The exact ordering doesn't
    # matter for ties; we just want later dates earlier.
    return "".join(chr(255 - ord(c)) for c in s) if s else ""


def _do_workspaces_list(args: dict) -> Any:
    """List workspaces filtered by kind + free-text substring."""
    kind = args.get("kind") or "org"
    q = _nfkc_lower(args.get("q") or "")
    with ReportArchiveClient() as c:
        rows = c.fetch_workspaces()
    out: list[dict] = []
    for w in rows:
        if not isinstance(w, dict):
            continue
        wkind = w.get("kind") or "org"
        if kind != "all" and wkind != kind:
            continue
        if q:
            haystack = _nfkc_lower((w.get("name") or "") + " " + (w.get("slug") or ""))
            if q not in haystack:
                continue
        out.append({
            "slug": w.get("slug"),
            "name": w.get("name"),
            "kind": wkind,
            "parent_slug": w.get("parent_slug"),
        })
    return out


_ENTITY_TYPES_CACHE: list[dict] | None = None


def _do_entity_types_list(_args: dict) -> Any:
    """Return the entity-axis catalog; cached after first hit."""
    global _ENTITY_TYPES_CACHE
    if _ENTITY_TYPES_CACHE is None:
        with ReportArchiveClient() as c:
            _ENTITY_TYPES_CACHE = c.fetch_entity_types()
    return [{
        "id": t.get("id"),
        "slug": t.get("slug"),
        "label": t.get("label"),
        "icon": t.get("icon"),
        "multi": t.get("multi", False),
        "sort_order": t.get("sort_order", 0),
        "description": t.get("description"),
    } for t in (_ENTITY_TYPES_CACHE or []) if isinstance(t, dict)]


def _do_entities_list(args: dict) -> Any:
    """Resolve free-text → entity_id candidates for mention://entity/<id>."""
    axis = args.get("axis")
    type_id = args.get("type_id")
    q = args.get("q")
    include_deprecated = bool(args.get("include_deprecated", False))
    limit = max(1, min(int(args.get("limit") or 50), 200))

    # If axis given and no type_id, resolve via cached entity_types.
    if type_id is None and axis:
        types = _do_entity_types_list({})
        match = next((t for t in types if t.get("slug") == axis), None)
        if match is None:
            return []
        type_id = match.get("id")
    if type_id is None:
        # Search across all axes — backend supports omitted type_id.
        type_id_arg = None
    else:
        type_id_arg = int(type_id)

    with ReportArchiveClient() as c:
        rows = c.fetch_entities(
            type_id=type_id_arg, q=q,
            include_deprecated=include_deprecated, limit=limit,
        )
    # Decorate with axis_slug so the LLM has everything for the
    # mention://entity/<id>?axis=<slug> link.
    types_by_id = {t.get("id"): t for t in _do_entity_types_list({})}
    out: list[dict] = []
    for e in rows:
        if not isinstance(e, dict):
            continue
        tid = e.get("type_id")
        axis_slug = (types_by_id.get(tid) or {}).get("slug")
        out.append({
            "entity_id": e.get("id"),
            "value": e.get("value"),
            "code": e.get("code"),
            "axis_slug": axis_slug,
            "type_id": tid,
            "status": e.get("status") or "active",
        })
    return out


# ---- v0.5.0 dispatchers ---------------------------------------------- #
def _do_report_copy(args: dict) -> Any:
    rid = int(args["report_id"])
    title = str(args["title"])
    mode = args.get("mode") or "full"
    folder_id = args.get("folder_id")
    with ReportArchiveClient() as c:
        created = c.copy_report(
            rid, title=title, mode=mode,
            folder_id=int(folder_id) if folder_id is not None else None,
        )
    return {
        "id": created.get("id"),
        "title": created.get("title"),
        "workspace_slug": created.get("workspace_slug"),
        "revision": created.get("revision"),
        "view_url": f"http://localhost:3001/reports/{created.get('id')}",
    }


def _do_report_add_link(args: dict) -> Any:
    rid = int(args["report_id"])
    to_rid = int(args["to_report_id"])
    kind = args.get("kind") or "related"
    label = args.get("label")
    # v0.5.1 — 'outgoing' (default) or 'incoming'; server swaps from/to
    # when 'incoming' so the link points the other way.
    direction = args.get("direction") or "outgoing"
    with ReportArchiveClient() as c:
        link = c.add_report_link(
            rid, to_report_id=to_rid, kind=kind, label=label, direction=direction,
        )
    return link


def _do_report_types_list(_args: dict) -> Any:
    with ReportArchiveClient() as c:
        rows = c.fetch_report_types()
    return [{
        "id": t.get("id"),
        "name": t.get("name"),
        "description": t.get("description"),
        "status": t.get("status"),
    } for t in (rows or []) if isinstance(t, dict)]


def _do_report_publish(args: dict) -> Any:
    rid = int(args["report_id"])
    with ReportArchiveClient() as c:
        report = c.publish_report(rid)
    return {
        "id": report.get("id"),
        "title": report.get("title"),
        "phase": report.get("phase"),
        "revision": report.get("revision"),
        "view_url": f"http://localhost:3001/reports/{report.get('id')}",
    }


def _do_report_unpublish(args: dict) -> Any:
    rid = int(args["report_id"])
    with ReportArchiveClient() as c:
        report = c.unpublish_report(rid)
    return {
        "id": report.get("id"),
        "title": report.get("title"),
        "phase": report.get("phase"),
        "revision": report.get("revision"),
        "view_url": f"http://localhost:3001/reports/{report.get('id')}",
    }


def _do_folders_list(args: dict) -> Any:
    # v0.5.1 — workspace_slug is optional. Omit to fetch the caller's own
    # personal folders (matches RA routes.py:104-106).
    slug_raw = args.get("workspace_slug")
    slug = str(slug_raw) if slug_raw is not None else None
    with ReportArchiveClient() as c:
        rows = c.list_folders(slug) if slug is not None else c.list_folders()
    return [{
        "id": f.get("id"),
        "name": f.get("name"),
        "kind": f.get("kind"),
        "parent_id": f.get("parent_id"),
        "workspace_slug": f.get("workspace_slug"),
        "sort_order": f.get("sort_order"),
        "report_count": f.get("report_count"),
    } for f in (rows or []) if isinstance(f, dict)]


def _do_report_mount_set_folder(args: dict) -> Any:
    rid = int(args["report_id"])
    slug = str(args["workspace_slug"])
    folder_id_raw = args.get("folder_id")
    folder_id = int(folder_id_raw) if folder_id_raw is not None else None
    with ReportArchiveClient() as c:
        result = c.set_mount_folder(rid, slug, folder_id=folder_id)
    return result


def _do_report_mount_set_edit_policy(args: dict) -> Any:
    rid = int(args["report_id"])
    slug = str(args["workspace_slug"])
    policy = str(args["edit_policy"])
    with ReportArchiveClient() as c:
        result = c.set_mount_edit_policy(rid, slug, edit_policy=policy)
    return result


def _do_template_set_scope(args: dict) -> Any:
    # v0.5.1 — template_id is a SLUG (e.g. 'engineering-rca'), not an int.
    # RA backend TemplateScopeUpdate uses string pattern ^[a-z0-9][a-z0-9-]*$.
    tid = str(args["template_id"])
    slugs = args.get("owner_workspace_slugs")
    if slugs is not None:
        slugs = [str(s) for s in slugs]
    with ReportArchiveClient() as c:
        result = c.set_template_scope(tid, owner_workspace_slugs=slugs)
    return result


def _do_presets_list(args: dict) -> Any:
    template_id = args.get("template_id")
    with ReportArchiveClient() as c:
        rows = c.list_presets(template_id=template_id)
    return rows


def _do_preset_create(args: dict) -> Any:
    rid = int(args["report_id"])
    name = str(args["name"])
    slugs = args.get("owner_workspace_slugs")
    if slugs is not None:
        slugs = [str(s) for s in slugs]
    # v0.5.2 — D1: forward optional description (omit when None so older
    # client.create_preset signatures without the kwarg don't break).
    description = args.get("description")
    with ReportArchiveClient() as c:
        if description is not None:
            preset = c.create_preset(rid, name=name,
                                     owner_workspace_slugs=slugs,
                                     description=str(description))
        else:
            preset = c.create_preset(rid, name=name, owner_workspace_slugs=slugs)
    return preset


def _do_report_new_from_preset(args: dict) -> Any:
    pid = int(args["preset_id"])
    title = args.get("title")
    folder_id_raw = args.get("folder_id")
    folder_id = int(folder_id_raw) if folder_id_raw is not None else None
    with ReportArchiveClient() as c:
        result = c.new_report_from_preset(pid, title=title, folder_id=folder_id)
    new_id = result.get("id")
    return {
        "id": new_id,
        "workspace_slug": result.get("workspace_slug"),
        "view_url": f"http://localhost:3001/reports/{new_id}" if new_id is not None else None,
    }


def _do_preset_delete(args: dict) -> Any:
    pid = int(args["preset_id"])
    with ReportArchiveClient() as c:
        c.delete_preset(pid)
    return {"deleted": True, "id": pid}


def _do_composite_get(args: dict) -> Any:
    cid = int(args["composite_id"])
    with ReportArchiveClient() as c:
        return c.get_composite(cid)


def _do_composite_summary_set(args: dict) -> Any:
    cid = int(args["composite_id"])
    widgets = args["summary_widgets"]
    if not isinstance(widgets, list):
        raise ValueError("summary_widgets must be a list")
    expected_revision = args.get("expected_revision")
    if expected_revision is not None:
        expected_revision = int(expected_revision)
    with ReportArchiveClient() as c:
        updated = c.update_composite_summary(
            cid, summary_widgets=widgets, expected_revision=expected_revision,
        )
    return {
        "id": updated.get("id"),
        "title": updated.get("title"),
        "revision": updated.get("revision"),
        "summary_widget_count": len(updated.get("summary_widgets") or []),
    }


def _do_composites_submittable_for(args: dict) -> Any:
    rid = int(args["report_id"])
    with ReportArchiveClient() as c:
        return c.list_submittable_composites(rid)


def _do_composites_requests_list(args: dict) -> Any:
    cid = int(args["composite_id"])
    status_filter = args.get("status_filter")
    with ReportArchiveClient() as c:
        return c.list_composite_requests(cid, status_filter=status_filter) \
            if status_filter is not None else c.list_composite_requests(cid)


def _do_composites_submit(args: dict) -> Any:
    cid = int(args["composite_id"])
    rid = int(args["report_id"])
    note = args.get("note")
    with ReportArchiveClient() as c:
        return c.submit_to_composite(cid, report_id=rid, note=note)


def _do_composites_request_accept(args: dict) -> Any:
    cid = int(args["composite_id"])
    req_id = int(args["request_id"])
    with ReportArchiveClient() as c:
        return c.accept_composite_request(cid, req_id)


def _do_composites_request_reject(args: dict) -> Any:
    cid = int(args["composite_id"])
    req_id = int(args["request_id"])
    reason = args.get("reason")
    with ReportArchiveClient() as c:
        return c.reject_composite_request(cid, req_id, reason=reason)


def _do_composites_request_withdraw(args: dict) -> Any:
    cid = int(args["composite_id"])
    req_id = int(args["request_id"])
    with ReportArchiveClient() as c:
        return c.withdraw_composite_request(cid, req_id)


# --------------------------------------------------------------------------- #
# v0.6.0 — composites body editing dispatchers
# --------------------------------------------------------------------------- #
def _do_composite_create(args: dict) -> Any:
    title = args["title"]
    kind = args["kind"]
    kwargs: dict[str, Any] = {
        "title": title,
        "kind": kind,
        "view_mode": args.get("view_mode", "single"),
    }
    if "period_date" in args:
        kwargs["period_date"] = args.get("period_date")
    if "workspace_slug" in args:
        kwargs["workspace_slug"] = args.get("workspace_slug")
    if "description" in args:
        kwargs["description"] = args.get("description") or ""
    if "two_col_view" in args:
        kwargs["two_col_view"] = bool(args.get("two_col_view"))
    if "summary_widgets" in args:
        kwargs["summary_widgets"] = list(args.get("summary_widgets") or [])
    if "items" in args:
        kwargs["items"] = list(args.get("items") or [])
    with ReportArchiveClient() as c:
        created = c.create_composite(**kwargs)
    if isinstance(created, dict):
        return {
            "id": created.get("id"),
            "title": created.get("title"),
            "kind": created.get("kind"),
            "workspace_slug": created.get("workspace_slug"),
            "view_mode": created.get("view_mode"),
            "revision": created.get("revision"),
            "item_count": len(created.get("items") or []),
        }
    return created


def _do_composite_update(args: dict) -> Any:
    cid = int(args["composite_id"])
    kwargs: dict[str, Any] = {}
    if "title" in args:
        kwargs["title"] = args.get("title")
    if "view_mode" in args:
        kwargs["view_mode"] = args.get("view_mode")
    if "description" in args:
        kwargs["description"] = args.get("description")
    if "two_col_view" in args:
        kwargs["two_col_view"] = bool(args.get("two_col_view"))
    if "summary_widgets" in args:
        kwargs["summary_widgets"] = list(args.get("summary_widgets") or [])
    if "items" in args:
        kwargs["items"] = list(args.get("items") or [])
    # Tri-state: explicit null clears, omission leaves alone.
    if "period_date" in args:
        kwargs["period_date"] = args.get("period_date")
    if "expected_revision" in args and args.get("expected_revision") is not None:
        kwargs["expected_revision"] = int(args["expected_revision"])
    with ReportArchiveClient() as c:
        updated = c.update_composite(cid, **kwargs)
    if isinstance(updated, dict):
        return {
            "id": updated.get("id"),
            "title": updated.get("title"),
            "revision": updated.get("revision"),
            "view_mode": updated.get("view_mode"),
            "item_count": len(updated.get("items") or []),
        }
    return updated


def _do_composite_items_set(args: dict) -> Any:
    cid = int(args["composite_id"])
    items = args["items"]
    if not isinstance(items, list):
        raise ValueError("items must be a list")
    expected_revision = args.get("expected_revision")
    if expected_revision is not None:
        expected_revision = int(expected_revision)
    with ReportArchiveClient() as c:
        updated = c.update_composite(
            cid,
            items=items,
            expected_revision=expected_revision,
        )
    if isinstance(updated, dict):
        return {
            "id": updated.get("id"),
            "title": updated.get("title"),
            "revision": updated.get("revision"),
            "item_count": len(updated.get("items") or []),
        }
    return updated


def _do_composite_delete(args: dict) -> Any:
    cid = int(args["composite_id"])
    with ReportArchiveClient() as c:
        c.delete_composite(cid)
    return {"deleted": True, "id": cid}


def _do_composite_publish(args: dict) -> Any:
    cid = int(args["composite_id"])
    with ReportArchiveClient() as c:
        published = c.publish_composite(cid)
    if isinstance(published, dict):
        return {
            "id": published.get("id"),
            "title": published.get("title"),
            "published_at": published.get("published_at"),
            "revision": published.get("revision"),
        }
    return published


def _do_composite_unpublish(args: dict) -> Any:
    cid = int(args["composite_id"])
    with ReportArchiveClient() as c:
        unpublished = c.unpublish_composite(cid)
    if isinstance(unpublished, dict):
        return {
            "id": unpublished.get("id"),
            "title": unpublished.get("title"),
            "published_at": unpublished.get("published_at"),
            "revision": unpublished.get("revision"),
        }
    return unpublished


# --------------------------------------------------------------------------- #
# v0.6.0 — activities + notifications dispatchers
# --------------------------------------------------------------------------- #
def _do_report_activities(args: dict) -> Any:
    rid = int(args["report_id"])
    limit = int(args.get("limit", 20))
    before_id = args.get("before_id")
    if before_id is not None:
        before_id = int(before_id)
    with ReportArchiveClient() as c:
        body = c.fetch_report_activities(rid, limit=limit, before_id=before_id)
    items = body.get("items") if isinstance(body, dict) else []
    return {
        "items": items or [],
        "count": len(items or []),
    }


def _do_notifications_list(args: dict) -> Any:
    unread_only = bool(args.get("unread_only", False))
    limit = int(args.get("limit", 50))
    before_id = args.get("before_id")
    if before_id is not None:
        before_id = int(before_id)
    with ReportArchiveClient() as c:
        body = c.list_notifications(
            unread_only=unread_only, limit=limit, before_id=before_id,
        )
    items = body.get("items") if isinstance(body, dict) else []
    unread_count = body.get("unread_count") if isinstance(body, dict) else 0
    return {
        "items": items or [],
        "count": len(items or []),
        "unread_count": int(unread_count or 0),
    }


def _do_notifications_unread_count(_args: dict) -> Any:
    with ReportArchiveClient() as c:
        n = c.unread_notification_count()
    return {"unread_count": int(n)}


def _do_notification_mark_read(args: dict) -> Any:
    nid = int(args["notification_id"])
    with ReportArchiveClient() as c:
        out = c.mark_notification_read(nid)
    if isinstance(out, dict):
        return {"id": out.get("id", nid), "marked_read": True}
    return {"id": nid, "marked_read": True}


def _do_notifications_mark_all_read(_args: dict) -> Any:
    with ReportArchiveClient() as c:
        n = c.mark_all_notifications_read()
    return {"marked_read": int(n)}


_DISPATCH = {
    "ping": _do_ping,
    "templates_list": _do_templates_list,
    "templates_show": _do_templates_show,
    "templates_suggest": _do_templates_suggest,
    "widgets_catalog": _do_widgets_catalog,
    "widgets_suggest_extras": _do_widgets_suggest_extras,
    "report_show": _do_report_show,
    "report_lock_status": _do_report_lock_status,
    "examples_status": _do_examples_status,
    "tier_show": _do_tier_show,
    "report_create": _do_report_create,
    "report_update": _do_report_update,
    "report_revise": _do_report_revise,
    "report_append": _do_report_append,
    "report_add_page": _do_report_add_page,
    "report_delete": _do_report_delete,
    "report_mount": _do_report_mount,
    "report_unmount": _do_report_unmount,
    "report_mounts": _do_report_mounts,
    "reports_search": _do_reports_search,
    "workspaces_list": _do_workspaces_list,
    "entity_types_list": _do_entity_types_list,
    "entities_list": _do_entities_list,
    "report_milestone_add": _do_report_milestone_add,
    "report_milestone_remove": _do_report_milestone_remove,
    "file_upload": _do_file_upload,
    "catalog_sync": _do_catalog_sync,
    "examples_mine_from_report": _do_examples_mine_from_report,
    "tier_set": _do_tier_set,
    "catalog_sync_templates": _do_catalog_sync_templates,
    "report_export": _do_report_export,
    "report_import": _do_report_import,
    "report_dump": _do_report_dump,
    # ---- v0.5.0 -------------------------------------------------------- #
    "report_copy": _do_report_copy,
    "report_add_link": _do_report_add_link,
    "report_types_list": _do_report_types_list,
    "report_publish": _do_report_publish,
    "report_unpublish": _do_report_unpublish,
    "folders_list": _do_folders_list,
    "report_mount_set_folder": _do_report_mount_set_folder,
    "report_mount_set_edit_policy": _do_report_mount_set_edit_policy,
    "template_set_scope": _do_template_set_scope,
    "presets_list": _do_presets_list,
    "preset_create": _do_preset_create,
    "report_new_from_preset": _do_report_new_from_preset,
    "preset_delete": _do_preset_delete,
    "composite_get": _do_composite_get,
    "composite_summary_set": _do_composite_summary_set,
    "composites_submittable_for": _do_composites_submittable_for,
    "composites_requests_list": _do_composites_requests_list,
    "composites_submit": _do_composites_submit,
    "composites_request_accept": _do_composites_request_accept,
    "composites_request_reject": _do_composites_request_reject,
    "composites_request_withdraw": _do_composites_request_withdraw,
    # ---- v0.6.0 — composites body editing ------------------------------ #
    "composite_create": _do_composite_create,
    "composite_update": _do_composite_update,
    "composite_items_set": _do_composite_items_set,
    "composite_delete": _do_composite_delete,
    "composite_publish": _do_composite_publish,
    "composite_unpublish": _do_composite_unpublish,
    # ---- v0.6.0 — activities + notifications --------------------------- #
    "report_activities": _do_report_activities,
    "notifications_list": _do_notifications_list,
    "notifications_unread_count": _do_notifications_unread_count,
    "notification_mark_read": _do_notification_mark_read,
    "notifications_mark_all_read": _do_notifications_mark_all_read,
}


# --------------------------------------------------------------------------- #
# MCP server wiring
# --------------------------------------------------------------------------- #
server: Server = Server(SERVER_NAME)


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


# v0.5.2 — A4: build the typed-subclass → error-code dispatch table once at
# import time. Each tuple is (exception class, error code, include report_id?).
# Order matters: more-specific subclasses come first; the runtime loop walks
# the table top-down so a subclass match wins over its parent (AuthorLockedError
# before ApiError, etc.). Entries are skipped when the class is None — that
# happens only when client.py hasn't been updated to v0.5.2 yet (defensive).
def _build_typed_error_map() -> list[tuple[type, str, bool]]:
    raw = [
        # 403 — author lock (v0.5.1)
        (AuthorLockedError, "author_locked", True),
        # 403 — RA-stable Korean/ASCII signatures (A2)
        (FinalizedReadOnlyError, "finalized_readonly", True),
        (NoEditPermissionError, "no_edit_permission", True),
        (OutOfWorkspaceScopeError, "out_of_workspace_scope", True),
        # 409 — reports lock + revision (errors[0].code)
        (LockHeldByOtherError, "lock_held_by_other", True),
        (LockNotHeldError, "lock_not_held", True),
        (RevisionMismatchError, "revision_mismatch", True),
        # 409 — composites revision (FastAPI {detail:str})
        (CompositeRevisionConflict, "composite_revision_mismatch", False),
    ]
    return [(cls, code, has_rid) for (cls, code, has_rid) in raw if cls is not None]


_TYPED_ERROR_MAP: list[tuple[type, str, bool]] = _build_typed_error_map()


def _format_typed_error(exc: ApiError, code: str, include_report_id: bool) -> dict:
    """A4 — render a typed RA exception as {error, reason, code, status_code, ...}."""
    out: dict = {"error": code}
    reason = getattr(exc, "reason", None)
    if reason:
        out["reason"] = reason
    inner_code = getattr(exc, "code", None)
    if inner_code:
        out["code"] = inner_code
    if include_report_id:
        rid = getattr(exc, "report_id", None)
        if rid is not None:
            out["report_id"] = rid
    out["status_code"] = exc.status_code
    out["message"] = str(exc)
    if exc.payload is not None:
        out["payload"] = exc.payload
    return out


def _api_error_payload(exc: ApiError) -> dict:
    """A8 — promote payload.errors[0].code to top-level error_code."""
    out: dict = {
        "error": "API error",
        "status_code": exc.status_code,
        "message": str(exc),
        "payload": exc.payload,
    }
    if isinstance(exc.payload, dict):
        errs = exc.payload.get("errors")
        if isinstance(errs, list) and errs and isinstance(errs[0], dict):
            ec = errs[0].get("code")
            if ec:
                out["error_code"] = ec
    return out


@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    args = arguments or {}
    fn = _DISPATCH.get(name)
    if fn is None:
        return _text({"error": f"unknown tool '{name}'", "available": sorted(_DISPATCH)})
    try:
        result = await asyncio.to_thread(fn, args)
    except ApiError as e:
        # v0.5.2 — A4: typed-subclass dispatch BEFORE the generic ApiError
        # branch. Walk the (class, code, include_rid) table and emit a
        # structured {error: <code>, reason, code, report_id} payload.
        for cls, code, include_rid in _TYPED_ERROR_MAP:
            if isinstance(e, cls):
                return _text(_format_typed_error(e, code, include_rid))
        # A8 — generic ApiError, promote errors[0].code to top-level.
        return _text(_api_error_payload(e))
    except (ValueError, KeyError, IndexError, FileNotFoundError) as e:
        return _text({"error": type(e).__name__, "message": str(e)})
    except RuntimeError as e:
        # v0.5.2 — A5: classify the three RuntimeError flavours the inner
        # dispatchers raise (SnapshotMissing, LLMError, no-LLM-provider) so
        # the LLM sees `snapshot_missing` / `llm_error` / `no_llm_provider`
        # instead of a useless "internal" label.
        return _text(_classify_runtime_error(e))
    except Exception as e:  # final safety net — surface the type for diagnosis
        return _text({"error": "internal", "type": type(e).__name__, "message": str(e)})
    return _text(result)


def _classify_runtime_error(exc: RuntimeError) -> dict:
    """A5 — map SnapshotMissing / LLMError / no-LLM-provider to stable codes."""
    # Late imports — avoid pulling llm / schemas at module load (heavy).
    try:
        from report_skill.schemas import SnapshotMissing
    except ImportError:  # pragma: no cover
        SnapshotMissing = ()  # type: ignore[assignment,misc]
    try:
        from report_skill.llm import LLMError
    except ImportError:  # pragma: no cover
        LLMError = ()  # type: ignore[assignment,misc]

    if SnapshotMissing and isinstance(exc, SnapshotMissing):
        return {"error": "snapshot_missing", "detail": str(exc)}
    if LLMError and isinstance(exc, LLMError):
        return {"error": "llm_error", "detail": str(exc)}
    # `_do_report_revise` raises bare RuntimeError("no LLM provider configured ...")
    # when llm_mod.is_configured() returns False.
    msg = str(exc)
    if "no LLM provider" in msg or "no llm provider" in msg.lower():
        return {"error": "no_llm_provider", "detail": msg}
    return {"error": "internal", "type": type(exc).__name__, "message": msg}


async def _run() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    """console_scripts entry point."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
