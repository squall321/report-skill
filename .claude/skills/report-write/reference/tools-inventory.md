# report-write reference — MCP tool inventory + version history

Referenced from SKILL.md. Full list of MCP tools by the release that added them, plus the current inventory count. From any MCP client (Claude Desktop / Continue / Cursor) call them by name.

## v0.5.0 — new MCP tools

The following 21 MCP tools were added in 0.5.0.

Report-level:

- `report_copy` — POST `/reports/{id}/copy`; full or content-only clone (`mode=full|content`, default `full`).
- `report_add_link` — POST `/reports/{id}/links`; register a report-to-report link (`kind` default `"related"`, `direction` default `"outgoing"`). `direction="outgoing"` means "this report links TO the other" (the common case — used when the author of THIS report cites the other). `direction="incoming"` means "the other report links TO this one"; use it when the author of the OTHER report (the source) is registering an inbound reference to the current report.

  ```
  # outgoing — common case: my report references their earlier postmortem
  report_add_link(report_id=412, target_report_id=298, kind="related", direction="outgoing")
  # incoming — I am the editor of report 298 and want to record that 412 cites me
  report_add_link(report_id=298, target_report_id=412, kind="cited-by", direction="incoming")
  ```
- `report_types_list` — GET `/report-types`; resolver for `report_type_id`.
- `report_publish` — POST `/reports/{id}/publish`; finalize phase + fan-out notifications. Author-only.
- `report_unpublish` — POST `/reports/{id}/unpublish`; revert phase to drafting. Author-only.

Folders + mounts:

- `folders_list` — GET `/folders?workspace_slug=...`; folders inside a workspace board.
- `report_mount_set_folder` — PUT `/mounts/{rid}/{slug}/folder`; move a mount into a folder. `folder_id=null` clears.
- `report_mount_set_edit_policy` — PUT `/mounts/{rid}/{slug}/edit-policy`; one of `default | owner_only | coauthor | manager`. `manager` (v0.8.0+) auto-syncs a `workspace_manager` grant on the report.

Templates:

- `template_set_scope` — PATCH `/templates/{id}/scope`; restrict template ownership to a workspace tree. Empty list = 전사(global).

Presets:

- `presets_list` — GET `/presets`; preset summaries visible to the actor.
- `preset_create` — POST `/presets`; save the current report as a reusable preset.
- `report_new_from_preset` — POST `/presets/{id}/new-report`; instantiate a fresh report (lands in personal workspace).
- `preset_delete` — DELETE `/presets/{id}`; only the creator (or system admin) may delete.

Composites:

- `composite_get` — GET `/composites/{id}`; full composite with items + summary_widgets.
- `composite_summary_set` — PATCH `/composites/{id}`; replace `summary_widgets` (with optional `expected_revision`).
- `composites_submittable_for` — GET `/composites/submittable-for/{report_id}`; which composites accept this report.
- `composites_requests_list` — GET `/composites/{id}/requests`; default filter pending.
- `composites_submit` — POST `/composites/{id}/requests`; submitter creates a pending request.
- `composites_request_accept` — POST `/composites/{id}/requests/{req}/accept`; composite owner-only.
- `composites_request_reject` — POST `/composites/{id}/requests/{req}/reject`; composite owner-only. `reason` accepted for forward-compat but currently ignored server-side.
- `composites_request_withdraw` — POST `/composites/{id}/requests/{req}/withdraw`; submitter (or composite owner / system admin).

## v0.6.0 — new MCP tools

The following 11 MCP tools were added in 0.6.0.

Composites (top-level lifecycle):

- `composite_create` — POST `/api/composites`. Create a recurring or theme composite, optionally seeded with `items[]`.
- `composite_update` — PATCH `/api/composites/{id}`. Top-level field editing (`period_date` tri-state nullable, `expected_revision` concurrency).
- `composite_items_set` — PATCH `/api/composites/{id}` with `items[]`. Full agenda list replacement.
- `composite_delete` — DELETE `/api/composites/{id}`. Owner or sys-admin only.
- `composite_publish` — POST `/api/composites/{id}/publish`. Recurring composites freeze per-item snapshots. Idempotent.
- `composite_unpublish` — POST `/api/composites/{id}/unpublish`. Clears snapshots. Idempotent.

Activity feed:

- `report_activities` — GET `/api/reports/{id}/activities`. Newest-first lifecycle/lock/edit/mount events; `before_id` cursor pagination.

Notifications:

- `notifications_list` — GET `/api/notifications`. Returns `{items, unread_count}`. Supports `--unread` + `--kind` filters.
- `notifications_unread_count` — GET `/api/notifications/unread-count`. Single integer probe for polling loops.
- `notification_mark_read` — PATCH `/api/notifications/{id}/read`. Idempotent.
- `notifications_mark_all_read` — POST `/api/notifications/mark-all-read`. Returns rows-flipped count.

## v0.7.0 — new MCP tools

Widget relations:

- `widget_relations_list` — GET `/api/widget-relations`. List relation slugs that `rich_text` mention chips can target.

## v0.8.0 / v0.9.0 / v0.10.0 / v0.11.0 — tools documented elsewhere

