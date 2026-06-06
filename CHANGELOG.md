# Changelog

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
