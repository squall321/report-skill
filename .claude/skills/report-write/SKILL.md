---
name: report-write
description: Create, update, publish, or extend a ReportArchive report from chat content. Use when the user wants to "write this up", "create a weekly report", "update the report", "add a page", "fix the issues block", "publish to dx", "mount onto the team board", or "/report-write". Covers CREATE (new report), UPDATE (patch blocks), ADD-PAGE (append page), APPEND (incremental items), MOUNT/UNMOUNT (publish to org boards), and FROM-PROMPT (internal LLM block generation).
---

# /report-write — chat content to ReportArchive report

You are orchestrating the `report-skill` CLI to publish, update, or extend ReportArchive reports. Every operation has two access paths: the CLI (`report-skill ...` — the installer puts `report-skill.exe` on PATH, invoke it bare from any cwd) and the same operations as snake_case MCP tools (`report_update`, `reports_search`, ...) from any MCP client — same backend, use whichever the session provides. Setup is assumed done: `report-skill ping` must succeed (else `.env` is wrong or the server is down — surface and stop) and `report-skill catalog list` should show ≥30 widgets (if "no snapshot", run `report-skill catalog sync` once).

## Quick start — the most common one-liner

"create a weekly report and publish to dx" (any one-shot create + publish pattern):

```powershell
report-skill report create -t weekly-dev -i draft.json --mount-to dx
```

That normalizes + validates the draft locally, POSTs to the server in `.env`, and auto-mounts to the `dx` board. Without `--mount-to`, the report lands in the user's personal workspace only.

## Personal workspace + mount semantics (READ FIRST)

Every `POST /reports` lands in the author's **personal workspace** (`personal-<user_id>`), regardless of `REPORT_API_WORKSPACE_SLUG` in `.env` (that env var is the *read/permission* context, not the create target). To make a report visible on a team board (`dx`, `qa`, `dept-mx`, ...), it must be **mounted** there:

| user intent | command |
|---|---|
| save to my personal space only | `report create -t ... -i ...` |
| save AND publish in one shot | `report create -t ... -i ... --mount-to dx` |
| publish an existing report | `report mount <id> -w dx` |
| publish to several boards | `report mount <id> -w dx -w qa -w dept-mx` |
| see where it's published | `report mounts <id>` |
| unpublish from one board | `report unmount <id> -w dx` |

`--edit-policy` on mount: `default` (author + board lead), `owner_only`, `coauthor` (every board member), `manager` (author + board managers; auto-syncs a `workspace_manager` grant, v0.8.0+).

Heuristic for `--mount-to`: user names a team/부서/보드 → mount to that slug; "weekly 보고" with no team → personal, offer to publish later; "send to the team / 공유 / 게시" → mount required.

## Pick the right mode

| user intent | where |
|---|---|
| "create a new report" | **Flow A** below |
| "fix the X block of report 42" / "더 간결하게" | **Flow B** below |
| "publish report X to dx" / "unpublish" / "발행" | **Flow G** below |
| "use the same setup as last week" (preset) / "clone report X" | Read `reference/flows-advanced.md` (A.0 / A.0b) |
| "I don't know what template fits" / "just give it raw text" | Read `reference/flows-advanced.md` (Flow E / D) |
| "add a milestone / append items" / "add a page" | Read `reference/flows-advanced.md` (Flow F / C) |
| 종합보고(composite), 공유(grants), 게시취소(takedown), 알림 | Read `reference/flows-advanced.md` |

## Flow A — CREATE

### A1. Pick a template

```powershell
report-skill templates list
```

Show templates with id + name. If only one matches the conversation topic, use it. Otherwise ask which. (No obvious fit → `templates suggest`, see `reference/flows-advanced.md`. A matching preset or a named source report to clone beats rebuilding — check `reference/flows-advanced.md` A.0/A.0b first when the user implies one.)

### A2. Inspect the template's blocks

```powershell
report-skill templates show <template-id>
```

