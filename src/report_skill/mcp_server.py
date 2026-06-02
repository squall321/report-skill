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
from report_skill.client import ApiError, ReportArchiveClient

SERVER_NAME = "report-skill"


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
        },
        ["template_id", "blocks", "title"],
    ),
    _tool(
        "report_update",
        "Patch specific blocks of an existing report. Other blocks preserved. Auto edit-lock.",
        {
            "report_id": {"type": "integer"},
            "blocks": {"type": "object"},
            "extra_blocks": {"type": "array", "items": {"type": "object"}},
            "title": {"type": "string"},
            "phase": {"type": "string", "enum": ["drafting", "reviewing", "finalized"]},
            "lifecycle": {"type": "string", "enum": ["single_shot", "ongoing"]},
            "status": {"type": "string", "description": "LEGACY alias for phase"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "page_index": {"type": "integer", "default": 0},
        },
        ["report_id"],
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
        "Append a new page to an existing report. New template allowed.",
        {
            "report_id": {"type": "integer"},
            "template_id": {"type": "string"},
            "blocks": {"type": "object"},
            "name": {"type": "string"},
            "extra_blocks": {"type": "array", "items": {"type": "object"}},
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
        "POST a previously-exported ReportCreate JSON payload to /api/reports.",
        {"payload_path": {"type": "string", "description": "absolute path to the payload JSON file"}},
        ["payload_path"],
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
    with ReportArchiveClient() as c:
        env = c.login()
    return {"status": "ok", "logged_in_as": env.get("email"), "user_id": env.get("user_id")}


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
        "status": report.get("status"),
        "revision": report.get("revision"),
        "report_date": report.get("report_date"),
        "tags": report.get("tags"),
        "page_count": len(report.get("pages") or []),
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
        payload = report_builder.build_create_payload(
            tpl, result.content,
            title=args["title"],
            report_date=args.get("report_date"),
            phase=args.get("phase"),
            lifecycle=args.get("lifecycle"),
            status=args.get("status"),  # legacy → mapped to phase by builder
            tags=derived_tags,
            extra_blocks=result.extra_blocks,
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
        existing_extra_ids = {b.get("id") for b in existing_extras if isinstance(b, dict)}
        new_extras = [e for e in result.extra_blocks if e.get("id") not in existing_extra_ids]
        updated = report_ops.update_blocks(
            c, rid,
            page_index=page_index,
            block_patches=result.content,
            add_extra_blocks=new_extras,
            title=args.get("title"),
            phase=args.get("phase"),
            lifecycle=args.get("lifecycle"),
            status=args.get("status"),
            tags=args.get("tags"),
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
        updated = report_ops.add_page(
            c, rid,
            template_id=tpl["template_id"],
            template_version=tpl["version"],
            name=args.get("name"),
            content=result.content,
            extra_blocks=result.extra_blocks,
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


def _do_report_import(args: dict) -> Any:
    """POST a previously-exported ReportCreate payload."""
    from pathlib import Path as _Path
    p = _Path(args["payload_path"])
    if not p.is_file():
        raise FileNotFoundError(f"payload not found: {p}")
    payload = json.loads(p.read_text(encoding="utf-8"))
    with ReportArchiveClient() as c:
        created = c.create_report(payload)
    return {
        "id": created.get("id"),
        "title": created.get("title"),
        "revision": created.get("revision"),
        "view_url": f"http://localhost:3001/reports/{created.get('id')}",
    }


_DISPATCH = {
    "ping": _do_ping,
    "templates_list": _do_templates_list,
    "templates_show": _do_templates_show,
    "templates_suggest": _do_templates_suggest,
    "widgets_catalog": _do_widgets_catalog,
    "widgets_suggest_extras": _do_widgets_suggest_extras,
    "report_show": _do_report_show,
    "examples_status": _do_examples_status,
    "tier_show": _do_tier_show,
    "report_create": _do_report_create,
    "report_update": _do_report_update,
    "report_append": _do_report_append,
    "report_add_page": _do_report_add_page,
    "report_delete": _do_report_delete,
    "report_mount": _do_report_mount,
    "report_unmount": _do_report_unmount,
    "report_mounts": _do_report_mounts,
    "report_milestone_add": _do_report_milestone_add,
    "report_milestone_remove": _do_report_milestone_remove,
    "file_upload": _do_file_upload,
    "catalog_sync": _do_catalog_sync,
    "examples_mine_from_report": _do_examples_mine_from_report,
    "tier_set": _do_tier_set,
    "catalog_sync_templates": _do_catalog_sync_templates,
    "report_export": _do_report_export,
    "report_import": _do_report_import,
}


# --------------------------------------------------------------------------- #
# MCP server wiring
# --------------------------------------------------------------------------- #
server: Server = Server(SERVER_NAME)


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    args = arguments or {}
    fn = _DISPATCH.get(name)
    if fn is None:
        return _text({"error": f"unknown tool '{name}'", "available": sorted(_DISPATCH)})
    try:
        result = await asyncio.to_thread(fn, args)
    except ApiError as e:
        return _text({"error": "API error", "status_code": e.status_code,
                      "message": str(e), "payload": e.payload})
    except (ValueError, KeyError, IndexError, FileNotFoundError) as e:
        return _text({"error": type(e).__name__, "message": str(e)})
    except Exception as e:  # final safety net — surface the type for diagnosis
        return _text({"error": "internal", "type": type(e).__name__, "message": str(e)})
    return _text(result)


async def _run() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    """console_scripts entry point."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
