# Changelog

## 0.14.0 — 2026-06-11

Minor — closes the entire remaining P3/P4 backlog from the v0.11.0 cold-eye
review: VOC error reporting, observability, LLM-context reduction (SKILL.md
3-tier split), dry_run across content writes, and the last two LOW items.
MCP `_DISPATCH` grows 91 → 93.

Added — VOC / observability (the headline):

- NEW `src/report_skill/telemetry.py` — every HTTP call (success + failure,
  with status / duration / typed error code) and every MCP tool call is
  appended to daily JSONL logs under `%LOCALAPPDATA%/report-skill/logs/`.
  Secrets auto-redacted (password / token / secret / authorization patterns
  masked); error messages truncated at 500 chars; only arg KEY names are
  recorded, never values. Telemetry never raises — failures no-op silently.
- NEW MCP tool `session_log` — recent skill activity with filters
  (`n`, `errors_only`, `kind=http|tool`). The LLM can triage "what just
  failed" without the user copy-pasting stack traces.
- NEW MCP tool `voc_export` — one call bundles skill version, Python/OS,
  backend URL (creds excluded), recent calls, recent errors, and an optional
  user note into `voc-<timestamp>.md` + `.json` under
  `%LOCALAPPDATA%/report-skill/voc/`. 사용자가 문제를 겪는 즉시 "VOC
  내보내줘" 한 마디로 첨부 가능한 제출 파일이 만들어진다.
- CLI mirrors: `report-skill voc export [--note ...]` + `report-skill voc
  log [--n/--errors-only/--kind]` (rich table). The 5 highest-traffic write
  commands (report create / update / publish / revise / append) print a
  one-line VOC hint after a generic ApiError.
- Hook points: `client._request` records the FINAL outcome of every HTTP
  call (retry loops record once); `mcp_server.call_tool` records every tool
  dispatch via try/finally so all exit paths are covered.

Changed — SKILL.md 3-tier split (LLM context cost: 1,162 → 257 lines, -78%):

- `SKILL.md` is now the CORE tier only: the 3 main flows (create / revise
  with the content-aware read sequence / mount-publish with the takedown
  branch), hard rules, a 5-code error primer, and a routing table.
- 4 reference files the LLM Reads on demand:
  `reference/widgets.md` (130) — per-widget shapes, mention:// spec, color
  tokens, cell_styles/cell_html/text_html;
  `reference/errors-recovery.md` (124) — full 20-code table + Recovery
  flows #1–#5 + lifecycle notes;
  `reference/tools-inventory.md` — version-by-version tool history + the
  93-tool inventory;
  `reference/flows-advanced.md` (433) — presets / composites / grants /
  takedown / notifications / activities.
- PACKAGING BUG caught in the process: `build_release.ps1` copied
  `.claude/skills/*.md` FLAT — directory-style skills would have shipped
  without their subfolders. Fixed to recursive copy (install.ps1 hints too).

Added — dry_run across content writes:

- `report_create` / `report_update` / `report_append` / `report_add_page`
  accept `dry_run=true`: the full normalize + validate + payload-build
  pipeline runs, then returns `{dry_run, would_send, validation}` WITHOUT
  sending. The LLM can pre-flight a complex multi-block patch and fix
  schema errors before touching the server.
- `report_delete` `dry_run=true` returns an impact preview — title,
  mounted boards, `composite_ref_count` — without deleting (and without
  needing `confirm` in preview mode).
- `report_revise` already had dry_run (unchanged).

Fixed — last LOW backlog items:

- `_ENTITY_TYPES_CACHE` now carries a 600s TTL + one forced refetch on an
  axis-lookup miss (heals "new entity axis added after server start" in
  long-lived MCP processes).
- `notifications_mark_all_read` now requires `confirm=true` (flips every
  unread row irreversibly; per-id `notification_mark_read` preferred).

Verified:

- Unit: 486 passed, 5 skipped, 12 deselected.
- Live E2E (backend up): 11 passed, 1 skipped — and the telemetry log
  captured the suite's real HTTP traffic (POST /reports 29ms, trash,
  mounts...), proving the VOC pipeline end-to-end.
- `_DISPATCH` = 93; structural parity locks (4) all green.

## 0.13.1 — 2026-06-10

Patch — first LIVE run of the v0.13.0 E2E suite against the real backend
caught one genuine API-contract drift; this release fixes it and freshens
the bundled snapshot. The backend was restarted (uvicorn :3000) and DB
migrations p28–p31 applied (password reset / soft delete / takedown queue /
composite snapshot-detach) — the backend code had outpaced the DB schema,
so every login 500'd until the upgrade.

Fixed — trash/restore API contract drift (caught by e2e, impossible to
catch with fake clients):

- RA's POST /reports/{id}/trash and /restore return `data=None`
  (`success_response(data=None)`) — NOT the report record. client.py's
  docstrings claimed "returns the report with deleted_at set" and the MCP
  dispatchers passed the raw None straight to the LLM.
- `_do_report_trash` / `_do_report_restore` now re-fetch after the write
  and return verified state: `{report_id, trashed/restored, deleted_at}`
  plus a recovery note. The LLM gets ground truth instead of `null`.
- client.py docstrings corrected — including the stale "blocked while
  mounted" claim (trash succeeds while mounted; only permanent delete is
  blocked with 409 report_still_mounted).
- The 2 affected e2e tests now assert state via re-fetch.

Changed — bundled snapshot regenerated (33 widgets, 8 templates,
fetched 2026-06-10):

- `widgets.snapshot.json` now includes caption_color / caption_html /
  note_color / note_html / cell_styles / cell_html / text_html /
  ref_categories — everything RA shipped since the stale 2026-06-01 dump.
  The LLM prompt builder finally advertises the fields the adapters have
  been passing through since v0.9.x.
- Structural parity lock (d) — adapter `_PASSTHROUGH` ⊆ snapshot — promoted
  from XFAIL to a HARD lock (it XPASSed on the fresh snapshot).

Verified (live backend):

- E2E: 11 passed, 1 skipped (dry_run — LLM provider not configured).
  Mount/unmount, purge-blocked-while-mounted (409 typed), trash-while-
  mounted, Korean 403 detection, Korean payload round-trip, stale-revision
  recovery — all validated against the real API for the first time.
- Unit: 484 passed, 5 skipped, 12 deselected.
- `report-skill --version` reports 0.13.1.

## 0.13.0 — 2026-06-10

Minor — robustness release. A 6-lens audit of the publishing surface (verdict:
patchy) drove session resilience, write-tool safety gates, 5 new typed error
codes, executable recovery flows in SKILL.md, 4 generic structural parity
locks, and a gated live-backend E2E suite. _DISPATCH stays 91 — no new tools,
deeper safety on the existing ones.

Session resilience (client.py):