You'll see each block's id + widget type + key props. The id is the key for the draft; the type tells you what shape the input should take.

### A2.5 Tag the report — related-info metadata (top-level fields)

Three top-level "related info" fields drive search facets, board filtering, and the "related reports" sidebar (distinct from free-form `tags` and from body mention chips):

| field | type | meaning |
|---|---|---|
| `collab_workspace_slugs` | `list[str]` | departments/workspaces named as collaborators |
| `entity_ids` | `list[int]` | tagged entity values (model name, customer, project, ...) |
| `report_type_id` | `int` | one canonical report-type taxonomy id (weekly, postmortem, RFC, ...) |

Resolve BEFORE building the draft — never invent ids; zero matches → drop the field:

```powershell
report-skill tools workspaces-list --q "dx" --kind org        # collab_workspace_slugs
report-skill tools entity-types-list                          # entity axes (cached)
report-skill tools entities-list --axis model_name --q "HFP-X1"   # entity_ids
report-skill tools report-types-list                          # report_type_id
```

Put the resolved values at the draft's top level (NOT inside `blocks`/`pages`). Empty-list semantics on UPDATE: `[]` CLEARS the list; omitting the key leaves it unchanged; `report_type_id: null` clears the type.

### A3. Build the draft JSON

Single-page shape:
```json
{
  "title": "5월 4주차 백엔드 주간보고",
  "report_date": "2026-05-26",
  "tags": ["weekly", "backend"],
  "collab_workspace_slugs": ["dx"],
  "entity_ids": [412],
  "report_type_id": 3,
  "blocks": {
    "<block_id>": "<anything-ish — Read reference/widgets.md for per-widget input shapes>"
  }
}
```

Multi-page shape:
```json
{
  "title": "...",
  "pages": [
    {"template_id": "weekly-dev", "name": "백엔드", "blocks": {...}},
    {"template_id": "weekly-dev", "name": "프론트엔드", "blocks": {...}}
  ]
}
```

Style rules for block content:

- **No markdown emphasis in rich_text.** The frontend renders the `markdown` field as plain text — `**bold**` shows as literal asterisks — and `**term**`-peppered prose is the #1 AI tell. Write clean analyst prose; for definition lists use a bulleted_list widget instead of bold-prefixed lines.
- **Cross-references are mention chips, not bare titles or raw URLs** — `[표시 문구](mention://report/<id>?ws=<slug>)` etc. Raw URLs are stripped by the DOMPurify allowlist. Full syntax, resolver chain, and chip-style rules: Read `reference/widgets.md`. Use chips sparingly — 1-2 per paragraph for genuine cross-references only.
- Widgets you don't know how to fill — just omit them. Orchestrator marks `skipped` and the block stays empty.

