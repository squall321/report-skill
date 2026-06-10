# report-write reference — advanced flows

Referenced from SKILL.md. Everything beyond the core create / revise / mount path: presets, copy, template suggestion, from-prompt, add-page, append, composites (종합보고), grants/sharing, soft delete + takedown queue, activities, notifications.

## A.0 Check for presets first (skip A1–A3 when one fits)

Presets are saved snapshots of an existing report's tags, related-info, page settings, and block structure. If a matching preset exists, instantiating it is much cheaper than rebuilding the draft from a template — the new report inherits every field the preset captured.

```powershell
# list presets visible to the actor (전사 + own workspace tree)
report-skill tools presets-list

# optionally narrow to one template
report-skill tools presets-list --template-id <template-id>

# instantiate — title and folder_id are optional
report-skill report new-from-preset <preset-id> --title "5월 4주차 백엔드 주간보고" --folder-id 12
```

`new-from-preset` returns `{id, workspace_slug}` for the freshly created report (lands in the actor's personal workspace). Use this when:

- The user said "use the same setup as last week / 지난주랑 똑같이"
- A preset's name clearly matches the requested report (e.g. preset "백엔드 주간보고 — 표준 구성")
- The conversation already produced a similar report and the author asked to save it as a preset for next time (`preset_create` MCP tool — owner-only)

If no preset fits, fall through to template selection (SKILL.md Flow A).

### Worked example — preset flow (LLM call sequence)

```
1. presets_list                                 → returns [{id: 7, name: "백엔드 주간보고 — 표준 구성", ...}, ...]
2. choose preset id matching user request       → id = 7
3. report_new_from_preset(preset_id=7,
       title="5월 4주차 백엔드 주간보고",
       folder_id=null)                          → returns {id: 412, workspace_slug: "personal-23"}
4. (optional) report_update(report_id=412,
       patch={"tags": ["weekly", "backend"]})   → adjust tags / collab fields the preset did not pin
5. (optional) report_mount(report_id=412, workspaces=["dx"])   → Flow G to publish
```

The preset already carries `tags`, `entity_ids`, `collab_workspace_slugs`, `report_type_id`, lifecycle, page settings, and block scaffolding. Only override what the user explicitly asked to change.

## A.0b Copy an existing report (clone instead of recreate)

When the user says "report X 복사해서 새로 만들어줘" / "같은 구조로 새 보고서" and a specific source report is named, prefer `report copy` over re-running the draft pipeline:

```powershell
# full clone — tags, related-info, lifecycle, links, page settings all preserved
report-skill report copy <source-report-id> --title "5월 4주차 백엔드 주간보고"

# content-only clone — drops tags, report_type_id, entity_ids, lifecycle, links
report-skill report copy <source-report-id> --title "..." --mode content

# optionally land it in a specific personal folder
report-skill report copy <source-report-id> --title "..." --folder-id 7
```

Modes:

- `full` (default) — exact clone of every field the writer can set, including `collab_workspace_slugs`, `entity_ids`, `report_type_id`, lifecycle, and report-to-report links. Best when the user wants a sibling of the source.
- `content` — copies title/pages/blocks only; drops related-info, lifecycle, and links. Best when the user wants a clean starting point with the same body structure but different metadata.

The new report always lands in the author's personal workspace. Mount it (SKILL.md Flow G) to publish to a team board.

### Copy vs preset — when to use which

| situation                                                                       | use                       |
|---------------------------------------------------------------------------------|---------------------------|
| user names a specific source report ("같은 구조로 새 보고서", "X 복사해서")       | `report_copy` (A.0b)      |
| user names a saved configuration / template profile ("지난주랑 똑같이", "preset") | `report_new_from_preset` (A.0) |
| no specific source AND no preset exists                                         | fall through to template flow (SKILL.md Flow A) |
| user wants the body filled too (text, rows, images)                             | `report_copy` (body is cloned) |
| user wants only the SCAFFOLD (block ids, tags, page settings) — fresh body      | `report_new_from_preset`  |

Mnemonic: presets capture *configuration*, copy clones *content*. A preset is reusable across many reports; a copy is a one-off clone of one source.

## Flow E — when you don't know which template to use

This is the most common gap. If the user didn't say "use template X" AND you're not confident from context which fits:

### E1. Ask the recommender (keyword first, LLM if available)

```powershell
report-skill templates suggest "<one-line summary of what the user wants to capture>"
```

This prints a ranked list with `score`, `matched_keywords`, `confidence`, and (if LLM is configured) `llm_reasoning`. Pick:

- **confidence=high** — go with the top suggestion silently
- **confidence=medium** — show the user the top 2-3 and ask which (or proceed if there's clearly one best fit)
- **confidence=low** — tell the user "none of these match well; should I use a generic carrier (e.g. monthly-summary) and put everything in extra_blocks instead? Or pick: ..."

### E2. Or skip the picker entirely with `report from-prompt --auto`

```powershell
report-skill report from-prompt "<user's raw text>" --auto --with-extras --create
```

- `--auto`         — runs templates suggest internally; picks top if confidence≥medium, else aborts with suggestions
- `--with-extras`  — scans the text for chartable data / dates / hierarchies / etc. and auto-adds extra_blocks for visual widgets the template doesn't already cover
- `--create`       — POSTs after dry-run validates clean

Use this form when the user just wants the report to exist and trusts the skill to organize it.

## Flow D — FROM-PROMPT (internal LLM does the structuring)

When the user just dumps a chunk of text and wants the report assembled:

```powershell
report-skill report from-prompt "<raw notes here>" -t <template-id> --create
```

The skill's own LLM (auto-detected: Anthropic / OpenAI / Ollama / **Claude Code bridge** via env vars) generates each block via a schema-constrained prompt, normalize-and-validates, and POSTs. Omit `--create` for dry-run.

When run through the bridge provider, `from-prompt` will auto-resolve unambiguous references via the same MCP resolver tools (`reports_search` / `workspaces_list` / `entities_list`). Ambiguous references fall back to plain prose — no fake ids. To enable resolution inline, call the resolver tools first and feed the chosen ids into the prompt text using the mention syntax from `widgets.md`.

**Bridge mode** (uses the current Claude Code session as the LLM, no API key needed):
```powershell
$env:SKILL_LLM_PROVIDER = "bridge"
report-skill report from-prompt "<text>" --auto --with-extras --create
# CLI blocks — in this chat, user says "process bridge queue"
# /bridge-process skill kicks in, Claude generates responses, CLI continues
```

## Flow C — ADD a new page to an existing report

```powershell
report-skill report add-page <report-id> -t <new-template-id> -i <page-draft>.json --name "Page title"
```

The new page can use a DIFFERENT template than existing pages — multi-page reports legitimately mix layouts. Same draft shape as `report create`.

## Flow F — APPEND (incremental updates to existing blocks)

When the user wants to ADD an entry without replacing what's already there — e.g. "add a milestone for next week", "log another issue":

### F1. One-shot milestone shortcut (most common)

```powershell
# add one event
report-skill report milestone add <report-id> --date 2026-08-01 --label "프로덕션 안정화" --status pending

# remove by date (optionally narrow with --label)
report-skill report milestone remove <report-id> --date 2026-08-01 --label "프로덕션 안정화"
```

Auto-detects the milestone block on the page. Use `--block-id` to disambiguate when multiple milestone blocks exist. Add dedupes by (date, label) — duplicate adds are no-ops. Remove deletes all items matching the filter. (v0.13.0: `label` is required on remove — date alone is no longer accepted.)

### F2. Generic append for any block type

```powershell
report-skill report append <report-id> -i <appends>.json
```

Append JSON shape:
```json
{
  "blocks": {
    "milestones": {"items": [{"date": "2026-08-01", "label": "..."}]},
    "progress":   ["new bullet item"],
    "issues":     [{"issue": "...", "severity": "보통", "owner": "..."}]
  }
}
```

Per-widget merge semantics:
- `milestone` — append events; dedupe by (date, label); sort by date
- `bulleted_list` — append; dedupe by string
- `table` / `chart` / `scatter` — append rows
- `pie` / `waffle` / `treemap` — append; dedupe by label (last-wins)
- `key_value` — merge dict keys (new wins)
- `rich_text` — append as a new paragraph
- `raci_matrix` — merge by activity label; deep-merge per-role assignments
- `sankey` / `network` — append nodes + links/edges; dedupe
- `image` / `video` / `attachment` — append files; dedupe by file_id
- `heading` / `equation` / `html_embed` — replace (no append semantics)

### Concurrency — parallel append calls

The skill uses **optimistic locking** (`expected_revision`) to handle multiple processes writing to the same report simultaneously:
- Each append fetches the current `revision`, sends it back with the PATCH
- If the server's revision moved (someone else committed first), backend returns 409 `revision_mismatch`
- The skill auto-retries up to 3 times: re-fetch → re-merge with the latest state → re-PATCH
- `--max-retries N` adjusts the bound

Plus lock-acquire retries: if the backend lock table hits a transient 5xx under contention, the skill backs off with exponential jitter and retries up to 5 times. Verified safe under 5+ parallel processes writing to the same milestone block.

Limitation: when the backend genuinely can't serialize (rare), the loser raises `RevisionConflict`. The user should re-run.

## Flow H — Submit to composite (agenda)

Composite reports (종합보고) bundle multiple individual reports as agenda items for review meetings, monthly all-hands, etc. The author of an individual report can ASK a composite owner to include their report. Note: this section adds the **submit / withdraw** flow (which the report author uses). Accept / reject is composite-owner-only — separate ops not covered here.

When the user says "이 보고서 월간 종합에 올려줘 / agenda에 넣어줘 / submit to the all-hands":

### H1. Discover which composites accept this report

```powershell
report-skill tools composites-submittable-for --report-id <report-id>
```

Returns each visible composite with `already_item` (already accepted into the composite) and `already_pending` (a request is already open). Skip those — submitting again raises 409.

### H2. Submit the request

```powershell
report-skill tools composites-submit \
  --composite-id <composite-id> \
  --report-id <report-id> \
  --note "5월 백엔드 주간 — 결제 API 안정화 분"
```

The note is optional, max 1000 chars, and is shown to the composite owner when they review the queue. Returns the created request id and `status: "pending"`.

Result shape (composites_submit):

```json
{
  "id": 184,
  "composite_id": 27,
  "report_id": 412,
  "status": "pending",
  "submitted_at": "2026-06-05T09:14:22Z"
}
```

The composite owner then calls `composites_request_accept` / `_reject` and the request transitions to `accepted` / `rejected`. The author can `composites_request_withdraw` while it's still `pending`.

### H3. Withdraw a pending request (submitter-only)

If the author changes their mind before the owner decides:

```powershell
report-skill composites withdraw --composite-id <composite-id> --request-id <request-id>
```

`accept` / `reject` (composite owner-only) and `withdraw` (submitter, composite owner, or system admin) all use the `composites` sub-app — see `tools-inventory.md` § v0.5.0 Composites for the full list. To inspect existing requests:

```powershell
report-skill tools composites-requests-list --composite-id <composite-id>
```

Default filter is `pending` — pass `--status accepted` / `rejected` / `withdrawn` if needed (server-side filter).

## Flow E (expanded) — Build a composite from scratch (v0.6.0)

The v0.5.x surface only let you *submit* a report into someone else's composite and edit `summary_widgets`. v0.6.0 adds full body editing — you own the composite end-to-end. Use this when the user says "make a 5월 종합" / "build a recurring report from these N items":

### E.1 Create the empty composite

```powershell
report-skill composites create \
  --title "2026-05 백엔드 종합" \
  --kind recurring \
  --view-mode single \
  --period-date 2026-05-31
```

`--kind` is the `CompositeKind` enum value (typically `recurring` for periodic all-hands or `theme` for ad-hoc curated bundles). Pass `--workspace <slug>` to put it on a board you co-own; omit it to use the active workspace. Returns the new `id` + `revision: 1`.

You can also seed items at creation time with `--items-file items.json`:

```json
[
  {"ref_report_id": 412, "note": "결제 API 안정화"},
  {"ref_report_id": 415, "note": "인프라 마이그레이션", "display_column": 2}
]
```

(`display_column` is 1=left, 2=right — only meaningful when `view_mode=two_col`. `group_name` is an optional per-item grouping label rendered as a header in DOCX export.)

### E.2 Update top-level fields after the fact

```powershell
report-skill composites update <composite-id> \
  --title "renamed" \
  --view-mode two_col \
  --description "5월 백엔드 종합 — 분기 리뷰용" \
  --expected-revision 3
```

Only the flags you pass are sent. `--expected-revision` is optimistic-concurrency — if someone else edited the composite first, the server returns 409 `composite_revision_mismatch` and the CLI exits 4.

To clear `period_date` (not just leave it), pass `--period-date ""` — the empty string is the CLI signal for null.

### E.3 Replace the entire items list

```powershell
report-skill composites items-set <composite-id> \
  --items-file items.json \
  --expected-revision 3
```

`items.json` is the full replacement list (order = position). This is the canonical "edit the agenda" flow — fetch the composite, rewrite the JSON, push it back.

### E.4 Publish / unpublish

```powershell
report-skill composites publish <composite-id>
report-skill composites unpublish <composite-id>
```

Owner-only. For `kind=recurring`, publish freezes every item's content into `snapshot_content` so the composite renders the as-of-publish state even if source reports drift later. Unpublish clears snapshots + returns the composite to live + editable mode. Both are idempotent.

### E.5 Delete

```powershell
report-skill composites delete <composite-id> --yes
```

The `--yes` flag is required (destructive). Owner / sys admin only.

## v0.8.0 — unified grants / sharing

ReportArchive's prior ad-hoc sharing surface (mount edit-policy + collab workspaces) has been unified under a single grant model. Three resource taxonomies — content, folders, boards — each expose **list / add / remove**.

Principal taxonomy:

| `principal_type` | `principal_ref` | Notes |
|---|---|---|
| `workspace` | board slug | the board's members get the grant; inherited by descendant boards |
| `workspace_manager` | board slug | only that board's managers — used by mount edit-policy=`manager` |
| `user` | user id (string) | single-person grant |
| `all_org` | (omit) | force `level=view`, 전체 공개 |

Levels: `view` | `edit`.

Content (reports + composites) — owner / sys admin only for add/remove:

- `content_shares_list` — `{content_type: "reports"|"composites", content_id}` → GET `/api/{content_type}/{id}/shares`.
- `content_share_add` — `{content_type, content_id, principal_type, principal_ref?, level?}` → POST `/api/{content_type}/{id}/shares`. Upsert.
- `content_share_remove` — `{content_type, content_id, grant_id}` → DELETE `/api/{content_type}/{id}/shares/{grant_id}`.

Folders — board manager / sys admin only for add/remove. Only org folders are shareable:

- `folder_shares_list` — `{folder_id}` → GET `/api/folders/{id}/shares`.
- `folder_share_add` — `{folder_id, principal_type, principal_ref?, level?}` → POST.
- `folder_share_remove` — `{folder_id, grant_id}` → DELETE.

Boards (workspace) — board manager / sys admin only for add/remove. Only org boards are shareable:

- `board_shares_list` — `{workspace_slug}` → GET `/api/workspaces/{slug}/shares`.
- `board_share_add` — `{workspace_slug, principal_type, principal_ref?, level?}` → POST.
- `board_share_remove` — `{workspace_slug, grant_id}` → DELETE.

Mount edit-policy (`report_mount_set_edit_policy`) interaction: setting policy to `manager` causes the server to auto-create the matching `workspace_manager` grant on the report; switching back to `owner_only` / `default` removes it. Use `content_shares_list` to inspect the result after a policy change.

Authorization rules — common 403 reasons:

- `공유 설정은 작성자(또는 시스템 관리자)만 변경할 수 있습니다.` — `content_share_add/remove` from a non-owner non-admin.
- `게시판 공유는 그 게시판 매니저(또는 시스템 관리자)만 변경할 수 있습니다.` — `board_share_*` / `folder_share_*` without manager rights on the target board.
- `Out of scope` — viewer is outside the visible scope of the content.

400 errors: `조직 부서만 공유 대상이 될 수 있습니다.` (workspace ref must be an org board), `잘못된 사용자 id` (`user` ref not numeric).

Service-account quirk: `report-skill` typically authenticates as a service account, so most `content_share_*` calls fail unless the service account is the report owner. Use the calls primarily for read (`*_list`) and for boards/folders the service account manages.

## v0.10.0 — soft delete + takedown queue

ReportArchive shipped a 3-stage report-deletion redesign and a takedown-request queue so non-managers can ask a board manager to unmount a report they own. `report-skill` exposes the full surface as 6 new MCP tools (`_DISPATCH` 76 → 82).

**Soft delete (RA dc8bd45 + ff64778).** `DELETE /api/reports/{id}` is no longer the right way to delete a report. Use:

- `report_trash` — `POST /api/reports/{id}/trash`. Move to trash (soft delete). `report_trash` succeeds even while mounted — board copies are preserved (게시분 보존). It is `report_delete` (permanent purge) that is blocked with 409 `report_still_mounted` while any mount exists — see Recovery flow #5 in `errors-recovery.md`.
- `report_restore` — `POST /api/reports/{id}/restore`. Recover from trash. Returns the report with `deleted_at` cleared.

**Takedown requests (RA 3e92860).** When a board's mount edit-policy puts the manager (not the owner) in control of unmounting, the owner can no longer pull their own report off that board. The takedown queue is the recovery path:

- `report_takedown_request` — `POST /api/reports/{id}/takedown-requests`. Owner submits a request naming the board (`workspace_slug`) and optionally a `reason`.
- `takedowns_list` — `GET /api/takedown-requests`. Manager / sys-admin view. Filter by `workspace_slug` + `status` (`pending` | `approved` | `rejected`).
- `takedown_approve` — `POST /api/takedown-requests/{id}/approve`. Unmounts the report from the board and closes the request.
- `takedown_reject` — `POST /api/takedown-requests/{id}/reject`. Leaves the mount in place; the `reason` surfaces back to the requester.

## Flow J — Verify side-effects after a write (v0.6.0)

After any composite publish, report publish, mount toggle, author-lock change, etc., the backend emits a row in `report_activities`. Use this to confirm a write actually fired the downstream notifications:

```powershell
report-skill report activities <report-id> --limit 20
```

Returns newest-first. Each row carries `{id, actor, type, payload, created_at}`. Common types you'll see:

- `created` / `phase_to_finalized` / `phase_to_drafting` — lifecycle
- `locked` / `unlocked` / `lock_force_unset` — author-lock toggle
- `mount_added` / `mount_removed` — board publish
- `edit` — block-level update

Pagination is cursor-style: pass `--before-id <smallest-id-from-prev-page>` to walk back through history. The endpoint returns an empty list (not 403) for public-only viewers — that's intentional, so a reactive agent doesn't crash on a non-member call.

## Flow K — Reactive agent loop (v0.6.0)

When the user wants the skill to *react* to events rather than just author reports — "notify me when someone publishes to my board" / "auto-accept agenda requests from team X" / "mark all today's notifications read":

### K.1 Poll the inbox

```powershell
report-skill notifications list --unread-only --limit 50
```

Returns `{items, unread_count}`. Each item carries `{id, type, ref_table, ref_id, payload, actor, created_at, read_at}`. Type values include `composite_request_created`, `report_published`, `report_mention`, etc.

### K.2 Get just the badge

```powershell
report-skill notifications unread-count
```

Cheap single-integer probe — use this in a polling loop.

### K.3 Mark items read

```powershell
report-skill notifications mark-read <notification-id>
report-skill notifications mark-all-read
```

`mark-read` is idempotent. `mark-all-read` returns the count of rows it flipped — use the number to confirm progress in agent logs.

### K.4 Idiomatic agent shape

```text
loop forever:
  n = notifications unread-count
  if n == 0: sleep + continue
  items = notifications list --unread-only
  for item in items:
    if item.type == "composite_request_created" and policy_allows(item):
      composites accept --composite-id <id> --request-id <rid>
    notifications mark-read <item.id>
```

Combine with Flow J (`report activities`) for write-side verification: after `composites accept`, walk the source report's activity log to confirm the `phase_to_finalized` / `mount_added` event landed.