- v0.8.0 grants/sharing tools (`content_shares_list/add/remove`, `folder_share_*`, `board_share_*`) — full semantics in `flows-advanced.md` § unified grants.
- v0.9.0 `widget_ref_categories_list` — documented in `widgets.md` § widget styling + cross-references.
- v0.10.0 soft delete + takedown tools (`report_trash`, `report_restore`, `report_takedown_request`, `takedowns_list`, `takedown_approve`, `takedown_reject`) — documented in `flows-advanced.md` (`_DISPATCH` 76 → 82).
- v0.11.0 content-aware read tools (`report_outline`, `page_show_content`, `block_show`, `block_preview`) — documented in SKILL.md Flow B (read-before-patch sequence).

## v0.12.0 — high-value read surface + change detection automation

v0.12.0 ships 5 new MCP tools the LLM has been missing — surfaces that prior audits had flagged but were postponed. It also adds two scripts that close the manual-discovery loop on RA upstream changes.

### 5 new MCP tools (LLM use cases)

| Tool | Use when |
|---|---|
| `composites_by_report` | "Which weekly composites use this report?" — reverse navigation before editing |
| `reports_list` | General report list with filters (entity_ids, folder_id, include_public, include_descendants) — distinct from `reports_search` (mention-chip linkable subset) |
| `comments_inbox_list` | "What review threads need my attention?" — open / unread threads across all visible reports |
| `entities_usage_list` | "Which entities are unused / deprecated candidates?" — `with_usage=true` projection |
| `workspace_members_list` | "Who can edit this board?" / "Who is the manager?" — before recommending edit-policy or filing a takedown |

### Change detection scripts (operator side)

These are NOT MCP tools — they are dev-side scripts for keeping report-skill in sync with RA upstream:

- `scripts/watch_ra.ps1` — daily diff scanner. Run on schedule; reports new RA commits since last check, grouped by conventional prefix, with file impact tally (backend Python files, frontend-only files, migrations added, routes/schemas/registry touched). Recommends running `check_ra_impact.py` when backend changes detected.
- `scripts/check_ra_impact.py` — automated gap analysis. Compares RA `@router` decorators, widget content_schema field additions, and Korean error strings against report-skill coverage; surfaces concrete gaps (un-wrapped endpoints, missing adapter passthroughs, undetected error strings). Closes the manual-discovery half of the audit pattern.

### Prompt hint coverage (full)

`prompt.py` `_WIDGET_INPUT_HINTS` now covers all 33 widgets (was 12). Every widget the LLM might author gets a 1-3-line input guide alongside the JSON schema dump, with key fields + common pitfalls. The hints are merged into `build_create_prompt` / `build_block_revise_prompt` / `build_block_batch_prompt` automatically.

## v0.13.0 — recovery flows + backend-truth corrections

No new endpoints — this release hardens error handling and aligns the docs with verified backend behavior.

- **5 new typed error codes** — `mount_forbidden` (403, board-manager-only op attempted), `mount_target_invalid` (400, bad slug / non-org workspace / folder mismatch), `report_still_mounted` (409, permanent delete attempted while mounted), plus two infra codes: `network_unreachable` and `auth_unavailable` (both retryable — backend down, not a code bug). All five are in the typed error table in `errors-recovery.md`.
- **Recovery flows** — 5 worked scenarios for `revision_mismatch`, `composite_revision_mismatch`, `lock_held_by_other`, `finalized_readonly`, and `report_still_mounted`; the error table links to them. See `errors-recovery.md` § Recovery flows (worked scenarios).
- **Backend-truth corrections** — unmount is board-manager-only (owners go through `report_takedown_request` → `takedown_approve`, SKILL.md Flow G); `report_trash` succeeds while mounted — only `report_delete` is blocked by mounts (v0.10.0 section corrected).
- **Confirm gates** — `composite_delete` and `preset_delete` now require an explicit `confirm=true` (destructive-op gate); calls without it are rejected before hitting the API.
- **Label required on `report_milestone_remove`** — removing by date alone is no longer accepted; pass `label` too, so one date shared by several milestones cannot be mass-deleted by accident.
- **Token caching** — the MCP server caches the auth token across tool calls instead of re-authenticating per call; multi-tool sequences are noticeably faster.

## v0.14.0 — observability + dry_run

- `session_log` — recent skill activity (tool calls + HTTP calls) with status / duration / error codes. `errors_only=true` to triage a failure the user just hit.
- `voc_export` — export a VOC bundle (skill version, environment, recent calls/errors, optional user note) as `voc-<timestamp>.md` + `.json` under `%LOCALAPPDATA%/report-skill/voc/`. Call this whenever the user says something broke and wants to report it.
- `dry_run` flag on `report_create` / `report_update` / `report_append` / `report_add_page` (validate + build the payload, nothing sent) and `report_delete` (impact preview: mounts + composite refs, nothing deleted).
- `notifications_mark_all_read` now requires `confirm=true`.

## MCP tool inventory (v0.14.x)

The MCP server now exposes **93 tools via stdio** (`report-skill-mcp`). The full list grew from the initial 23 read/write/offline tools through the version sections above — call any of them by name from Claude Desktop / Continue / Cursor / any MCP client. The exact set is the runtime `_DISPATCH` map in `mcp_server.py`; verify locally with:

```powershell
report-skill-mcp --help   # or:
python -c "from report_skill.mcp_server import _DISPATCH; print(len(_DISPATCH))"
```