Write the draft to `.skill-cache\draft-<timestamp>.json` (the CLI resolves `.skill-cache` next to its `.env`, typically `%LOCALAPPDATA%\report-skill\.skill-cache\`).

### A4. Dry-run

```powershell
report-skill report draft -t <template-id> -i .skill-cache\draft-<timestamp>.json
```

(Omit `-t` if the draft has `pages` with per-page `template_id`.)

Read the block status table:
- `ok` / `repaired` — good
- `skipped` — block empty (user may want to add)
- `unsupported` — no adapter; fine to leave blank
- `failed` — schema validation failed
- `fell_back` — input auto-substituted to a simpler widget via `extra_blocks` (e.g. table → bulleted_list); look for the `<id>__fb` synthetic row right below.

### A5. Loop on failures

For each `failed`: show the user the block_id, widget type, and `detail`; ask for clarified content for just that block; patch the draft file; re-run `report draft`; repeat until clean.

### A6. POST

```powershell
report-skill report create -t <id> -i <draft>.json
```

Add `--allow-failures` only if the user explicitly opts in. Return the printed `view: <url>` line verbatim (do not invent the URL).

## Flow B — UPDATE an existing report

### B0. Read before you patch (v0.11 content-aware read surface)

`report_show` is structure-only (page count, block ids, widget types — no bodies). Do NOT author patches blind; call the read tools first:

| Tool | Use when | Returns |
|---|---|---|
| `report_outline` | "what's in this report?" | every page + block id + widget type + 1-line title preview (NO bodies) |
| `page_show_content` | "show me page N as-is" | every block on the page with current content (truncate=true caps each body at ~1000 chars) |
| `block_show` | "show me the `risks_table` block" | raw content + widget type + props + schema summary for one block |
| `block_preview` | "what does this block look like rendered?" | markdown / plain-text preview |

Typical edit sequence: `report_outline` → `page_show_content` or `block_show` (pin-point) → (optional) `block_preview` → author the patch with full context → `report_update` / `report_revise`. All four are read-only and safely precede any write.

### B1. LLM-driven revision (preferred when the user described the change in prose)

For "summary 블록 더 간결하게", "이슈에서 결제 API 항목 제거":

```powershell
report-skill report revise <report-id> "<자연어 수정 지시>" --block-ids summary,issues [--page 0]
report-skill report revise <report-id> "<지시>" --all [--page 0]      # let the LLM look at every filled block
report-skill report revise <report-id> "<지시>" --block-ids summary --dry-run   # preview, no POST
```

For each target block the CLI fetches current content, prompts the configured LLM with (current + instruction + schema), validates against the widget's content_schema, and PATCHes ONLY blocks whose content actually changed. CR-1 `scoped_content` protection: blocks not listed in `--block-ids` (and unchanged under `--all`) stay intact on the server.

Prefer `revise` for ambiguous prose; prefer `update` (B2) when the user handed you a specific draft to push.

### B2. Manual patch (exact JSON in hand)

```powershell
report-skill report update <report-id> -i <patch>.json [--page 0]
```

The patch file only needs the blocks you want to change:
```json
{
  "blocks": {"issues": [{...new rows...}]},
  "title": "...optional new title...",
  "phase": "reviewing",
  "lifecycle": "ongoing",
  "extra_blocks": [...optional ad-hoc additions...]
}
```

Phase enum: `drafting` (default) / `reviewing` / `finalized`. Lifecycle enum: `single_shot` (default) / `ongoing`. Legacy `status` alias auto-maps (`draft→drafting`, `in_progress→reviewing`, `completed→finalized`).

Other blocks are preserved. Edit lock is auto-acquired+released. Update REPLACES the block's content — for "add one more entry", use Flow F append (`reference/flows-advanced.md`) instead.

#### B2.1 Patchable top-level fields

All optional — omit a key to leave its current value untouched.

Related-info (resolver chain in A2.5): `report_type_id` (`int|null`, null clears), `entity_ids` (`list[int]`, `[]` clears), `collab_workspace_slugs` (`list[str]`, `[]` clears).

Page-level rendering controls (report-wide): `page_width_px` (320..3000), `page_gap_px` (0..200), `page_blend_blocks` (bool), `page_slide_guide` (bool), `page_slide_ratio` (`"16:9"|"4:3"|"16:10"|"custom"`), `page_slide_ratio_custom_w` / `_h` (1..10000, only with `"custom"`), `page_rich_text_prefix_d0/_d1/_d2` (bullet glyphs, max 8 chars; defaults `■` / `–` / `·`).

## Flow G — MOUNT / UNMOUNT / PUBLISH

```powershell
report-skill report mount <report-id> -w dx                       # publish to one board
report-skill report mount <report-id> -w dx -w qa -w dept-mx      # several boards in one call
report-skill report mount <report-id> -w dx --edit-policy coauthor --folder-id 7 --note "MX team review"
report-skill mounts set-note --report-id <id> --workspace dx --note "..."   # 게시 메모 변경 후속 ('' clears, v0.15.0)
report-skill report mounts <report-id>                            # list current mounts
report-skill report unmount <report-id> -w dx                     # board-manager-only, see below
```

Mount is idempotent (re-mounting is a no-op). Unmount removes visibility on that board but does NOT delete the report — the personal copy stays. Mounting a `drafting` report auto-advances `phase` to `reviewing` (one-way).

**Unmount is board-manager-only.** `report_unmount` works only if the actor is that board's manager (or sys admin). A mere owner gets 403 `mount_forbidden` — do not retry; file `report_takedown_request` naming the board instead, and the manager approves via `takedown_approve` (queue details: `reference/flows-advanced.md` § takedown).

### G.1 PUBLISH vs MOUNT (do not conflate)

- **MOUNT / UNMOUNT** — visibility on a team board; phase stays whatever it was. "공유해줘 / 게시해줘 / send to the team" → mount.
- **PUBLISH** (`report publish <id>`) — finalizes: `phase → finalized`, fans out notifications to every member of every mounted board. Author-only. "보고서 확정 / 발행 / 완료 처리 / 마감" → publish.
- "발행 취소 / 다시 작성중으로" → `report unpublish <id>` (phase back to `drafting`).

Both publish and unpublish are idempotent — re-calling on an already-final/drafting report is a no-op; notifications fire only on the actual transition.

## Hard rules (always apply)

- **Never invent ids.** Resolve via `reports_search` / `workspaces_list` / `entity_types_list` + `entities_list` / `report_types_list` / `folders_list`. Zero matches → drop the field or write plain prose; >1 plausible match → ask the user.
- **Cross-references are mention chips** (`mention://...`), never bare titles or raw URLs (stripped at render time).
- **`note` fields auto-prefix `※`** — never include a leading `※` in table/comparison/image note input.
- **Preserve fields you didn't change.** Never include `collab_workspace_slugs` / `entity_ids` / `report_type_id` / `page_*` in a patch unless the user's instruction targets that field — stale values silently overwrite.
- **Confirm gates on destructive tools.** `report_delete`, `composite_delete`, `preset_delete` require explicit `confirm=true` / `--yes`; always confirm with the user first.
- **Trash ≠ delete.** `report_trash` is soft delete — works even while mounted (board copies preserved), restorable via `report_restore`. `report_delete` is permanent purge — blocked with 409 while any mount exists.

## Error handling

Errors come back as typed `{error: <code>, ...}` payloads. The full table (21 codes) and the 5 worked recovery flows: Read `reference/errors-recovery.md`. The 5 most common:

- `revision_mismatch` (409) — concurrent PATCH; skill auto-retries (re-fetch + re-merge, default 3); if exhausted, re-read and re-apply your intent on top — never blind-overwrite.
- `author_locked` (403) — human-set lock; stop, surface the `reason`, do not retry (`report_lock_status` shows holder + expiry).
- `finalized_readonly` (403) — published report; ask the user, then `report_unpublish` → edit → `report_publish`.
- `mount_forbidden` (403) — non-manager tried a manager-only mount op (e.g. unmount); file `report_takedown_request` instead.
- `network_unreachable` — backend down, retryable; before re-issuing a create after a timeout, check `reports_search` to avoid duplicates.

## When to Read which reference file

| 상황 | 파일 |
|---|---|
| 위젯 입력 형식이 필요할 때 (per-widget shapes, mention:// 전체 스펙, #widget refs, color tokens, cell_styles/cell_html/text_html, 파일 업로드) | `reference/widgets.md` |
| 에러 코드 의미/복구 절차 (typed error 전체 표, Recovery flows #1–#5, lock 조회, lifecycle notes) | `reference/errors-recovery.md` |
| 도구 전체 목록/버전별 추가분 (v0.5.0~v0.15.0, 100-tool inventory) | `reference/tools-inventory.md` |
| 프리셋/복사/템플릿 추천/from-prompt/append/add-page/종합보고(+양식)/공유(grants)/게시취소(takedown)/알림/activities | `reference/flows-advanced.md` |