- Module-level JWT cache (11h TTL vs RA's 12h token life) — every
  `with ReportArchiveClient()` block reuses one token instead of paying a
  ~250ms bcrypt login per MCP tool call. A 30-call publish flow saves ~7s.
- 401 → one-shot re-login + retry in `_request` (loop-guarded; skips the
  login request itself). Required pairing with the cache: a stale cached
  token now heals transparently.
- New typed infra errors: `NetworkUnreachableError` ("network_unreachable",
  retryable) for httpx connect/timeout failures and `AuthUnavailableError`
  ("auth_unavailable", retryable) when login itself can't reach the backend.
  The LLM can now distinguish "backend down — retry later" from a code bug
  (previously both surfaced as `{"error":"internal"}`).
- GET-only network retry (2 attempts, 0.5s/1.0s backoff). Writes get NO
  network retry — avoids duplicate creates on lost responses.
- logger.info backfilled on 24 write methods (v0.7.1 discipline,
  retro-applied — was missing on create_report, publish_report, all
  composite writes, etc.).

New typed error codes (client detection + `_TYPED_ERROR_MAP` + SKILL.md table):

- `mount_forbidden` (403) — unmount / takedown processing without board-
  manager rights. Guidance: owners use `report_takedown_request` instead.
- `mount_target_invalid` (400) — bad slug / non-org workspace / folder
  mismatch (first 400-family typed code).
- `report_still_mounted` (409) — permanent delete attempted while mounted;
  carries report_id; links to Recovery flow #5.

Write-tool safety:

- `composite_delete` + `preset_delete` now REQUIRE confirm=true (hard
  deletes — published composites included, no trash exists for either).
- `report_milestone_remove` now REQUIRES label — date-only matching used to
  bulk-remove every milestone on the same date.
- `report_delete` description: "PERMANENT purge — prefer report_trash".
- `report_unmount` warns that folder/edit-policy/note are discarded;
  `takedown_approve` says check `takedowns_list` first;
  `notifications_mark_all_read` steers to per-id.

Mount / publish UX guards (mcp_server dispatchers):

- workspace_slugs passed as a bare string no longer char-splits ("dx" →
  ["d","x"]) — wrapped into a single-element list.
- folder_id + multiple boards pre-rejected (backend validates the same
  folder against every board → guaranteed late failure).
- Trashed-report guard: mounting or publishing a `deleted_at` report is
  rejected client-side with "restore first via report_restore".
- Mount failures now state the batch is atomic (zero mounts created).
- Re-mount of an already-mounted board now points to
  `report_mount_set_folder` / `report_mount_set_edit_policy` (re-mounting
  never updates them).
- `report_publish` response includes `mounted_board_count`; 0 adds a
  warning that notifications reached nobody.
- view_url no longer hardcodes localhost:3001 — derived from
  `report_api_base_url` via `_frontend_base()` (11 sites).

SKILL.md — recovery flows + 2 backend-truth corrections:

- New "Recovery flows (worked scenarios)" section: #1 revision_mismatch
  (re-fetch → rebase → retry → report_activities), #2 composite_revision_
  mismatch, #3 lock_held_by_other (report_lock_status → wait, never force),
  #4 finalized_readonly (unpublish → edit → republish), #5 report_still_
  mounted (trash needs NO unmount; purge needs unmount-or-takedown per
  board). Error-table cells link to flows.
- CORRECTION: Flow G — unmount is board-manager-only (RA mounts/services
  417-421); owners must use the takedown queue. Previous text sent owners
  into guaranteed 403s.
- CORRECTION: v0.10.0 section claimed "mounted blocks trash" — actually
  trash succeeds while mounted (board copies preserved); only permanent
  delete is blocked (409 report_still_mounted).
- Network resilience note (POST timeout → check existence via
  reports_search before re-creating) + trashed-report guard note +
  catalog_sync MCP tool name alongside the CLI wording.

Structural parity locks (tests/test_structural_parity.py — NEW, generic,
reflection/AST-based; replaces per-incident hardcoded lists):

- (a) every ApiError subclass (19 today) must be in `_TYPED_ERROR_MAP` —
  the "client has it, MCP map forgot it" incident class (v0.8.1, v0.10.1)
  is now structurally impossible.
- (b) every typed exception must be handled in cli*.py except clauses (AST
  scan) or carry a justified exemption. Fixed today: LockNotHeldError +
  CompositeRevisionConflict handlers added to cli.py.
- (c) every client write method must call logger.info (AST scan; baseline
  empty after the 24-method backfill).
- (d) every adapter _PASSTHROUGH key must exist in the bundled snapshot's
  content_schema — currently XFAIL (snapshot fetched 2026-06-01 predates
  caption_color etc.); regenerate via catalog_sync against a live backend,
  then drop the xfail. The corruption is now visible instead of silent.

E2E suite (tests/e2e/ — NEW, gated):

- pytest marker `e2e` + `addopts -m 'not e2e'` (default runs untouched);
  requires RS_E2E=1 AND a reachable backend (login health probe) or
  everything skips. Self-cleaning fixture: unmount → trash → purge.
- 12 lifecycle cases: stdio handshake/tool count, create, read round-trip,
  snapshot drift, stale-revision recovery, Korean-403 detection,
  mount/list/unmount, purge-blocked-while-mounted (409), trash-succeeds-
  while-mounted, trash/restore, Korean payload round-trip, dry-run
  no-side-effect. Mount-mutating cases accept the typed 403 as a pass
  (validates detection wiring when the service account isn't a manager).
- tests/_verify_path_b_mcp.py deleted (absorbed; it polluted real data
  with no cleanup).

Verified:

- pytest 483 passed, 5 skipped, 12 deselected (e2e), 1 xfailed (snapshot
  stale — intentional marker).
- `_DISPATCH` = 91 (unchanged); typed map = 20 codes.
- Live validation while the backend was down: the new
  `AuthUnavailableError` fired exactly as designed on the login probe.
- `report-skill --version` reports 0.13.0.

## 0.12.0 — 2026-06-10

Minor — first release driven by structural-improvement analysis instead of
"RA shipped X, react." Closes 3 of the 5 priority backlogs surfaced in the
v0.11.0 cold-eye review.

Added — 5 new MCP tools (P1 backlog), _DISPATCH 86 → 91:

- `composites_by_report` — reverse navigation: every composite that
  references the report as an item. GET /api/composites/by-report/{id}.
- `reports_list` — general report list with entity_ids / folder_id /
  include_public / include_descendants filters. Distinct from
  `reports_search` (mention-chip linkable subset).
- `comments_inbox_list` — open / unread review threads. GET
  /api/comments/inbox.
- `entities_usage_list` — entities with usage_count populated. GET
  /api/entities?with_usage=true.
- `workspace_members_list` — board members + roles. GET
  /api/workspaces/{slug}/members.

Each has a matching CLI command under `report-skill tools …`.

Added — change-detection automation (P0 backlog):

- `scripts/watch_ra.ps1` — daily scanner for RA upstream. Detects new
  commits since last check (persists cursor at
  `%LOCALAPPDATA%/report-skill/last_ra_check.txt`), groups by
  conventional prefix (feat / fix / refactor / chore), tallies file
  impact (backend Python, frontend-only, migrations, routes / schemas /
  registry touched). Recommends running the deeper impact analyzer when
  backend changes detected; otherwise reports "frontend-only, no
  report-skill action needed".
- `scripts/check_ra_impact.py` — automated gap analysis. Diffs RA
  `@router` decorators, widget content_schema field additions, and new
  Korean error strings against report-skill coverage; surfaces concrete
  gaps. Closes the manual-discovery half of the audit pattern that
  v0.4-v0.11 cycles repeated by hand.

Added — prompt hint coverage from 12/33 → 33/33 widgets (P1 backlog):

- `prompt.py` `_WIDGET_INPUT_HINTS` extended to chart, scatter,
  scatter3d, box, density, contour, heatmap, sankey, network, mind_map,
  tree, radar, milestone, flowchart, equation, video, attachment,
  cad_3d, raci_matrix, key_value, quadrant — 21 new entries. Every
  widget the LLM might author now has a 1-3-line authoring guide with
  required fields + key optional fields + common pitfalls.

SKILL.md:

- New "v0.12.0 — high-value read surface + change detection automation"
  section documents the 5 new tools (with "use when" mapping) and the
  watch_ra / check_ra_impact operator scripts.
- Tool inventory header bumped to "MCP tool inventory (v0.12.x)" + 91.

Tests — pytest 474 → 479 (+5):

- `_ARGS_BY_TOOL` gains 5 entries (composites_by_report etc.).
- fake-client mock gains 5 method return values.
- `test_dispatch_count_is_91` in `test_mcp_roundtrip.py` and
  `test_widget_relations.py` lock the new surface size.

Verified:

- pytest 479 passed, 5 skipped (no failures).
- MCP `_DISPATCH` count = 91.
- All 5 new client methods importable.
- `_WIDGET_INPUT_HINTS` now has 33 entries (1 per widget).
- `report-skill --version` reports 0.12.0.

## 0.11.0 — 2026-06-10

Minor — adds 4 content-aware read tools so the driving LLM (Claude Desktop /
Cursor) can SEE existing content before authoring patches. Through v0.10.x
`report_show` returned page count + block ids only — never the actual content
body. The LLM was effectively patching blind. This release closes that gap.
MCP `_DISPATCH` grows 82 → 86. Backward-compatible: existing tools unchanged.

Added — 4 new MCP tools + matching CLI commands:

- `report_outline` — structural tree of a report: every page + block id +
  widget type + title-only preview. NO content bodies. Use this first to
  navigate before fetching specific blocks. CLI: `report outline <id>`.
- `page_show_content` — dump one page completely with every block's current
  content + props. `truncate=true` (default) caps each block body at ~1000
  chars for navigation; `false` returns raw content. CLI:
  `report page-show <id> <page-index> [--full]`.
- `block_show` — pin-point fetch of one block: raw content + widget type +
  props + schema summary (required fields, max_chars). Exactly what the LLM
  needs to author an accurate patch. Handles both content blocks and extras.
  CLI: `report block-show <id> <page-index> <block-id>`.
- `block_preview` — human-readable markdown / plain-text rendering of a
  block: table → markdown grid, rich_text → bulleted lines, heading →
  `# title`, milestone → bulleted timeline, equation → `$$ latex $$`, etc.
  Useful for visual inspection. CLI: `report block-preview <id> <page-index>
  <block-id>`.

All 4 are READ-ONLY — none mutate state. They safely precede any write.

Typical edit flow with these tools:

1. `report_outline(report_id)` — identify target page + block.
2. `page_show_content` or `block_show` — read current content.
3. Optional `block_preview` to confirm visual intent.
4. `report_update` (specific blocks) or `report_revise` (LLM-rewrite).

Implementation details:

- All 4 dispatchers reuse `report_ops.fetch_report` + `client.fetch_template`
  so the existing typed-exception family (AuthorLocked, TrashRestoreForbidden,
  OutOfWorkspaceScope, etc.) flows naturally without per-tool plumbing.
- Templates are cached per `(template_id, template_version)` within
  `report_outline` so a 20-page report with the same template across pages
  only fetches the template once.
- `block_preview` is best-effort per widget type — unknown widget types fall
  back to JSON dump (capped at 2000 chars).

SKILL.md:

- New "v0.11.0 — content-aware read surface" section documents the 4 tools
  with a side-by-side "use when / returns" table and the recommended edit
  flow.
- Tool inventory header bumped to "(v0.11.x)" + 86 tools.

Tests — pytest 470 → 474 (+4):

- `tests/test_dispatch_parametrized.py` `_ARGS_BY_TOOL` gains 4 entries
  (block_id = "summary" canonical). `_install_stubs` `fetch_report` stub
  now includes `content: {"summary": "Sample summary text."}` so the new
  block-aware dispatchers reach their return statement without raising
  KeyError on the smoke harness.
- `tests/test_mcp_roundtrip.py::test_dispatch_count_is_86` +
  `tests/test_widget_relations.py::test_dispatch_count_is_86` — renamed
  and bumped 82 → 86 to lock the new surface size.

Verified:

- pytest 474 passed, 5 skipped (no failures).
- MCP `_DISPATCH` count = 86.
- All 4 new tools registered + dispatch through fake client without
  raising.
- `report-skill --version` reports 0.11.0.

## 0.10.2 — 2026-06-10

Patch — closes 6 leftover gaps the prior v0.10.0/v0.10.1 sweeps missed,
caught by a single exhaustive 21-layer audit run.

Fixed (high) — comparison adapter `note_color` / `note_html` passthrough:

- `comparison.py` `_PASSTHROUGH_SIMPLE` gains `note`, `note_color`, `note_html`
  — the v0.9.1/v0.9.2 caption-color sweep covered every adapter for caption
  fields but missed the comparison note siblings (table + image were the only
  ones that got note passthrough then). RA registry has long advertised them
  so the adapter was silently dropping any color/html note the user picked.

Fixed (high) — `TakedownOwnerForbiddenError` + detection + mapping:

- New typed exception `TakedownOwnerForbiddenError` in `client.py`. Catches
  RA 403 `"본인 보고서만 게시취소를 요청할 수 있습니다."` (a non-owner trying
  to submit a takedown request on someone else's report). Surfaces
  `{error: "takedown_owner_forbidden", reason, report_id}`.
- Defensive import added to `mcp_server.py` and the class registered in
  `_build_typed_error_map()` so the call_tool envelope reaches the LLM.
- The v0.10.1 audit found 6/7 new RA error strings covered — this catches
  the 7th, completing typed-exception coverage for the soft-delete + takedown
  surface.

Fixed (high) — CLI typed-exception handlers for the 6 v0.10.0 write commands:

- `report trash` / `report restore` — catch `TrashRestoreForbiddenError`
  before generic `ApiError` and emit `[trash_restore_forbidden]` with a
  parenthetical reminder that only owner/sys-admin may act.
- `report takedown-request` — catch `TakedownOwnerForbiddenError` (v0.10.2).
- `tools takedown-approve` / `tools takedown-reject` — catch
  `TakedownAlreadyProcessedError` (do-not-retry guidance) and
  `TakedownManagerForbiddenError` (manager-only reminder).
- Exit code 3 distinguishes typed-forbidden from generic HTTP error (exit 2),
  matching the convention established for `shares` commands in v0.8.3.
- Without this v0.10.1's typed envelopes only reached the MCP surface — CLI
  users still saw opaque `ApiError` text.

Fixed (high) — `deleted_at` surfaced on `_do_report_show`:

- `mcp_server.py` `_do_report_show` summary projection now includes
  `deleted_at` so the LLM can detect a trashed report BEFORE issuing a
  write that would otherwise raise `TrashRestoreForbiddenError` (non-owner)
  or fail silently because writes on a trashed report are ill-defined.

Fixed (med) — SKILL.md error-code table rows for v0.10.x:

- 4 new rows added to the typed-error reference table near line 879:
  `trash_restore_forbidden`, `takedown_owner_forbidden`,
  `takedown_manager_forbidden`, `takedown_already_processed`. Each has the
  HTTP code, meaning, and "how the LLM should react" guidance matching the
  rest of the table.

Tests — pytest 469 → 470 (+1) plus 1 expanded:

- `tests/test_authorlocked_mapping.py` gains
  `test_build_typed_error_classifies_takedown_owner_forbidden` detection test.
- `test_typed_error_map_includes_v010_classes` expanded to assert
  `takedown_owner_forbidden` is also in `_TYPED_ERROR_MAP`. The parity lock
  now covers all 4 v0.10.x typed codes — so the v0.8.1 omission pattern
  ("client receives but MCP doesn't") cannot recur for this surface either.

Verified:

- pytest 470 passed, 5 skipped (no failures).
- MCP `_DISPATCH` count = 82 (unchanged — patch only).
- 4 typed-error map codes (trash + 3 takedown) confirmed by test.
- `report-skill --version` reports 0.10.2.

## 0.10.1 — 2026-06-10

Patch — closes the v0.10.0 audit gap: 6 new MCP tools shipped but the
matching 5 new RA 403/409 Korean error strings were not detected by
`_build_typed_error`, so the LLM saw opaque ApiError envelopes whenever
a write was forbidden. Also closes 3 prompt-builder / docs drift items.

Fixed (high) — 3 new typed exceptions reach the LLM end-to-end:

- `TrashRestoreForbiddenError` (403) — catches `"이 보고서를 삭제할 권한이 없습니다 (소유자만 가능)."`
  + `"이 보고서를 복구할 권한이 없습니다 (소유자만 가능)."`. Surfaces
  `{error: "trash_restore_forbidden", reason: ..., report_id: N}`. Hits
  non-owner `report_trash` / `report_restore` calls.
- `TakedownManagerForbiddenError` (403) — catches both variants of
  `"이 게시판...게시취소...권한이 없습니다 (게시판 매니저만 가능...)."`.
  Surfaces `{error: "takedown_manager_forbidden", reason: ...}`. Hits
  non-manager `takedown_approve` / `takedown_reject` calls (RA 3e92860).
- `TakedownAlreadyProcessedError` (403, RA `MountForbiddenError` envelope)
  — catches `"이미 처리된 요청입니다."`. Surfaces
  `{error: "takedown_already_processed", ...}` so the LLM knows the
  request is closed and does NOT retry.

All 3 subclasses are registered in `mcp_server._build_typed_error_map()`
so `call_tool` produces structured envelopes (same pattern as the v0.8.2
fix for the grants typed exceptions). Both client.py and mcp_server.py
imports are defensive — older client.py keeps loading.

Fixed (med) — prompt builder hint coverage for new v0.9.0/v0.10.0 fields:

- `prompt.py` `_WIDGET_INPUT_HINTS["heading"]` — documents `text_html`
  (v0.10.0 — sanitized HTML for per-char color/format on top of plain
  `text`, both kept in sync).
- `prompt.py` `_WIDGET_INPUT_HINTS["table"]` — documents `cell_styles` +
  `cell_html` side-tables keyed by `"rowKey::columnKey"`.
- `prompt.py` `_WIDGET_INPUT_HINTS["comparison"]` — same for
  `"rowKey::caseKey"`.

Without these hints v0.10.0's passthrough was effectively invisible — the
adapter would forward the fields but the LLM never knew to author them.

Fixed (low) — docs drift:

- `docs/BUILDING.md` architecture diagram updated from `_DISPATCH 76 tools (v0.9.x)`
  to `_DISPATCH 82 tools (v0.10.x)`.

Tests — pytest 465 → 469 (+4):

- `tests/test_authorlocked_mapping.py` gains 3 detection tests
  (`test_build_typed_error_classifies_trash_restore_forbidden` /
  `_takedown_manager_forbidden` / `_takedown_already_processed`) plus a
  parity lock `test_typed_error_map_includes_v010_classes` so the v0.8.1
  omission pattern cannot recur for these 3 codes.

Verified:

- pytest 469 passed, 5 skipped (no failures).
- MCP `_DISPATCH` count = 82 (unchanged — patch only).
- All 3 new typed exceptions importable + registered in
  `_TYPED_ERROR_MAP` (asserted by new test).
- `report-skill --version` reports 0.10.1.

## 0.10.0 — 2026-06-09

Minor — covers RA's 3-stage report-deletion redesign + takedown-request queue
+ per-char rich markup on heading / table / comparison cells. 6 new MCP tools
(_DISPATCH 76 → 82), 3 adapter passthrough additions, fully backward-compatible.

Added — 6 new MCP tools (and matching CLI commands):

Soft delete (RA dc8bd45 + ff64778) — replaces ad-hoc DELETE:

- `report_trash` — POST /api/reports/{id}/trash. Move to trash (soft delete).
  Blocked while the report is mounted to any board; ask the user to unmount
  or file a takedown request first.
- `report_restore` — POST /api/reports/{id}/restore. Recover from trash.

Takedown requests (RA 3e92860) — non-owner-mediated unmount queue:

- `report_takedown_request` — POST /api/reports/{id}/takedown-requests.
  Owner asks the board manager to unmount the report from a specific board
  (workspace_slug + optional reason).
- `takedowns_list` — GET /api/takedown-requests. Manager / sys-admin view.
  Filter by workspace_slug + status (pending | approved | rejected).
- `takedown_approve` — POST /api/takedown-requests/{id}/approve. Unmounts
  and closes the request.
- `takedown_reject` — POST /api/takedown-requests/{id}/reject. Leaves the
  mount in place; reason surfaces to the requester.

Added — adapter passthrough for new content fields:

- `heading.py` `_PASSTHROUGH` gains `text_html` (RA a97d5b5). Plain `text`
  is kept as the TOC/export title; `text_html` carries per-char color and
  format (same sanitized HTML grammar as caption_html).
- `table.py` `_PASSTHROUGH` gains `cell_html` (RA 7976ff7). Side-table
  keyed by `"rowKey::columnKey"` (same key shape as cell_styles); values =
  sanitized HTML per cell.
- `comparison.py` `_PASSTHROUGH_SIMPLE` gains `cell_html` (RA d62af9d). Same
  shape, keyed by `"rowKey::caseKey"`.

Added — client wrappers (6 new sync methods, all with module-level INFO
logging at write entry):

- `client.trash_report(report_id)` / `restore_report(report_id)`.
- `client.request_report_takedown(report_id, *, workspace_slug, reason=None)`.
- `client.list_takedown_requests(*, workspace_slug=None, status=None)`.
- `client.approve_takedown_request(request_id)`.
- `client.reject_takedown_request(request_id, *, reason=None)`.

CLI:

- `report trash <id>` / `report restore <id>` / `report takedown-request <id>
  --workspace SLUG [--reason TXT]`.
- `tools takedowns-list [--workspace SLUG] [--status STATUS]`.
- `tools takedown-approve <request-id>` / `tools takedown-reject <request-id>
  [--reason TXT]`.

Skipped — RA d7cdd4d (password recovery) covered in v0.9.0; RA bd7c418 is
frontend-only DOCX export styling; RA workspace-navigation polish (6ecf4ee
etc.) is pure frontend UX.

SKILL.md:

- New "v0.10.0 — soft delete + takedown queue + per-cell rich markup" section
  documents the 6 new tools, the mount-blocked semantics on `report_trash`,
  the takedown request → approve/reject lifecycle, and the heading
  `text_html` + table/comparison `cell_html` passthrough.
- Tool inventory header bumped to "MCP tool inventory (v0.10.x)" + 82 tools.

Tests — pytest 459 → 465 (+6):

- `tests/test_dispatch_parametrized.py` `_ARGS_BY_TOOL` gains 6 entries
  + fake-client mock returns for the 6 new methods.
- `tests/test_mcp_roundtrip.py::test_dispatch_count_is_82` +
  `tests/test_widget_relations.py::test_dispatch_count_is_82` — renamed and
  bumped 76 → 82 to lock the new surface size.

Verified:

- pytest 465 passed, 5 skipped (no failures).
- MCP `_DISPATCH` count = 82.
- All 6 new client methods importable.
- `report-skill --version` reports 0.10.0.

## 0.9.2 — 2026-06-09

Patch — closes the v0.9.1 coverage gap. v0.9.1 patched 24 widget adapters
that follow the `_PASSTHROUGH` tuple pattern but missed 8 adapters with
inline normalize() implementations: attachment, equation, flowchart,
key_value, quadrant, raci_matrix, rich_text, video. All 8 widgets accept
`caption_color` + `caption_html` per RA defcb74; v0.9.2 wires them through.

Fixed (high) — 8 remaining adapters caption_color/caption_html passthrough:

- `attachment.py` normalize() dict branch — caption color tokens after caption.
- `equation.py` normalize() dict branch — caption color tokens after caption.
- `flowchart.py` normalize() dict branch — caption color tokens after caption.
- `key_value.py` normalize() items-array path — caption color tokens after caption.
- `quadrant.py` normalize() dict branch — caption color tokens after caption.
- `raci_matrix.py` normalize() dict branch — caption color tokens after caption.
- `video.py` normalize() dict branch — caption color tokens after caption.
- `rich_text.py` normalize() dict branch — caption color tokens after caption_skip_autofill.

Each follows the same shape:

```python
if isinstance(raw.get("caption_color"), str):
    out["caption_color"] = raw["caption_color"]
if isinstance(raw.get("caption_html"), str):
    out["caption_html"] = raw["caption_html"][:2000]
```

Coverage check now passes — all 32 widget adapters that RA defcb74 added
caption_color to are wired in report-skill.

Verified:

- pytest 459 passed, 5 skipped (no failures).
- MCP `_DISPATCH` count = 76 (unchanged — patch only).
- `grep -l caption_color src/report_skill/adapters/*.py | wc -l` → 32.
  RA defcb74 affected 32 widgets; report-skill now covers all 32.
- `report-skill --version` reports 0.9.2.

## 0.9.1 — 2026-06-09

Patch — closes the v0.9.0 audit gap: defcb74 added `caption_color` /
`caption_html` (and `note_color` / `note_html` on `table` + `image`) to nearly
every widget content schema, but v0.9.0 only wired `cell_styles` on table /
comparison and left these new caption / note color fields silently dropped
across 24 widget adapters. Also corrects 3 SKILL.md inaccuracies the audit
caught.

Fixed (high) — caption / note color tokens reach the server:

- 24 adapter `_PASSTHROUGH` tuples gained `caption_color` + `caption_html`:
  box, bulleted_list, cad_3d, chart, comparison, contour, density, heatmap,
  html_embed, image, milestone, mind_map, network, packing, pie,
  progress_bar, radar, sankey, scatter, scatter3d, table, treemap, tree,
  waffle.
- `table` + `image` adapters additionally gained `note_color` + `note_html`
  (the only two widgets whose registry content also supports note color
  tokens).
- Round-trip safe: any caption / note color the user picks in the UI now
  survives a revise round-trip instead of being silently stripped on the
  next normalize.

Fixed (med) — SKILL.md inaccuracies:

- `cell_styles` example payload was `{"cells": [...]}` (wrong shape — table /
  comparison content uses `"rows"`, not `"cells"`). Corrected to `{"rows":
  [...], "cell_styles": {"r0::c1": {"bg": "amber", "fg": "ink"}}}`.
- Tool inventory header was still `MCP tool inventory (v0.8.x)` with
  `75 tools` after v0.9.0 shipped at 76. Updated to `(v0.9.x)` and 76.
- New section under the v0.9.0 widget styling note: the full 18 color-token
  enum (`ink / gray / slate / red / orange / amber / yellow / lime / green /
  teal / cyan / sky / blue / indigo / violet / purple / pink / rose`) is now
  listed verbatim with a "no shading suffixes" warning — `amber-50` /
  `ink-700` would be rejected by the server. The same enum drives
  `cell_styles.{bg,fg}`, `caption_color`, `note_color`, and the rich_text
  body's `color` mark.
- New paragraph explaining `caption_color` + `caption_html` are now widely
  available across the 24 affected widgets and that adapter passthrough is
  wired end-to-end.

Verified:

- pytest 459 passed, 5 skipped (no failures).
- MCP `_DISPATCH` count = 76 (unchanged — patch only, no new tools).
- All 24 adapter `_PASSTHROUGH` tuples include `caption_color` +
  `caption_html`; table + image include `note_color` + `note_html`.
- `report-skill --version` reports 0.9.1.
- Standalone install migrated to 0.9.1; stale 0.9.0 dist-info removed.

## 0.9.0 — 2026-06-09

Minor — covers 4 newly landed ReportArchive features on the widget surface:
text-color tokens (defcb74), `#widget` cross-references + inline font + caption
header alignment (074233d), password recovery (d7cdd4d — service-account no-op),
and per-cell bg/fg color tokens on table / comparison (c2d9663). MCP _DISPATCH
grows 75 → 76. Backward-compatible: existing tools unchanged.

Added — 1 new MCP tool + matching CLI command:

- `widget_ref_categories_list` — projection of `GET /api/widgets` → the new
  `ref_categories` field shipped by RA 074233d. Returns the ordered category
  metadata (`[{key, label}]`: 그림 / 표 / 비교표 / 키-값 / RACI / 수식 / 목록
  / 첨부 / 영상 / 임베드) the rich_text body uses for `#widget` cross-references.
  Numbers are derived at render time per `(page, id)` reading order, so the
  LLM only needs to know which categories exist when composing references.

Added — adapter passthrough for new widget content fields:

- `table.py` `_PASSTHROUGH` gains `cell_styles` — per-cell `{bg?, fg?}` color
  tokens keyed by `"rowKey::columnKey"`. v0.5.0/v0.5.2 cleanup notes preserved.
  Server validates via `_CELL_STYLES_SCHEMA` (RA c2d9663), so adapter passes
  the dict through unchanged.
- `comparison.py` `_PASSTHROUGH_SIMPLE` gains `cell_styles` — same shape but
  keyed by `"rowKey::caseKey"`.

Added — client wrapper + docstring update:

- `client.list_ref_categories()` — convenience projection of `fetch_widgets()`
  return shape to just the `ref_categories` list. v0.9.0+ (RA 074233d).
- `fetch_widgets()` docstring notes the new `ref_categories` field in the
  response envelope.

Skipped — RA d7cdd4d (password recovery + signup default host):

- The change adds `POST /users/forgot-password` and admin-mediated reset
  endpoints. `report-skill` authenticates as a long-lived service account
  whose JWT is configured via `.env` (`REPORT_API_EMAIL` /
  `REPORT_API_PASSWORD`), so password recovery is operator territory. No
  client / MCP / CLI wrapper added.

SKILL.md:

- New "v0.9.0 — widget styling + cross-references" section near the v0.8.0
  grants section documents cell color tokens on table / comparison, the
  `widget_ref_categories_list` tool, and the new rich_text inline FontFamily
  + text-color tokens (defcb74). Includes the per-widget category mapping rule
  (`REF_CATEGORY_BY_TYPE`) so the LLM knows table is the only widget under
  `"table"` and visual widgets share `"figure"`.

Tests — pytest 458 → 459 (+1):

- `tests/test_dispatch_parametrized.py`:
  - `_ARGS_BY_TOOL["widget_ref_categories_list"] = {}` so the dispatch sweep
    runs the new tool.
  - Fake client mock gains `list_ref_categories.return_value = []`.
- `tests/test_mcp_roundtrip.py::test_dispatch_count_is_76` + 
  `tests/test_widget_relations.py::test_dispatch_count_is_76` —
  renamed and bumped 75 → 76 to lock the new surface size.

Verified:

- pytest 459 passed, 5 skipped (no failures).
- MCP `_DISPATCH` count = 76.
- `widget_ref_categories_list` registered + dispatches via fake client.
- `cell_styles` passthrough confirmed via adapter unit tests (existing fixtures).
- `report-skill --version` reports 0.9.0.

## 0.8.3 — 2026-06-09

Patch — closes 1 high + 1 med leftover from the v0.8.2 audit.

Fixed (high) — manager edit-policy now exercised in the test suite:

- `tests/test_dispatch_parametrized.py` `_ARGS_BY_TOOL["report_mount_set_edit_policy"]`
  now uses `"manager"` (was `"coauthor"`). The previous value left the
  v0.8.1/v0.8.2 manager-policy code path completely untested through the
  parametrized dispatch sweep, so a regression on the manager dispatch
  would have slipped silently. The `manager` value flows through the
  schema enum check + dispatcher pass-through + client wrapper, matching
  the same shape the LLM will use.

Fixed (med) — CLI shares commands surface typed exceptions:

- `cli.py` imports `ShareSetupForbiddenError` + `BoardShareForbiddenError`.
- 6 `shares` add/remove commands (content-add, content-remove,
  folder-add, folder-remove, board-add, board-remove) now catch the
  matching typed exception BEFORE the generic `ApiError` and emit a
  human-readable `[share_setup_forbidden]` / `[board_share_forbidden]`
  message with a parenthetical hint about who actually has permission.
  Exit code 3 distinguishes typed-forbidden from generic HTTP error
  (exit 2), matching the convention used elsewhere in the CLI.

Verified:

- pytest 458 passed, 5 skipped (unchanged from v0.8.2).
- MCP `_DISPATCH` count = 75 (unchanged — patch only).
- `report-skill --version` reports 0.8.3.
- Standalone install migrated to 0.8.3; stale 0.8.2 dist-info removed.

## 0.8.2 — 2026-06-08

Patch — closes 4 high + 1 med leftover gap that v0.8.1's audit missed. The
critical one: v0.8.1 added the two grants typed exceptions to client.py but
did NOT register them in the `call_tool` typed-error map, so the LLM still
saw opaque `ApiError` envelopes — v0.8.1's headline feature was sliently
undelivered at the MCP surface, the same "builder receives but MCP doesn't"
asymmetry as v0.5.0 → v0.5.1.

Fixed (high):

- `mcp_server.py` defensive import block now also imports
  `ShareSetupForbiddenError` and `BoardShareForbiddenError`.
- `mcp_server.py` `_build_typed_error_map()` includes both grants subclasses
  in the dispatch table (before the 409 codes). `call_tool` now surfaces
  `{error: "share_setup_forbidden"}` / `{error: "board_share_forbidden"}`
  to the LLM, matching the rest of the typed-exception family.
- `mcp_server.py` `report_mount` tool schema enum gains `manager` (the
  policy was added to `report_mount_set_edit_policy` in v0.8.1 but the
  initial-mount schema still rejected it).
- `client.py` `set_mount_edit_policy` docstring now lists `manager` with
  the RA p27 auto-sync note (was missed by v0.8.1's high-priority sweep).
- `report_ops.py` `mount_report` docstring lists `manager` similarly.

Added (med) — SKILL.md error-code table:

- Two new rows `share_setup_forbidden` (403, owner / sys admin only) and
  `board_share_forbidden` (403, manager / sys admin only) so the LLM has a
  documented handling guide for each.

Tests — pytest 455 → 458 (+3):

- `tests/test_authorlocked_mapping.py`:
  - `test_build_typed_error_classifies_share_setup_forbidden` —
    `_build_typed_error` correctly classifies the Korean owner-gate string.
  - `test_build_typed_error_classifies_board_share_forbidden` — same for
    the board-manager-gate string.
  - `test_typed_error_map_includes_grants_classes` — locks the
    `_build_typed_error_map()` contents so the v0.8.1 omission cannot recur.

Verified:

- pytest 458 passed, 5 skipped.
- MCP `_DISPATCH` count = 75 (unchanged).
- `_TYPED_ERROR_MAP` contains both new codes (asserted by new test).
- `report-skill --version` reports 0.8.2.
- Standalone install migrated to 0.8.2; stale 0.8.1 dist-info removed.

## 0.8.1 — 2026-06-08

Patch — closes 4 high + 1 med gap left by v0.8.0's RA-grants rollout.

Fixed (high) — `manager` edit-policy value exposed end-to-end:

- `mcp_server.py` `report_mount_set_edit_policy` tool schema enum now includes
  `manager` (was missing — v0.8.0 documented the value in SKILL.md but the MCP
  schema still rejected it). Tool description updated with Korean meaning of
  each policy.
- `cli.py` `mounts set-edit-policy --policy` accepts `manager` (was rejected by
  hardcoded 3-tuple validation). Help text + invalid-value error text updated.
- `report_ops.py` `set_mount_edit_policy` docstring lists `manager` with the
  RA p27 auto-sync note.
- `SKILL.md` mount edit-policy section adds the 4th value with RA reference.

Fixed (med) — new 403 reasons typed:

- `client.py` adds two typed exceptions: `ShareSetupForbiddenError`
  (`공유 설정은 작성자(또는 시스템 관리자)만`) and `BoardShareForbiddenError`
  (`게시판 공유는 그 게시판 매니저(또는 시스템 관리자)만`). Both subclass
  `ApiError` (existing-handler-compatible). Detected in `_build_typed_error`
  before falling through to generic 403. LLM now sees structured
  `{error: "share_setup_forbidden"}` / `{error: "board_share_forbidden"}`
  instead of opaque 403.

Verified:

- pytest 455 passed, 5 skipped.
- MCP `_DISPATCH` count = 75 (unchanged).
- New exceptions importable from `report_skill.client`.
- `report-skill mounts set-edit-policy --help` shows 4 policy values.

## 0.8.0 — 2026-06-08

Minor — covers the ReportArchive "통합 grant 기반 공유/권한 체계" landed at RA
`dbdbf99` + `c6308ae` (2026-06-07). Adds 9 new MCP tools, 9 CLI commands, and a
SKILL.md section documenting the unified grant taxonomy and authorization rules.
No breaking changes to existing tools.

Added — unified grants / sharing surface (9 new MCP tools, _DISPATCH 66 → 75):

Content grants (reports + composites) — owner / sys admin only for writes:

- `content_shares_list` — GET `/api/{content_type}/{id}/shares`. content_type
  ∈ {reports, composites}.
- `content_share_add` — POST. Upsert a grant for a principal
  (workspace / workspace_manager / all_org / user). `level` ∈ {view, edit};
  all_org forces view + no principal_ref.
- `content_share_remove` — DELETE `/api/{content_type}/{id}/shares/{grant_id}`.

Folder grants (org folders only) — board manager / sys admin only for writes:

- `folder_shares_list`, `folder_share_add`, `folder_share_remove` — analogous
  to content grants, scoped to `/api/folders/{folder_id}/shares*`.

Board grants (org workspaces only) — board manager / sys admin only for writes:

- `board_shares_list`, `board_share_add`, `board_share_remove` — analogous,
  scoped to `/api/workspaces/{slug}/shares*`.

Added — `shares` Typer sub-app with 9 commands mirroring the MCP surface:
`content-list / content-add / content-remove`,
`folder-list / folder-add / folder-remove`,
`board-list / board-add / board-remove`.
Each write command wraps `ApiError` with a typed-exception-aware error path.

Added — `client.py` gains 9 thin wrappers:
`list_content_shares` / `add_content_share` / `remove_content_share`,
`list_folder_shares` / `add_folder_share` / `remove_folder_share`,
`list_board_shares` / `add_board_share` / `remove_board_share`. Each write
emits a module-level INFO log line before the HTTP call.

New principal taxonomy reflected in MCP tool schemas:

- `workspace_manager` — RA p27 enum addition. Used by mount edit-policy
  `manager` (작성자 + 게시판 매니저). Auto-synced by RA on `report_mount_set_edit_policy`
  policy changes.

Tests — pytest 446 → 455 (+9):

- `tests/test_dispatch_parametrized.py` `_ARGS_BY_TOOL` extended with 9 grant
  entries; fake client gains mock returns for the 9 new methods.
- `tests/test_mcp_roundtrip.py::test_dispatch_count_is_75` / 
  `tests/test_widget_relations.py::test_dispatch_count_is_75` renamed + bumped
  to 75 to lock the new surface size.

SKILL.md:

- New "v0.8.0 — unified grants / sharing" section documents the principal
  taxonomy table, 403 / 400 error reasons, mount-policy ↔ grant interaction,
  and the service-account ownership quirk.
- Tool inventory header updated to "MCP tool inventory (v0.8.x)" with 75-tool
  count.

Verified:

- pytest 455 passed, 5 skipped.
- MCP `_DISPATCH` count = 75 (was 66 in v0.7.1).
- All 9 share methods importable from `report_skill.client.ReportArchiveClient`.
- `report-skill shares --help` displays the 9 new commands.

## 0.7.1 — 2026-06-07

Patch — robustness hardening only (no new features, no behavior change on success path).

Resource lifecycle:

- `ReportArchiveClient.__init__` now wraps the post-`httpx.Client()` block in a
  try/except that closes the underlying httpx client on partial-construction
  failure (previously a theoretical leak if any future init step raised).
- `ReportArchiveClient.close()` swallows shutdown errors with a debug log
  instead of propagating, so cleanup is never the cause of a visible failure.

Logging discipline:

- Module loggers (`logger = logging.getLogger(__name__)`) initialized in
  `client.py`, `mcp_server.py`, `report_ops.py`.
- `login()` now logs `login OK user_id=...` at INFO.
- 12 write dispatchers in `mcp_server.py` (report create/update/delete/mount/
  unmount/publish/unpublish, composite create/delete, preset create/delete,
  template_set_scope) emit an INFO line before the HTTP call, so write
  activity is auditable even if the call raises.
- 4 write methods in `report_ops.py` (update_blocks, add_page, replace_page,
  delete_report) emit INFO at entry.
- The prior bare `logging.warning(...)` in `report_ops.py:244` is now
  `logger.warning(...)` for namespace consistency.

Input validation:

- New `_int_arg(args, key, *, default=None)` helper in `mcp_server.py` —
  raises `ValueError("argument '<key>' must be integer, got <type>: <repr>")`
  with the offending value, instead of the cryptic
  `ValueError: invalid literal for int()`. 46 `int(args["..."])` call sites
  across all dispatchers were routed through the helper.
- `cli.py` user-supplied `json.loads(...)` paths (6 sites) now catch
  `JSONDecodeError` and print `Invalid JSON in <source>: line N, col M: <msg>`
  via `typer.echo(..., err=True); raise typer.Exit(1)`.
- `cli.py` user-supplied file reads (6 sites) catch `FileNotFoundError` with a
  helpful message naming the offending option label.

Error message quality:

- `_do_report_delete` confirm gate: "confirm parameter must be true (boolean)
  to delete" (was "confirm must be true to actually delete").
- `_do_report_revise`: "must pass one of: block_ids (list of ids) or
  revise_all (true)" (was "either block_ids or revise_all is required").
- `composite_summary_set` widgets type check: now interpolates
  `type(value).__name__` so the caller sees `dict` / `str` instead of a
  generic "must be a list".
- `composite_items_set` items type check: same treatment.
- `report_ops` "no pages" guards now name the operation
  (`cannot update_blocks` / `cannot replace_page`).

Type hint tightening:

- `client.py` `get(...) -> Any` / `post(...) -> Any` → `-> dict[str, Any] |
  list[Any]`.
- 8 public methods (`publish_report`, `unpublish_report`,
  `fetch_report_lock_status`, `get_composite`, `accept_composite_request`,
  `reject_composite_request`, `withdraw_composite_request`,
  `delete_report`) gained `report_id: int` / `composite_id: int` /
  `request_id: int` annotations where they were previously untyped.

Retry discipline (confirmed correct, no change required):

- `report_ops.py` `edit_lock` acquire path classifies transient (5xx /
  status==0) vs non-transient (4xx) — only 5xx retries, 4xx raises
  immediately. Documented in code comment; no behavioral change.
- `append_to_blocks`, `update_blocks`, `add_page`, `replace_page` 409 retry
  loops all bounded by `max_retries + 1` with exponential backoff; verified
  correct.

Docs drift:

- `docs/RECEIVER.md`: stale "advertises 23 tools" → full 66-tool description
  with categories (reads / writes / milestones / maintenance) plus a one-line
  `_DISPATCH` count verification snippet.
- `docs/BUILDING.md`: aligned to current `_DISPATCH = 66`.
- `.env.example`: confirmed complete coverage of all settings used at runtime
  (`OLLAMA_*`, `SKILL_LLM_*`, `SKILL_AI_TIER`, `SKILL_BRIDGE_TIMEOUT`,
  `REPORT_API_*`, `REPORT_BACKEND_PATH`) — no missing variables.

Verified:

- pytest 446 passed, 5 skipped (no failures).
- MCP `_DISPATCH` count = 66 (unchanged).
- `report-skill --version` → 0.7.1.
- `report-skill-mcp` stdio handshake works (no regression in entry point).

## 0.7.0 — 2026-06-07

Minor — closes the Med + Low audit gaps from the v0.6.0 recheck. CLI parity,
SKILL.md inventory, new `widget_relations_list` resolver tool, MCP error semantics
correction, extended typed-exception + negative-path test coverage.

Added — 1 new MCP tool + matching CLI command (MCP `_DISPATCH` 65 → 66):

- `widget_relations_list` — `GET /widget-relations`. Returns the catalog of relation
  slugs that `rich_text` mention chips can target. Closes the LLM "must guess relation
  slug" gap that prior versions left as a future bucket item.

Added — CLI parity with v0.6.0 MCP surface:

- `composites create` gains `--two-col-view` flag and `--summary-widgets-file PATH`
  (reads JSON file, forwards as `summary_widgets`).
- `composites update` gains `--summary-widgets-file PATH`.

Added — workspace flag alias unification (8 call sites):

- `reports-search`, `report mount`, `report unmount`, `tools folders-list`,
  `tools preset-create`, `composites create`, `mounts set-folder`,
  `mounts set-edit-policy` — all accept both `--workspace` and `--workspace-slug`
  (the previously canonical `--workspace-slug` site at line 646 now also accepts
  `--workspace`).

Changed — MCP `call_tool` error semantics:

- All 6 error paths in `call_tool` (unknown-tool, typed-subclass dispatch, generic
  `ApiError` envelope, `ValueError`/`KeyError`/`IndexError`/`FileNotFoundError`,
  classified `RuntimeError`, final safety net) now `raise` with the JSON envelope
  as the exception message, so the MCP framework yields
  `CallToolResult.isError=True`. Success path unchanged.
- Breaking only for clients that parsed error JSON from the success-shaped text
  content; non-breaking for clients that respect MCP error semantics.
- The error JSON shape (`{error, status_code, …}`) is preserved verbatim — only
  the delivery channel changed.

Fixed:

- `CompositeRevisionConflict` docstring rewritten to accurately describe the RA
  global error envelope (`backend/app/shared/errors.py:49-54`) and the
  `code == "composite_revision_mismatch"` shape, instead of the prior inaccurate
  "FastAPI default detail" wording.

Tests — pytest 431 → 446 (+15 new):

- `tests/test_authorlocked_mapping.py` — 7 sibling tests covering each typed
  exception subclass (LockHeldByOther, LockNotHeld, RevisionMismatch,
  CompositeRevisionConflict, FinalizedReadOnly, NoEditPermission, OutOfWorkspaceScope).
  `_invoke_call_tool` helper updated to catch the new raised-error path.
- `tests/test_dispatch_parametrized.py` — 5 negative-path cases (missing required,
  invalid enum, type coercion failure) across representative tools.
- `tests/test_widget_relations.py` (NEW) — `widget_relations_list` dispatch test +
  `_DISPATCH` count pin at 66.

Verified:

- pytest 446 passed, 5 skipped.
- `_DISPATCH` count = 66 (65 + `widget_relations_list`).
- `report-skill tools widget-relations-list --help` displays.
- `report-skill composites create --help` shows new flags; both `--workspace` and
  `--workspace-slug` accepted everywhere.

## 0.6.1 — 2026-06-06

Patch — silent no-op fix.

`composite_update` no longer advertises a `group_name` parameter at the MCP / client
/ dispatcher layers. The backend `CompositeReportUpdate` schema
(`backend/app/modules/composites/schemas.py:336-350`) has no `group_name` field;
Pydantic v2's default `extra='ignore'` was silently dropping the value while
returning `200 OK`, so the LLM observed success when nothing changed.

Fixed:

- `mcp_server.py` composite_update tool schema — `group_name` property removed.
- `mcp_server.py` _do_composite_update dispatcher — `group_name` pass-through removed.
- `client.py` update_composite signature + body — `group_name` kwarg removed,
  docstring corrected.

Note: `items[].group_name` (per-item group label used by `composite_items_set`) is
unaffected — that field exists in the RA `CompositeItemIn` schema and continues to
work as documented.

## 0.6.0 — 2026-06-06

Surface expansion — full composite body editing, report activity timeline, and the
notification inbox. No breaking changes to existing tools. MCP _DISPATCH grows from
54 to 65 (11 new tools). Adds a typer CliRunner smoke suite over every new command.

Added — composites body editing (6 new MCP tools, 6 new CLI commands):
- composite_create (POST /api/composites) — create a recurring or theme composite,
  optionally seeded with items[] at creation time.
- composite_update (PATCH /api/composites/{id}) — top-level field editing with
  tri-state nullable semantics on period_date + group_name (omit = leave alone,
  null = clear, value = set). Supports expected_revision optimistic-concurrency
  guard (409 CompositeRevisionConflict on mismatch).
- composite_items_set (PATCH /api/composites/{id} with items[]) — full agenda
  list replacement, order = position.
- composite_delete (DELETE /api/composites/{id}) — owner / sys admin only.
- composite_publish / composite_unpublish — owner only; recurring composites
  freeze/clear per-item snapshot_content on publish/unpublish. Idempotent.

Added — report activity timeline (1 new MCP tool, 1 new CLI command):
- report_activities (GET /api/reports/{id}/activities) — newest-first lifecycle /
  lock / edit / mount event stream. Cursor pagination via before_id. Public-only
  viewers receive an empty list per backend policy.

Added — notification inbox (4 new MCP tools, 4 new CLI commands):
- notifications_list (GET /api/notifications) — returns `{items, unread_count}`.
- notifications_unread_count (GET /api/notifications/unread-count) — single
  integer badge probe for polling loops.
- notification_mark_read (PATCH /api/notifications/{id}/read) — idempotent.
- notifications_mark_all_read (POST /api/notifications/mark-all-read) — returns
  rows-flipped count.

SKILL.md:
- Flow E expanded — full composite body editing workflow (create → seed items →
  update top-level → replace items → publish / unpublish → delete).
- Flow J added — "Verify side-effects" — walk report_activities after writes to
  confirm downstream notifications fired.
- Flow K added — "Reactive agent" — idiomatic polling loop combining the
  notifications inbox with the activity timeline for write-side verification.

Tests:
- Extended tests/test_mcp_roundtrip.py with 13 new test cases covering all 11
  new tools, plus a tri-state semantic lock for composite_update.period_date.
- NEW tests/test_cli_smoke.py — typer.testing.CliRunner suite over every new
  CLI command + help-text rendering smoke for all 11 sub-commands.
- tests/test_dispatch_parametrized.py extended — minimal-valid args for all 11
  new tools so the wall-to-wall coverage stays unbroken.

Verified: pytest 431 passed / 5 skipped; MCP _DISPATCH count 54 → 65.

## 0.5.2 — 2026-06-06

Patch — closes 85 audit gaps from the v0.5.1 deep re-audit. Data-loss prevention + error
surface + adapter completeness + test coverage. No breaking changes.

Sections: Fixed (concurrency), Fixed (error surface), Fixed (adapter passthrough — 14 widgets),
Fixed (bundle/builder/lifecycle), Added (preset_create description, 6 CLI mirrors), SKILL.md
updates, Tests added.

Verified: MCP _DISPATCH stays 54; pytest grows by ~40 new test cases.

## 0.5.1 — 2026-06-06

Patch — closes 5 critical + 3 minor gaps left by v0.5.0 (the prior verify phase missed MCP-layer
regressions because pytest did not exercise the MCP dispatch path).

Fixed (critical):
- report_create / report_update MCP tools now expose 13 fields (3 related-info + 10 page-level)
  that v0.5.0 added to builder/ops but forgot at the MCP surface. additionalProperties:False
  had blocked any workaround.
- AuthorLockedError exception + report_lock_status MCP tool. client._request detects RA
  403 "작성자가 수정 잠금" Korean prefix and raises AuthorLockedError(reason=...).
  call_tool surfaces {"error":"author_locked", ...} instead of opaque 403.
- template_set_scope type fix — schema and dispatcher now accept template slug (string),
  not integer id. cli_templates set-scope subcommand added.
- list_composite_requests accepts status_filter kwarg (was MCP-only, client TypeError).
  cli composites-requests-list --status option added.
- add_report_link / report_add_link accept direction enum ("outgoing"|"incoming", default outgoing).

Fixed (minor):
- folders_list MCP no longer requires workspace_slug (RA allows omitted = personal folders).
- report_ops.update_blocks now warns when phase="finalized" is patched without using report_publish.
- CHANGELOG 0.5.0 typo: "Flow E" → "Flow H" (actual SKILL.md header).

Added (1 new MCP tool):
- report_lock_status — wraps GET /api/reports/{id} projection for author_lock_enabled,
  author_lock_reason, author_lock_set_at.

Tests:
- NEW tests/test_mcp_roundtrip.py — dispatches report_create/report_update via _DISPATCH
  and asserts all 13 new fields propagate to the builder; asserts template_set_scope accepts
  string slug; asserts list_composite_requests accepts status_filter without TypeError.

Verified:
- MCP _DISPATCH count 53 → 54.
- pytest stays green including new roundtrip tests.

## 0.5.0 — 2026-06-05

Closes the writer-side asymmetry left open by 0.4.0 and lifts the skill
up to feature-parity with the ReportArchive backend as of commit e99e7f8.
Six backend modules grew new surface between v0.4.0 ship and now —
report-level related-info, page-level rendering controls, table/image
notes + sizing, report copy/links/publish, presets, and composites — and
the skill could resolve mention ids for them (0.4.0) but could not
actually write or call them. 0.5.0 adds the writer half: 21 new MCP
tools, 13 new report-level field slots on create + update + bundle
round-trip, three adapter passthroughs, and matching CLI surface.

### New — Report-level related-info (closes v0.4.0 writer asymmetry)

  - `ReportCreate` / `ReportUpdate` payloads gain `collab_workspace_slugs`
    (list[str]), `entity_ids` (list[int]), and `report_type_id` (int).
    Same empty-list-clears-all semantics on update for the two list
    fields; `report_type_id` is plain Optional[int] with null=clear.
  - `report_builder.build_create_payload` and `build_create_payload_multi`
    accept the three fields as keyword-only kwargs; `report_ops.update_blocks`
    accepts the same three for PATCH.
  - `bundle.ready_for_recreate` round-trips `collab_workspace_slugs`
    verbatim and projects the fetched `ReportRead.entities` list (objects
    with `id`) down to a flat `entity_ids` list on the way out, so a
    dump→import cycle preserves entity tags.

### New — Page-level rendering controls

10 new Optional kwargs on `build_create_payload` /
`build_create_payload_multi` / `update_blocks`, all stored at the report
top level (not per-page):

  - `page_width_px` (320..3000), `page_gap_px` (0..200)
  - `page_blend_blocks` (bool), `page_slide_guide` (bool)
  - `page_slide_ratio` (`16:9` | `4:3` | `16:10` | `custom`)
  - `page_slide_ratio_custom_w`, `page_slide_ratio_custom_h` (1..10000)
  - `page_rich_text_prefix_d0` / `_d1` / `_d2` (each max 8 chars)

`bundle._TOP_KEEP` extended so all ten fields plus `report_type_id`
round-trip through dump/import.

### New — Adapter passthroughs (closes silent revise round-trip loss)

  - `adapters/table.py` — dict input now passes through `note`,
    `column_widths` (dict[str,int]), `table_width_px` (int), and `merges`
    (list of `{r,c,rs,cs}` cell-span objects).
  - `adapters/comparison.py` — same four fields plus `row_label_width`
    (int) for the left label column width.
  - `adapters/image.py` — adds `note` passthrough alongside existing
    files/caption/aspect_ratio/max_count handling.
  - All three reuse a shared `_clean_note(s)` helper that strips a
    leading `※` (with or without trailing space) — the renderer adds the
    prefix itself — and truncates to 1000 chars.
  - `prompt._WIDGET_INPUT_HINTS` gains entries for `table`, `image`, and
    `comparison` so every prompt builder advertises the new fields to
    the LLM, with the explicit "do not include the leading ※" rule.

### New — Report copy + report links + publish

  - `client.copy_report(report_id, *, title, mode='full', folder_id=None)`
    → POST `/reports/{id}/copy`. Mode is `content` (blocks only) or
    `full` (default — content + extras). New report lands in the actor's
    personal workspace.
  - `client.add_report_link(report_id, *, to_report_id, kind='related',
    label=None)` → POST `/reports/{id}/links`. SDK supplies the default
    kind since the backend has no default.
  - `client.publish_report(id)` / `client.unpublish_report(id)` — POST
    `/reports/{id}/publish` and `/unpublish`. Owner-only on the server;
    publish fires the `phase_to_finalized` activity + fans notifications
    out to every mounted-board member. Distinct from `report mount`
    (post-to-board); see SKILL.md Flow G alias note.
  - CLI: `report copy ID --title TXT [--mode content|full] [--folder-id INT]`,
    `report publish ID`, `report unpublish ID`.
  - MCP: `report_copy`, `report_add_link`, `report_publish`,
    `report_unpublish`.
  - Author-lock surfacing: `ReportRead.author_lock_enabled` /
    `author_lock_reason` / `author_lock_set_at` are projected through
    `fetch_report`; a new `AuthorLocked` exception is raised when a 403
    response carries the literal Korean string `작성자가 수정 잠금
    상태입니다`. MCP `call_tool` returns `{"error":"author_locked",...}`
    instead of a generic ApiError.

### New — Presets module

Mirrors `/api/presets` (commit 6c77eba):

  - `client.list_presets(template_id=None)` → GET `/presets`.
  - `client.create_preset(report_id, *, name, owner_workspace_slugs=None)`
    → POST `/presets` (description defaults to `''` server-side; SDK
    maps `report_id` → `source_report_id`).
  - `client.new_report_from_preset(preset_id, *, title=None, folder_id=None)`
    → POST `/presets/{id}/new-report`.
  - `client.delete_preset(preset_id)` → DELETE `/presets/{id}` (returns
    `None` after discarding the `{deleted:true}` payload).
  - CLI: `report new-from-preset PRESET_ID [--title TXT] [--folder-id INT]`,
    `tools presets-list [--template-id INT]`.
  - MCP: `presets_list`, `preset_create`, `report_new_from_preset`,
    `preset_delete`.

### New — Composites module

Mirrors `/api/composites` and `/api/composites/{id}/requests` (commit
e99e7f8 added `summary_widgets`):

  - `client.get_composite(id)` → GET `/composites/{id}`.
  - `client.update_composite_summary(id, *, summary_widgets,
    expected_revision=None)` → PATCH `/composites/{id}` with the
    narrowed body. Same widget grammar as reports.
  - `client.list_submittable_composites(report_id)` → GET
    `/composites/submittable-for/{report_id}` (includes
    `already_item` / `already_pending` flags).
  - `client.list_composite_requests(composite_id)`,
    `client.submit_to_composite(composite_id, *, report_id, note=None)`,
    `client.accept_composite_request(...)`,
    `client.reject_composite_request(..., *, reason=None)` (backend
    currently ignores the reason — kwarg kept for forward-compat),
    `client.withdraw_composite_request(...)`.
  - CLI: `tools composites-submittable-for --report-id INT`,
    `tools composites-requests-list --composite-id INT`,
    `tools composites-submit --composite-id INT --report-id INT [--note TXT]`,
    `composites accept` / `reject` / `withdraw` (under a new
    `composites` sub-app).
  - MCP: `composite_get`, `composite_summary_set`,
    `composites_submittable_for`, `composites_requests_list`,
    `composites_submit`, `composites_request_accept`,
    `composites_request_reject`, `composites_request_withdraw`.

### New — Discovery + admin tools

  - `client.fetch_report_types()` → GET `/report-types` (unwraps
    `data.items`).
  - `client.list_folders(workspace_slug)` → GET
    `/folders?workspace_slug=` (returns items only; the GET may
    side-effect default folders into existence on first hit).
  - `client.set_mount_folder(report_id, workspace_slug, *, folder_id)` →
    PUT `/mounts/{rid}/{slug}/folder`. `folder_id=None` clears.
  - `client.set_mount_edit_policy(report_id, workspace_slug, *,
    edit_policy)` → PUT `/mounts/{rid}/{slug}/edit-policy`. Valid
    policies: `default`, `owner_only`, `coauthor`.
  - `client.set_template_scope(template_id, *, owner_workspace_slugs)` →
    PATCH `/templates/{id}/scope`. None/empty = 전사. Metadata-only — no
    version bump.
  - CLI: `tools report-types-list`, `tools folders-list --workspace SLUG`,
    `mounts set-folder` / `mounts set-edit-policy` (new `mounts`
    sub-app), `templates set-scope TEMPLATE_ID --workspace SLUG ...`.
  - MCP: `report_types_list`, `folders_list`,
    `report_mount_set_folder`, `report_mount_set_edit_policy`,
    `template_set_scope`.
  - `report_ops` gains `set_mount_folder` / `set_mount_edit_policy`
    helpers so the CLI/MCP layers share one call surface.

### SKILL.md additions

  - New A2.5 "Tag the report" section covering
    `collab_workspace_slugs` / `entity_ids` / `report_type_id` with the
    resolver chains (workspaces_list / entities_list / report_types_list)
    and write paths (create payload + update_blocks).
  - Per-widget input guidance rows for table / image / comparison call
    out the `note` field and the auto-rendered `※` prefix ("do not type
    it yourself").
  - Flow B patch shape note: the 10 page_* fields and the three
    related-info fields are patchable via `report update`.
  - Flow G alias note: PUBLISH vs MOUNT distinction — mount posts to a
    board (existing surface), publish flips the report to
    `phase=finalized` + writes a phase-change activity + fans
    notifications to every mounted-board member.
  - New A.0 "Check for presets" precursor flow (`presets_list` →
    `report_new_from_preset`) for cases where the report being created
    matches a known preset.
  - New Flow H "Submit to composite" (`composites_submittable_for` →
    `composites_submit`).
  - New "Report copy" mini-flow under Flow A.

### Why 0.5.0 (minor) not 0.4.1 (patch)

The release adds genuinely new capability surface — two whole new
backend modules (presets, composites) wired in end-to-end, 21 new MCP
tools, 13 new report-level field slots on create + update + bundle, and
a new domain exception (`AuthorLocked`). Backward-compatible throughout:
every new kwarg is Optional with the previous default behavior; existing
draft JSON without any of the new fields validates and round-trips
unchanged. Minor bump per semver: feature addition, no breakage.

### Verified

  - 21 new MCP tools land in `_DISPATCH` (32 + 21 = 53 total).
  - CLI gains two new sub-apps (`mounts`, `composites`) plus 17 new
    individual commands across `report`, `tools`, `templates`, and the
    two new sub-apps.
  - `bundle.ready_for_recreate` projects the 13 new top-level fields +
    `entities` → `entity_ids` rename — round-trip-safe on a real
    fetch→ready_for_recreate→create cycle.
  - Adapter dict-input passthrough preserves `note` / `column_widths` /
    `table_width_px` / `merges` (+ `row_label_width` on comparison) on
    table / comparison / image round-trips, and the `※` strip handles
    both attached (`※주석`) and spaced (`※ 주석`) forms.
  - pytest 280/280 stays green.

## 0.4.0 — 2026-06-05

LLM-authorable @mentions + four MCP resolver tools. After ReportArchive
shipped its `<a data-mention-*>` chip system for cross-references between
reports, departments, and tagged entities (frontend Tiptap `ReportLinkMark`
+ DOMPurify allowlist), the existing report-skill could neither **emit**
mentions through the rich_text adapter nor **resolve** the ids the LLM
would need to plug into them. 0.4.0 closes both halves of that gap.

### New: `mention://` markdown-link syntax in rich_text

The rich_text adapter recognizes a synthetic URL scheme on markdown
links and emits the exact `<a data-mention-*>` shape `ReportLinkMark`
expects. Three forms — pick by reference type:

  - `[label](mention://report/<int_id>?ws=<workspace_slug>)`
  - `[label](mention://dept/<workspace_slug>)`  (slug IS the id; no `?ws=`)
  - `[label](mention://entity/<int_id>?axis=<entity_type_slug>)`

The label is the visible chip text; the URL never reaches the DOM —
DOMPurify strips `href`, and navigation runs through the frontend's
delegated SPA click handler keyed off the `data-mention-*` attrs.
Mentions can nest inside emphasis (`**[X](mention://report/42?ws=dx)**`)
and `_md_inline_to_html`'s decoder was rewritten to a single recursive
pass so the nested case actually resolves (the prior recursion ran in a
fresh placeholders scope and leaked sentinel literals into the HTML).
Invalid ids (anything outside `^[A-Za-z0-9_-]+$`) silently degrade to
plain escaped markdown so the operator sees the mistake instead of a
broken anchor.

### New MCP tools (4)

  - **`reports_search`** — wraps `GET /api/reports/linkable`; ranks by
    title-exact > title-substring > owner/mount > recency. Filters:
    `q` / `workspace_slug` / `owner_name` / `mount_slug` / `date_from`
    / `date_to` / `limit` (≤50).
  - **`workspaces_list`** — wraps `GET /api/workspaces`; filters by
    `kind` (default `org` — personal leaks user names, virtual owns no
    data) + optional `q` substring on `name+slug`.
  - **`entity_types_list`** — wraps `GET /api/entity-types`; per-process
    cache so chained entity lookups hit the network once.
  - **`entities_list`** — wraps `GET /api/entities?type_id&q&include_deprecated&limit`;
    accepts either `axis` (resolved to `type_id` via cached
    `entity_types_list`) or `type_id` directly; type_id wins. Hard-caps
    `limit` at 200.

All four tools also expose CLI subcommands under `report-skill tools`:
`tools reports-search` / `tools workspaces-list` / `tools entity-types-list`
/ `tools entities-list`.

### Prompt builder teaches the LLM the syntax

`prompt.py` gains a `_WIDGET_INPUT_HINTS` dict keyed by widget_type;
`_render_block_spec` appends the matching hint after the JSON-schema
section in every prompt that touches rich_text (covers
`build_single_block_prompt`, `build_batch_prompt`, `build_page_prompt`,
and `build_block_revise_prompt`). The hint explicitly lists the three
forms, requires resolver-tool ids ("NEVER invent ids"), and warns
against linkifying every team name (AI tell). `build_block_revise_prompt`
also gains an explicit rule: "Preserve every existing mention link
verbatim unless the user's instruction explicitly asks to change,
remove, or re-target that mention."

### SKILL.md additions

  - New top-of-file style note: "Cross-references use mention chips,
    not bare titles or raw URLs."
  - `Per-widget input guidance` rich_text row updated to advertise
    mention support.
  - New **A3.5 Mentions in rich_text** subsection with the three
    forms, the resolver chain (CLI + MCP), the rules, and Korean
    examples for all three types.
  - Flow B1 note: mentions preserved verbatim during revise unless
    the instruction explicitly targets them.
  - Flow D note: from-prompt auto-resolves via the MCP tools when run
    through bridge.

### Why 0.4.0 (minor) not 0.3.3 (patch)

This release introduces new capability surface — a new vocabulary the
rich_text adapter now produces but did not before, plus four new
MCP/CLI tools. Old draft JSON without mentions continues to validate
and round-trip unchanged. Old draft JSON with the experimental
`#mention:...` form (which never shipped) was never recognized and is
not affected. Minor bump per semver: backward-compatible feature
addition.

### Verified

  - 7/7 adapter sanity cases (single report mention with ws, dept pair,
    entity with axis, mention nested in bold, invalid-id fallback,
    mention-only paragraph promoted to html, plain text).
  - MCP `_DISPATCH` now has 32 tools (28 + 4); CLI `tools` group has
    four subcommands.
  - pytest 280/280 stays green.
  - Frontend DOMPurify allowlist in
    `<ReportArchive>/frontend/src/modules/templates/widgets/RichText.jsx`
    permits exactly the attrs the adapter emits — confirmed via Lens 1
    of the planning workflow.

## 0.3.2 — 2026-06-04

Cross-instance report portability via a self-contained `bundle.zip`.
The existing `report import payload.json` flow breaks for any report
with media (image / video / cad_3d / attachment) because `file_id` is
server-local — instance B has no record of instance A's `f_abc123`.
0.3.2 adds a bundle format that re-uploads the bytes on the target
side and swaps every reference in the payload.

### New: `report-skill report dump <id> -o bundle.zip`

Fetches the report, downloads every referenced file via
`GET /api/files/{file_id}` (the existing backend endpoint —
report-skill-only change), and packs them into:

```
bundle.zip
├── payload.json          # ReportCreate-ready (server fields stripped)
└── files/
    ├── manifest.json     # [{file_id, filename, mime_type, size}, ...]
    ├── f_abc123          # raw bytes, named by ORIGINAL file_id
    └── f_def456
```

`-o` is optional; defaults to `bundle-report-<id>.zip` in cwd.

### Updated: `report-skill report import <path>`

Auto-detects from the file extension:
  - `.zip` → unpack bundle, re-upload each file (gets a fresh
    file_id from the target server), swap every old file_id in the
    payload, POST `/reports`
  - `.json` → existing behavior (POST payload verbatim; assumes
    file_ids are valid on this server)

No new CLI command for import — the existing one auto-routes.

### New MCP tool: `report_dump` + updated `report_import`

`report_dump(report_id, out_path?)` → returns
`{report_id, title, pages, files, missing_files, bundle_size,
bundle_path}`. `report_import(payload_path)` now also accepts `.zip`
and returns `mode: "bundle"|"json"` so callers can tell which path
ran.

### Bundle internals (for future maintainers)

- `collect_file_ids(obj)` — recursive JSON-tree walk; handles dict
  values (`evidence.file_id`) and array elements (`attachment.files[i].file_id`).
- `swap_file_ids(obj, mapping)` — in-place swap; leaves ids not in
  the mapping unchanged so callers can audit "missing on import".
- `ready_for_recreate(report)` — strips server-managed fields
  (`id`, `owner_id`, `created_at`, `revision`, page-level `id` /
  `report_id` / `page_index`) so the fetched record is POST-ready.
- `pack_report_bundle` + `import_bundle` — top-level functions that
  the CLI and MCP both wrap.

### What still requires source files

Files the source server no longer holds (deleted or storage gone)
land in `missing_files` in the dump summary. Import skips their
`file_id` swap, so those blocks render with a broken file_id on the
target. The dump output flags them so the operator knows.

### Verified

- pytest 280/280 stays green.
- `collect_file_ids` walks nested attachment.files lists correctly.
- `swap_file_ids` preserves unmapped ids verbatim.
- `ready_for_recreate` drops `id`/`created_at`/`revision` while
  keeping `title` / `pages` / per-page `content` / `blocks_order` /
  `extra_blocks`.

## 0.3.1 — 2026-06-04

CR-11 fix — `blocks_order` is now maintained on the edit paths
(`report add-page`, `report update`, MCP `report_add_page`, MCP
`report_update`), not just on `report create`. Previously the CR-2 +
CR-8 auto-compute logic lived inside `build_create_payload_multi` and
nothing called it from the edit flows, so:

- new pages added via `add-page` shipped with no `blocks_order`, which
  the backend interprets as "show every template block" — and the
  receiver saw the empty `progress`/`issues`/`next_week` blank-box
  render that CR-2 was supposed to fix
- new extras added via `report update` got their content stored but
  never showed up in the rendered report because their ids were
  missing from the page's `blocks_order` (backend hides anything not
  listed)

### What changed

- New helpers in `report_builder.py`:
  - `compute_blocks_order(template, content, extras, explicit=None)` —
    extracted from the inline create-flow code so add-page can call
    the exact same logic. Honors `explicit` verbatim when set.
  - `merge_blocks_order(existing, add_ids)` — appends new ids that
    aren't already present, preserving the user's ordering. Used by
    `update_blocks` when new extras are introduced.
- `report_ops.add_page` now accepts `template` + `blocks_order`. When
  `template` is supplied (the CLI/MCP always passes the fetched template
  dict now), the new page's `blocks_order` is auto-computed via
  `compute_blocks_order`. Explicit `blocks_order` overrides.
- `report_ops.update_blocks` now accepts `blocks_order`. When set, it
  REPLACES the page's blocks_order verbatim. When unset and
  `add_extra_blocks` is supplied, new extra ids are auto-merged into
  the page's existing order at the end via `merge_blocks_order`.
- CLI `report add-page` passes the fetched template + `draft.blocks_order`
  to `add_page`. CLI `report update` passes `draft.blocks_order` to
  `update_blocks`.
- MCP `report_add_page` + `report_update` tools gain an optional
  `blocks_order` argument with the same semantics.

### Why this is a 0.3.1 (patch) not 0.4.0 (minor)

Pure correctness fix on existing surfaces. No new commands, no new MCP
tools, no breaking changes. The two new args (`blocks_order` on update
and add-page) are optional with backward-compatible defaults — old
callers continue to work, just now with correct render order.

### Verified

- pytest 280/280 stays green.
- compute_blocks_order: heading extras float to top + filled template
  blocks middle + non-heading extras bottom, explicit override honored.
- merge_blocks_order: appends new ids past the existing order, idempotent
  on duplicates.

## 0.3.0 — 2026-06-03

LLM-driven revision flow — closes the gap between v0.2.0's two halves
("LLM creates from raw text" + "user/Claude Code crafts a patch and
PATCHes"). Now a single command takes an existing report + a Korean/English
revision instruction and applies the change with full CR-1 safety.

### New: `report-skill report revise`

```
report-skill report revise <id> "<자연어 수정 지시>"
  (--block-ids X,Y | --all) [--page N] [--dry-run] [--max-tokens 800]
```

For each target block: fetch the current content, prompt the LLM
(Anthropic / OpenAI / Ollama / bridge) with `(current + instruction +
content_schema)`, validate the LLM's output against the schema, and
PATCH only the blocks whose content actually changed.

CR-1 `scoped_content` protection applies — blocks not listed in
`--block-ids` (and unchanged when `--all`) stay intact on the server.
When `--all` and the instruction only touches one block, the others
come back unchanged from the LLM and are silently skipped — no false
patches.

`--dry-run` prints the resulting patch JSON without POSTing, so the
caller can review (and feed into `report update -i` later, or just sanity
check) before committing.

### New MCP tool: `report_revise`

Same surface as the CLI command, exposed to Claude Desktop / Cursor /
Continue / any MCP client. Schema:

```json
{
  "report_id": 42,
  "instruction": "summary에 PostgreSQL 15 마이그레이션 결과 한 줄 추가",
  "block_ids": ["summary"],   // OR set revise_all=true
  "page_index": 0,
  "dry_run": false,
  "max_tokens": 800
}
```

Returns `{id, title, revision, patched_blocks, results, view_url}` on
success; `{id, dry_run: true, patch, results}` on dry-run; `{id, patched:
[], results, note: "no blocks changed"}` when the instruction didn't
apply to any of the target blocks.

### New prompt builder: `build_block_revise_prompt`

`prompt.py` gains a fourth builder alongside `build_single_block_prompt`
/ `build_batch_prompt` / `build_page_prompt`. The revise prompt explicitly
includes:

- the block's authoritative JSON-schema
- the current block content (so the LLM knows what to preserve)
- the user's verbatim revision instruction
- explicit instructions: "preserve every field the user did NOT ask to
  change", "if the request does not apply to THIS block, return the
  current content unchanged"

That last rule is what makes `--all` cheap and safe: blocks the
instruction doesn't touch return verbatim, get equality-checked against
the current state, and never reach the patch dict.

### `/report-write` slash command updated

Flow B now has two sub-flows:
- **B1 — LLM-driven revision** (preferred when the user described the
  change in prose): `report revise <id> "<지시>" --block-ids ...`
- **B2 — Manual patch** (when the user handed you exact JSON):
  `report update <id> -i patch.json` — the previous Flow B verbatim.

Claude Code now picks B1 by default for vague / prose revision requests
and B2 only when the user explicitly provided patch content.

### Verified status from earlier releases

- v0.1.0: CR-1 (data loss on update), CR-2 (blocks_order auto), key_value
  Korean keys — all stand.
- v0.2.0: CR-3/5 onedir+noupx, CR-6 Defender opt-in, CR-7 receiver doc,
  CR-8 heading floating, CR-9 __version__ single source, CR-10 --version
  flag + MCP version — all stand.
- 280/280 deterministic tests pass.

### Not pursued

- **CR-4 Authenticode code signing** — out of scope for this project.
  Sufficient for the intended internal distribution: CR-3 (onedir) +
  CR-5 (--noupx) already eliminate the practical AV issues (verified
  20/20 cold-start). The remaining SmartScreen "Unknown publisher"
  warning is a one-time-per-machine click for internal users. If a
  wider-audience release ever needs Authenticode, the cost (~$300/yr
  EV cert or ~$10/mo Azure Trusted Signing) and the matching one-line
  `signtool sign /fd SHA256 /tr <timestamp> ...` addition to
  `build_exe.ps1` are well-understood — but not warranted for the
  current scope.

## 0.2.0 — 2026-06-03

Second batch of field-reported fixes (`report-skill_수정요청서_2026-06-03.md`).
Addresses the intermittent .exe startup failures that blocked CLI use during
the v0.1.0 verification window, plus six smaller refinements.

### PyInstaller migration (root cause of "Access is denied" / no-output runs)

- **CR-3 — `--onefile` → `--onedir`.** Onefile bundles re-extract ~19 MB to
  `%TEMP%\_MEIxxxxx` on every launch, racing Defender's real-time scanner.
  About half of launches lost the race on the test machine, surfacing as
  silent no-ops or `Access is denied`. Onedir lays the runtime out once at
  install time so subsequent launches just exec the entry .exe — no
  per-run extract, no race. Two trees ship:
  `bin\report-skill\report-skill.exe` and `bin\report-skill-mcp\report-skill-mcp.exe`,
  each with a sibling `_internal\` directory.
- **CR-5 — `--noupx`.** PyInstaller auto-packs with UPX when it's on PATH;
  UPX'd binaries are an AV-heuristic magnet. Build now passes `--noupx`
  to both pyinstaller invocations regardless of host state.

### Distribution + installer

- **Installer copies directory trees, not single files.** `install-standalone.ps1`
  now copies the two onedir trees into `%LOCALAPPDATA%\report-skill\bin\`,
  hard-replacing any prior `_internal\` (PyInstaller hashes pyd/pyc names
  per build — merging would leave dangling stragglers). Both entry
  directories are appended to user PATH so `report-skill` and
  `report-skill-mcp` still work bare-name.
- **CR-6 — `-AddDefenderExclusion` opt-in switch.** Off by default. When
  passed (and the shell is elevated), the installer calls
  `Add-MpPreference -ExclusionPath $InstallDir`. Non-admin shell: prints a
  warning and continues. `setup.bat` forwards all args (`%*`) so
  `setup.bat -AddDefenderExclusion` works.
- **CR-7 — Receiver doc divergence.** `docs/RECEIVER-STANDALONE.md` now
  opens with a "Which variant should I install?" table:
  wheel preferred when Python is available, standalone otherwise. The
  AV-heavy environment row explicitly recommends the wheel + offers
  `-AddDefenderExclusion` as the standalone fallback.

### Render order

- **CR-8 — Heading extras float to top of `blocks_order` by default.** A
  `heading` widget added as an `extra_block` used to land at the bottom
  of the page (extras appended after template blocks). Now the auto-
  computed `blocks_order` partitions extras into `heading` vs
  `non-heading`: headings prepend, non-headings append. Explicit per-page
  `blocks_order` still wins. Relative order among headings (and among
  non-headings) is preserved from the input extras list.

### Version observability

- **CR-9 — Single source of truth for `__version__`.** Replaced the
  hardcoded `__version__ = "0.0.1"` in `src/report_skill/__init__.py` with
  `importlib.metadata.version("report-skill")`. Falls back to
  `"0.0.0+unknown"` when imported from a source tree without `pip install
  -e .`. No more drift between `pyproject.toml` and the runtime constant.
- **CR-10 — `--version` flag + MCP version surfacing.** `report-skill
  --version` now prints `report-skill X.Y.Z` and exits (Typer eager
  callback on the root app). The MCP `ping` tool now returns
  `{"status":"ok","version":"X.Y.Z","logged_in_as":...}` so clients can
  verify the deployed version without OOB checks.

### Not pursued

- **CR-4 — Authenticode code signing** — out of scope. Sufficient for
  the internal distribution this project targets: CR-3 (onedir) + CR-5
  (--noupx) already remove the practical AV issues. The remaining
  SmartScreen "Unknown publisher" warning is a one-time click per
  receiver. See the v0.3.0 "Not pursued" note for the rationale and the
  one-line signtool integration path if a future wider release ever
  needs it.

### Verified status from v0.1.0 (no action needed)

- CR-1 (`report update` data loss) — fix from 0.1.0 stands.
- CR-2 (empty template blocks rendered as blank boxes) — fix from 0.1.0
  stands.
- key_value Korean keys → items[] auto-conversion — fix from 0.1.0 stands.

## 0.1.0 — 2026-06-02

First receiver-ready release. Resolves the two field-reported data/render
bugs from `report-skill_수정요청서_2026-06-02.md` and removes all
dev-machine path leakage from receiver-facing surfaces.

### Bug fixes (data correctness)

- **CR-1 — `report update` data loss (High).** Partial patches no longer
  silently overwrite template blocks the user omitted. `block_patches` is
  now restricted to the block ids the user actually named in the draft
  file; everything else is left intact on the server.
  (`cli.py`, `report_ops.update_blocks` callsite.)
- **CR-2 — empty template blocks rendered as blank boxes (Medium).** The
  builder now sets `blocks_order` per page to the union of (template
  blocks the user filled, in template natural order) + (extras in input
  order). Backend hides any template block not in `blocks_order`, so the
  carrier-template + extra_blocks workflow renders cleanly. Explicit
  per-page `blocks_order` in the draft still wins.
  (`report_builder.build_create_payload_multi`.)
- **key_value silent fallback on Korean keys.** ASCII-only `to_slug()`
  dropped Korean keys entirely, raising `NormalizeError`, falling back
  to `rich_text`, and losing the labels. The adapter now redirects any
  Korean / non-ASCII / reserved key to the items[] form so labels render
  as the literal Korean text. Mixed input emits a unified items[] list.
  (`adapters/key_value.py`.)
- **Stale REPORT_SKILL_ENV footgun.** Running the installer with a
  custom `-InstallDir` (eg a temp sandbox) used to clobber the
  User-scope `REPORT_SKILL_ENV` env var that pointed at the real
  install, leaving the user's actual binaries unreachable. Installer
  now only touches User scope when InstallDir is the default
  `%LOCALAPPDATA%\report-skill`. (`install-standalone.ps1`.)

### Receiver-readiness

- **41 dev-path edits across 12 files.** Removed every `cd d:\report-skill;
  .\venv\Scripts\report-skill.exe` prefix in the three shipped slash
  command markdowns and replaced with bare `report-skill` (the installer
  puts it on user PATH). Same treatment for INSTALL_SKILLS.md, README,
  MCP server config example, the bridge cache paths in
  `bridge-process.md`, and the `REPORT_BACKEND_PATH` default in
  `config.py` / `.env.example`. Author name `박국진` in a sample payload
  replaced with `홍길동`. Snapshot `api_base_url` scrubbed.
- **`setup.bat` double-click installer.** ASCII-only batch wrapper that
  invokes `install-standalone.ps1` with `-ExecutionPolicy Bypass` and
  pauses at the end. Non-technical receivers no longer need to know
  PowerShell.
- **Three-field interactive setup with current-value defaults.** Running
  `setup.bat` against an existing install re-prompts for server URL /
  email / password; pressing Enter keeps the current value. No silent
  skip, no clobbered passwords. Skips the write entirely if nothing
  changed.
- **Slash command directory format.** `~/.claude/skills/<name>.md` (flat)
  is not how Claude Code loads skills — it requires
  `~/.claude/skills/<name>/SKILL.md` (directory). Restructured source
  and installer; legacy flat `.md` drops auto-migrate to backup.
- **UTF-8 BOM on PS1 + SKILL.md.** Windows PowerShell 5.1 reads BOM-less
  .ps1 as cp949, mojibake-ing Korean string literals at parse time.
  Added BOM to all shipped scripts and SKILL.md files.
- **PYTHONIOENCODING=utf-8 set by installer.** Belt-and-suspenders for
  pipe / redirect contexts where `sys.stdout.reconfigure()` silently
  fails.
- **Lazy `Settings()`.** `report-skill --help` no longer dumps a pydantic
  ValidationError when REPORT_API_PASSWORD is unset. Real config errors
  print a friendly diagnostic naming the `.env` path + missing vars.

### Build / distribution

- `setup.bat`, `RECEIVER-STANDALONE.md`, and the directory-format
  `.claude/skills/<name>/SKILL.md` tree all ship in
  `dist/report-skill-standalone-v0.1.0.zip`.
- `build_release.ps1 -ExeOnly` chains into `build_exe.ps1` for the
  standalone variant; `-WithExe` produces both wheel + standalone in
  one run.

## 0.0.1 — initial bootstrap

- 33 widget adapters, orchestrator with cross-widget fallback chain,
  template suggest + widget suggest, MCP server, Claude Code slash
  commands, offline export/import, edit-lock + optimistic concurrency,
  S/M/W tier policy, bundled widget catalog + template baseline.
