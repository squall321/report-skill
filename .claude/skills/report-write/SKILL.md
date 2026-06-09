---
name: report-write
description: Create, update, publish, or extend a ReportArchive report from chat content. Use when the user wants to "write this up", "create a weekly report", "update the report", "add a page", "fix the issues block", "publish to dx", "mount onto the team board", or "/report-write". Covers CREATE (new report), UPDATE (patch blocks), ADD-PAGE (append page), APPEND (incremental items), MOUNT/UNMOUNT (publish to org boards), and FROM-PROMPT (internal LLM block generation).
---

# /report-write — chat content to ReportArchive report

You are orchestrating the `report-skill` CLI to publish, update, or extend ReportArchive reports. The installer (`setup.bat` / `install-standalone.ps1`) puts `report-skill.exe` on the user PATH at `%LOCALAPPDATA%\report-skill\bin\`, so invoke it as bare `report-skill` from any cwd.

## Quick start — the most common one-liner

If the user says "create a weekly report and publish to dx" (or any one-shot create + publish pattern), this is the answer:

```powershell
report-skill report create -t weekly-dev -i draft.json --mount-to dx
```

That single command (a) normalizes + validates the draft locally, (b) POSTs to the server URL in `.env`, (c) auto-mounts to the `dx` board so the team can see it. Without `--mount-to`, the report lands in the user's personal workspace only (see Personal workspace section).

## Prerequisites (once per machine)

- `report-skill ping` succeeds (auth + API reachability). If it fails, the user's `.env` is wrong or the server is down — surface and stop.
- The bundled widget catalog is sufficient for most ops; running `report-skill catalog list` should show ≥ 30 widgets. If it errors "no snapshot", run `report-skill catalog sync` once.

## Personal workspace + mount semantics (READ FIRST)

After the phase-6 backend migration, every `POST /reports` lands in the author's **personal workspace** (`personal-<user_id>`), regardless of `REPORT_API_WORKSPACE_SLUG` in `.env`. That env var is now the *read/permission* context, not the create target.

To make a report visible on a team board (eg. `dx`, `qa`, `dept-mx`), it must be **mounted** there. Two patterns:

| user intent | command |
|---|---|
| save to my personal space only | `report create -t ... -i ...` |
| save AND publish in one shot | `report create -t ... -i ... --mount-to dx` |
| publish an existing report | `report mount <id> -w dx` |
| publish to several boards | `report mount <id> -w dx -w qa -w dept-mx` |
| see where it's published | `report mounts <id>` |
| unpublish from one board | `report unmount <id> -w dx` |

`--edit-policy` options on mount:
- `default` — author + that board's lead can edit (Korean org default)
- `owner_only` — strictly the author
- `coauthor` — every board member can edit
- `manager` — author + that board's managers (auto-syncs a `workspace_manager` grant on the report; v0.8.0+, RA p27)

**Heuristic for picking** `--mount-to`:
- The user mentions a team / 부서 / 보드 (`dx`, `mx`, `qa`, etc) → use `--mount-to <slug>` matching the team
- The user says "weekly 보고", "정기 보고" without specifying a team → leave to personal; ask if they want to publish later
- The user says "send to the team" / "공유" / "게시" → mount required

## Pick the right mode

| user intent                                           | command                                                          |
|-------------------------------------------------------|------------------------------------------------------------------|
| "create a new report"                                 | **Flow A** below (`report create [--mount-to <slug>]`)           |
| "publish report X to dx" (already created)            | **Flow G** below (`report mount`)                                |
| "unpublish from dx"                                   | **Flow G** below (`report unmount`)                              |
| "fix the X block of report 42"                        | **Flow B** below (`report update`)                               |
| "add a milestone / append items"                      | **Flow F** below (`report milestone add` / `report append`)      |
| "add a page about Y to that report"                   | **Flow C** below (`report add-page`)                             |
| "just give it raw text, you figure it out"            | **Flow D** below (`report from-prompt`)                          |
| "I don't know what template fits"                     | **Flow E** below (`templates suggest` + `report from-prompt`)    |
| "use the same setup as last week (preset)"            | [**A.0 — presets**](#a0-check-for-presets-first-skip-a1a3-when-one-fits) |
| "clone an existing report as a starting point"        | [**A.0b — copy vs preset**](#a0b-copy-an-existing-report-clone-instead-of-recreate) |

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

## Flow A — CREATE

### A.0 Check for presets first (skip A1–A3 when one fits)

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

If no preset fits, fall through to A1.

#### Worked example — preset flow (LLM call sequence)

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

### A.0b Copy an existing report (clone instead of recreate)

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

The new report always lands in the author's personal workspace. Mount it (Flow G) to publish to a team board.

#### Copy vs preset — when to use which

| situation                                                                       | use                       |
|---------------------------------------------------------------------------------|---------------------------|
| user names a specific source report ("같은 구조로 새 보고서", "X 복사해서")       | `report_copy` (A.0b)      |
| user names a saved configuration / template profile ("지난주랑 똑같이", "preset") | `report_new_from_preset` (A.0) |
| no specific source AND no preset exists                                         | fall through to A1 (template) |
| user wants the body filled too (text, rows, images)                             | `report_copy` (body is cloned) |
| user wants only the SCAFFOLD (block ids, tags, page settings) — fresh body      | `report_new_from_preset`  |

Mnemonic: presets capture *configuration*, copy clones *content*. A preset is reusable across many reports; a copy is a one-off clone of one source.

### A1. Pick a template

```powershell
report-skill templates list
```

Show templates with id + name. If only one matches the conversation topic, use it. Otherwise ask which.

### A2. Inspect the template's blocks

```powershell
report-skill templates show <template-id>
```

You'll see each block's id + widget type + key props. The id is the key for the draft; the type tells you what shape the input should take.

### A2.5 Tag the report — related-info metadata (top-level fields)

ReportArchive stores three top-level "related info" fields on every report. These are distinct from `tags` (free-form strings shown on cards) and from body @mention chips (intra-paragraph cross-references). They drive search facets, filtering on team boards, and the "related reports" sidebar.

| field                     | type          | meaning                                                                                          |
|---------------------------|---------------|--------------------------------------------------------------------------------------------------|
| `collab_workspace_slugs`  | `list[str]`   | departments / workspaces named as collaborators on this report (chip row in header)              |
| `entity_ids`              | `list[int]`   | tagged entity values (model name, customer, project, etc.) — same axis grammar as `mention://entity` |
| `report_type_id`          | `int`         | one canonical report-type taxonomy id (e.g. "weekly", "incident postmortem", "RFC")              |

Rule: NEVER invent ids. Always resolve via the catalog tools before writing the draft. If a resolver returns zero matches, drop the field rather than guess.

Resolver chain — call BEFORE building the draft:

```powershell
# collab_workspace_slugs — list visible org/team workspaces
report-skill tools workspaces-list --q "dx" --kind org

# entity_ids — discover axes first (cached), then resolve values
report-skill tools entity-types-list
report-skill tools entities-list --axis model_name --q "HFP-X1"

# report_type_id — list the canonical taxonomy
report-skill tools report-types-list
```

Add the resolved values into the draft as top-level keys (NOT inside `blocks` and NOT inside `pages`):

```json
{
  "title": "5월 4주차 백엔드 주간보고",
  "report_date": "2026-05-26",
  "tags": ["weekly", "backend"],
  "collab_workspace_slugs": ["dx", "qa"],
  "entity_ids": [412, 87],
  "report_type_id": 3,
  "blocks": { ... }
}
```

Korean usage examples (one per field):

- `collab_workspace_slugs: ["dx", "qa"]` — "DX팀, QA팀과 공동 작성한 보고"
- `entity_ids: [412]` — "HFP-X1 모델 관련 회귀 테스트 결과"
- `report_type_id: 3` — "이번 건은 incident postmortem 으로 분류"

Empty-list semantics on UPDATE (Flow B): sending `"collab_workspace_slugs": []` or `"entity_ids": []` CLEARS all collaborators/entities. Omitting the key leaves them unchanged. `report_type_id: null` clears the type; omission leaves it.

### A3. Build the draft JSON

**Style note — rich_text content must NOT use markdown emphasis.**

The ReportArchive frontend's rich_text widget renders the `markdown` field as plain text — `**bold**` shows as literal asterisks, not bold. Also, heavy markdown formatting (`**term**` on every key phrase, `*italics*` peppered, `**Section:**` patterns inside body text) is the #1 visual tell for AI-generated content and makes reports look obviously machine-written.

When writing rich_text blocks: clean prose, the way a human analyst writes a paragraph. Reserve emphasis for genuinely critical phrases (≤1 per paragraph at most, and only when the reader truly needs the visual anchor). For lists with definitions, use a bulleted_list widget instead of bold-prefixed prose lines.

**Cross-references use mention chips, not bare titles or raw URLs.** When prose names another report, a department/workspace, or a tagged entity, use the markdown-link form with the synthetic `mention://` scheme (see A3.5 below). The frontend renders these as live chips with click navigation; raw titles do not link, and raw URLs are not allowed by the DOMPurify allowlist (the `href` is stripped at render time).



Single-page shape:
```json
{
  "title": "5월 4주차 백엔드 주간보고",
  "report_date": "2026-05-26",
  "tags": ["weekly", "backend"],
  "blocks": {
    "<block_id>": "<anything-ish — see widget guide below>"
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

Per-widget input guidance — adapters are tolerant:

| widget          | natural input shape                                                |
|-----------------|--------------------------------------------------------------------|
| heading         | `"제목 문자열"` or `{text, level?}`                                |
| rich_text       | markdown string; supports @-mentions (see A3.5 below)              |
| bulleted_list   | `["항목1", "항목2"]` or multi-line string with `-`/`*` bullets    |
| key_value       | flat dict `{"team": "백엔드", "lead": "..."}`                      |
| table           | list of dicts keyed by column.key, or markdown table string; dict form also accepts `note` (※-prefix auto), `column_widths`, `table_width_px`, `merges` |
| comparison      | dict-of-dicts `{"비용": {"as_is": "...", "to_be": "..."}}`; dict form also accepts `note` (※-prefix auto), `column_widths`, `row_label_width`, `table_width_px`, `merges` |
| chart/scatter   | `[{x: "Jan", revenue: 100}, ...]`                                  |
| pie/waffle      | dict label→value `{"북미": 40, "EMEA": 30}`                        |
| milestone       | dict date→label or list of `{date, label, status?}`                |
| flowchart       | list of step strings `["수집", "처리", "출력"]`                    |
| raci_matrix     | dict activity→{role: "R/A/C/I"}                                    |
| quadrant        | bucket `{q1:[...], q2:[...]}` or list of `{label, x, y}`           |
| tree/treemap    | nested dict `{"Tech": {"Eng": {...}}}`                             |
| sankey          | list of `{source, target, value}`                                  |
| equation        | `"E = mc^2"` (LaTeX, no $$ wrappers needed)                        |
| image/video/etc | requires `{file_id}`; image dict form also accepts `note` (※-prefix auto) |

Notes on `note` fields (table / comparison / image): the renderer automatically prepends a Korean `※` glyph to the rendered footnote. Do NOT include a leading `※` or `※ ` in the input — the adapter strips it. Max 1000 characters; longer text is truncated.

### A3.5 Mentions in rich_text

When a rich_text passage refers to another report, a department/workspace, or a tagged entity, use the markdown-link form with the synthetic `mention://` scheme so the frontend renders a live chip (with icon + click navigation) instead of a dead text fragment.

Three forms — pick by reference type:

- **Report**: `[표시 문구](mention://report/<int_id>?ws=<workspace_slug>)`
- **Department / workspace**: `[표시 문구](mention://dept/<workspace_slug>)`  ← path segment IS the slug; no `?ws=`
- **Entity** (tagged value like model name, customer): `[표시 문구](mention://entity/<int_id>?axis=<type_slug>)`

Resolver chain — call BEFORE writing the prose, never invent ids:

```powershell
# 1. Report id
report-skill tools reports-search --q "주간보고" --workspace-slug backend

# 2. Department slug
report-skill tools workspaces-list --q "dx" --kind org

# 3a. Discover available entity axes (cached after first call)
report-skill tools entity-types-list

# 3b. Resolve an entity value
report-skill tools entities-list --axis model_name --q "HFP-X1"
```

From an MCP client (Claude Desktop / Continue / Cursor) the four tools are exposed under the same names (`reports_search` / `workspaces_list` / `entity_types_list` / `entities_list`). Call them ONLY when an id is needed; if the user already typed an integer id, use it verbatim.

Rules:

- If `reports_search` returns >1 plausible match for the same fuzzy phrase, ASK the user to pick — do NOT silently choose the first row.
- If the resolver returns zero matches, drop the link and write the plain label as ordinary prose (no markdown-link with a fake id).
- For report mentions, `ws` is required for click-navigation; if `reports_search` returns a row with null/empty `workspace_slug`, treat it as un-mentionable.
- Department mentions use the workspace slug AS the id — never write `mention://dept/<int>` and never add a `?ws=` query string for dept.
- Entity mentions are display-only chips (no click navigation in v1) — use them when the cross-reference value is the message, not the destination.

Examples (Korean, all three types, real-feeling labels):

```
본 보고서는 [2026-W22 백엔드 주간보고](mention://report/137?ws=backend) 의 후속 분석이다.
검토는 [DX팀](mention://dept/dx) 과 [QA팀](mention://dept/qa) 이 공동 진행했다.
이번 회차 검증 대상: [HFP-X1](mention://entity/412?axis=model_name), 고객사 [현대모비스](mention://entity/87?axis=customer_name).
```

Style: reserve mention chips for GENUINE cross-references. Linkifying every team name in a status report (e.g. converting every appearance of "DX팀" into a chip) is an AI tell — same rationale as the `**bold**` warning above. One or two mentions per paragraph at most.

For widgets you don't know how to fill — just omit them. Orchestrator marks `skipped` and the block stays empty.

Write the draft to `.skill-cache\draft-<timestamp>.json` (the CLI resolves `.skill-cache` next to its `.env`, typically `%LOCALAPPDATA%\report-skill\.skill-cache\` on standalone installs).

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
- `fell_back` — original widget couldn't represent input; auto-substituted to a simpler widget via `extra_blocks` (e.g. table → bulleted_list). Look for a `<id>__fb` synthetic row right below — that's the fallback that did succeed.

### A5. Loop on failures

For each `failed`:
1. Show user the block_id, widget type, and `detail`
2. Ask user to clarify content for just that block
3. Patch the draft file, re-run `report draft`
4. Repeat until clean

### A6. POST

```powershell
report-skill report create -t <id> -i <draft>.json
```

Add `--allow-failures` only if the user explicitly opts in. Return the printed `view: <url>` line from the CLI output verbatim (do not invent the URL — the CLI prints the correct host for the receiver's deployment).

---

## Flow B — UPDATE an existing report

Two paths depending on whether the user gave you the new content verbatim
or asked you to *figure out* the new content from a natural-language
instruction.

### B1. LLM-driven revision (preferred when the user described the change in prose)

For "summary 블록 더 간결하게", "이슈에서 결제 API 항목 제거", "phase를 reviewing으로 + 다음 주 계획 한 줄 추가":

```powershell
report-skill report revise <report-id> "<자연어 수정 지시>" --block-ids summary,issues [--page 0]
# or to let the LLM look at every filled block:
report-skill report revise <report-id> "<지시>" --all [--page 0]
# preview the patch without POSTing:
report-skill report revise <report-id> "<지시>" --block-ids summary --dry-run
```

For each target block: the CLI fetches its current content, prompts the
configured LLM (Anthropic / OpenAI / Ollama / bridge) with `(current +
instruction + schema)`, validates the result against the widget's
content_schema, and PATCHes ONLY blocks whose content actually changed.
CR-1 `scoped_content` protection applies — blocks not listed in
`--block-ids` (and unchanged when `--all`) stay intact on the server.

When `--all` and the instruction only touches one block, the others come
back unchanged and are silently skipped — no false patches.

Prefer `revise` for ambiguous prose; prefer `update` (B2 below) when the
user gave you a specific draft to push.

### B2. Manual patch (when the user handed you exact JSON or you constructed it yourself)

For "fix the X block", "change the title", "update issues" with a concrete patch in hand:

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

Phase enum: `drafting` (default — 작성 중) / `reviewing` (리뷰 중) / `finalized` (발행 완료). Lifecycle enum: `single_shot` (default) / `ongoing`. The legacy `status` alias is still accepted but auto-maps (`draft→drafting`, `in_progress→reviewing`, `completed→finalized`).

Other blocks are preserved. Edit lock is auto-acquired+released. Update REPLACES the block's content. For "add one more entry to an existing block", use Flow F below instead.

#### B2.1 Patchable top-level fields (related-info + page settings)

The PATCH payload also accepts these top-level keys. All are optional — omit a key to leave its current value untouched.

Related-info (see A2.5 for resolver chain):

- `report_type_id` (`int | null`) — canonical type taxonomy. `null` clears.
- `entity_ids` (`list[int]`) — replacement set of tagged entities. `[]` clears all.
- `collab_workspace_slugs` (`list[str]`) — replacement set of collaborator departments. `[]` clears all.

Page-level rendering controls (apply to the report as a whole — top-level, not per-page):

- `page_width_px` (`int`, 320..3000) — canvas width in pixels
- `page_gap_px` (`int`, 0..200) — vertical gap between blocks
- `page_blend_blocks` (`bool`) — hide widget chrome / borders
- `page_slide_guide` (`bool`) — overlay slide-aspect guide
- `page_slide_ratio` (`"16:9" | "4:3" | "16:10" | "custom"`) — slide aspect enum
- `page_slide_ratio_custom_w` (`int`, 1..10000) — custom slide width (only used with `"custom"`)
- `page_slide_ratio_custom_h` (`int`, 1..10000) — custom slide height (only used with `"custom"`)
- `page_rich_text_prefix_d0` (`str`, max 8 chars) — depth-0 bullet glyph (default `■`)
- `page_rich_text_prefix_d1` (`str`, max 8 chars) — depth-1 bullet glyph (default `–`)
- `page_rich_text_prefix_d2` (`str`, max 8 chars) — depth-2+ bullet glyph (default `·`)

Rule — preserve existing related-info: when you author a PATCH from natural-language instructions (Flow B1 or B2), NEVER include `collab_workspace_slugs`, `entity_ids`, or `report_type_id` unless the user's instruction explicitly targets that field. Including them with stale values silently overwrites the report's tagging. The same caution applies to the `page_*` fields — only patch the ones the user asked to change.

## Flow G — MOUNT / UNMOUNT (publish to team boards)

```powershell
# publish to one board
report-skill report mount <report-id> -w dx

# publish to multiple boards in one call
report-skill report mount <report-id> -w dx -w qa -w dept-mx

# with edit policy + folder
report-skill report mount <report-id> -w dx --edit-policy coauthor --folder-id 7 --note "MX team review"

# list current mounts
report-skill report mounts <report-id>

# unpublish from one board
report-skill report unmount <report-id> -w dx
```

Mount is idempotent — re-mounting to a board that already has it is a no-op (reports "no new mounts created"). Unmount removes the visibility on that board but does NOT delete the report — the personal copy stays intact.

### G.1 PUBLISH vs MOUNT (do not conflate)

These are two distinct operations:

- **MOUNT / UNMOUNT** (this flow above) — posts the report onto a specific team board so that board's members can see it in their list. Pure visibility; phase stays whatever it was.
- **PUBLISH** (`report_publish` MCP tool / `report publish <id>` CLI) — finalizes the report. Transitions `phase` to `finalized` and fans out `report_phase_to_finalized` notifications to every member of every board the report is currently mounted on. Author-only operation.

Pick based on the user's intent:

- "공유해줘 / 게시해줘 / send to the team" → MOUNT to the relevant board(s)
- "보고서 확정 / 발행 / 완료 처리 / 마감" → PUBLISH
- "발행 취소 / 다시 작성중으로" → `report_unpublish` (sets `phase` back to `drafting`, records a `phase_to_drafting` activity)

PUBLISH is idempotent — calling it on an already-finalized report is a no-op that just returns current state. Likewise UNPUBLISH on a drafting report.

```powershell
# finalize the report and notify all mounted boards
report-skill report publish <report-id>

# revert to drafting
report-skill report unpublish <report-id>
```

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

`accept` / `reject` (composite owner-only) and `withdraw` (submitter, composite owner, or system admin) all use the `composites` sub-app — see [v0.5.0 — new MCP tools § Composites](#v050--new-mcp-tools) for the full list. To inspect existing requests:

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

## Flow F — APPEND (incremental updates to existing blocks)

When the user wants to ADD an entry without replacing what's already there — e.g. "add a milestone for next week", "log another issue":

### F1. One-shot milestone shortcut (most common)

```powershell
# add one event
report-skill report milestone add <report-id> --date 2026-08-01 --label "프로덕션 안정화" --status pending

# remove by date (optionally narrow with --label)
report-skill report milestone remove <report-id> --date 2026-08-01 --label "프로덕션 안정화"
```

Auto-detects the milestone block on the page. Use `--block-id` to disambiguate when multiple milestone blocks exist. Add dedupes by (date, label) — duplicate adds are no-ops. Remove deletes all items matching the filter.

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

## Flow C — ADD a new page to an existing report

```powershell
report-skill report add-page <report-id> -t <new-template-id> -i <page-draft>.json --name "Page title"
```

The new page can use a DIFFERENT template than existing pages — multi-page reports legitimately mix layouts. Same draft shape as `report create`.

## Flow D — FROM-PROMPT (internal LLM does the structuring)

When the user just dumps a chunk of text and wants the report assembled:

```powershell
report-skill report from-prompt "<raw notes here>" -t <template-id> --create
```

The skill's own LLM (auto-detected: Anthropic / OpenAI / Ollama / **Claude Code bridge** via env vars) generates each block via a schema-constrained prompt, normalize-and-validates, and POSTs. Omit `--create` for dry-run.

When run through the bridge provider, `from-prompt` will auto-resolve unambiguous references via the same MCP resolver tools (`reports_search` / `workspaces_list` / `entities_list`). Ambiguous references fall back to plain prose — no fake ids. To enable resolution inline, call the resolver tools first and feed the chosen ids into the prompt text using the mention syntax from A3.5.

**Bridge mode** (uses the current Claude Code session as the LLM, no API key needed):
```powershell
$env:SKILL_LLM_PROVIDER = "bridge"
report-skill report from-prompt "<text>" --auto --with-extras --create
# CLI blocks — in this chat, user says "process bridge queue"
# /bridge-process skill kicks in, Claude generates responses, CLI continues
```

## File uploads (for image/video/cad_3d widgets)

Media widgets require a `file_id`. Upload local files first:

```powershell
report-skill files upload <path>
# prints: file_id=abc123  ready to paste into draft
```

Then reference the `file_id` in the draft's media block.

## Sample draft

```json
{
  "title": "5월 4주차 백엔드 주간보고",
  "report_date": "2026-05-26",
  "tags": ["weekly", "backend"],
  "blocks": {
    "meta": {"team": "백엔드", "sprint": "Sprint-23", "lead": "홍길동"},
    "summary": "이번 주는 API 통합과 PostgreSQL 마이그레이션을 진행했다.",
    "progress": ["REST API 5개 추가", "단위 테스트 커버리지 85%"],
    "issues": [
      {"issue": "결제 API 지연", "severity": "높음", "owner": "김철수", "due": "2026-05-31"}
    ],
    "next_week": ["결제 API 통합", "캐시 레이어 최적화"]
  }
}
```

## v0.5.0 — new MCP tools

The following 21 MCP tools were added in 0.5.0. From any MCP client (Claude Desktop / Continue / Cursor) call them by name.

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

## v0.5.1 / v0.5.2 — author lock, typed errors, lifecycle notes

### report_lock_status — inspect the edit-lock holder

```
report_lock_status(report_id=<id>)
→ {
    "report_id": 412,
    "locked": true,
    "holder": {"user_id": 7, "user_name": "홍길동", "user_email": "hong@ex.com",
                "acquired_at": "2026-06-05T08:55:11Z", "expires_at": "2026-06-05T09:25:11Z"},
    "self_held": false,
    "reason": "weekly_review"
  }
```

Call before any write operation when the user mentions "잠겨있다 / 누가 편집 중" or after seeing an `author_locked` / `lock_held_by_other` error. When `self_held=true`, the current actor already owns the lock and can keep writing. When `locked=true` AND `self_held=false`, surface the holder name + expiry to the user and STOP — do not retry.

### AuthorLockedError surfacing

When the report's author has set a manual edit lock ("작성자가 수정 잠금 상태입니다") any write tool returns:

```json
{"error": "author_locked", "reason": "<lock 사유>", "report_id": 412}
```

LLM behaviour:

- Do NOT retry. The lock is intentional and human-set; immediate retry will fail identically.
- Tell the user the lock reason and that only the author (or a system admin force-unset) can release it.
- Offer to call `report_lock_status` to confirm the current holder + ETA, or to wait for unlock.
- For a different report, the lock is irrelevant — proceed normally.

### Typed error codes (v0.5.2)

The MCP server maps the backend's stable error signatures to typed `{error: <code>, ...}` payloads instead of generic `"API error"`. Use the table below to decide how to react:

| code                          | status | meaning                                                                                  | how the LLM should react                                                                                          |
|-------------------------------|--------|------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------|
| `author_locked`               | 403    | author has set a manual lock on the report                                               | stop, surface `reason` + `report_id`, do not retry; suggest `report_lock_status` or wait                          |
| `lock_held_by_other`          | 409    | another user is actively editing (system edit-lock, time-bound)                          | surface holder, wait or retry after expiry; do not force-unlock                                                   |
| `lock_not_held`               | 409    | tried to release/extend a lock you don't hold                                            | call `report_lock_status` to reconcile state; usually a stale client                                              |
| `revision_mismatch`           | 409    | someone PATCHed the report since you fetched it                                          | auto-retry up to `--max-retries` (default 3); the skill re-fetches + re-merges; on final failure tell the user    |
| `composite_revision_mismatch` | 409    | composite items[] was edited concurrently                                                | re-fetch composite via `composite_get`, re-build items list, retry                                                |
| `finalized_readonly`          | 403    | report is `phase=finalized` — body PATCH is blocked                                      | suggest `report_unpublish` first if the user really wants to edit; otherwise stop                                 |
| `no_edit_permission`          | 403    | actor is not the author / coauthor / board-default editor                                | stop and surface — mount edit-policy or coauthor list controls this; not retryable                                |
| `out_of_workspace_scope`      | 403    | actor's workspace tree does not cover the target report / composite                      | stop; resource is invisible to this actor — do not retry under a different workspace slug                         |
| `share_setup_forbidden`       | 403    | only the content owner / sys admin may add/remove a content-level grant (RA dbdbf99)     | stop and surface — `content_share_add` / `_remove` requires owner. service account usually cannot do this         |
| `board_share_forbidden`       | 403    | only the board manager / sys admin may add/remove a board or folder grant (RA dbdbf99)   | stop and surface — `board_share_*` / `folder_share_*` requires manager rights on the target board                  |
| `trash_restore_forbidden`     | 403    | only the report owner / sys admin may trash or restore a report (RA dc8bd45)             | stop and surface — non-owners cannot soft-delete; mention the actual owner if known                               |
| `takedown_owner_forbidden`    | 403    | only the report owner may submit a takedown request on their own report (RA 3e92860)     | stop and surface — non-owners cannot file takedown requests; ask the actual owner                                  |
| `takedown_manager_forbidden`  | 403    | only the target board's manager / sys admin may approve / reject a takedown (RA 3e92860) | stop and surface — service account usually lacks this; do not retry                                                |
| `takedown_already_processed`  | 403    | the takedown request was already approved / rejected / withdrawn (RA 3e92860)            | stop — DO NOT retry. Re-fetch via `takedowns_list` to confirm the final status                                     |
| `snapshot_missing`            | n/a    | local widget catalog snapshot not present                                                | run `report-skill catalog sync` once, then retry                                                                  |
| `llm_error`                   | n/a    | configured LLM provider returned an error during `from-prompt` / `revise`                | surface `detail`; try `--provider <other>` or set `SKILL_LLM_PROVIDER`                                            |
| `no_llm_provider`             | n/a    | no LLM provider configured for a tool that needs one                                     | tell the user to set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / use `bridge` mode                                   |

For all other ApiErrors the legacy `{error: "API error", status_code, message, payload}` shape still applies.

### Lifecycle notes (v0.5.2)

- **phase=finalized self-lock** — direct body PATCH (`report_update`, `report_add_page`, `report_revise`, `report_append`) on a `phase=finalized` report is rejected with `finalized_readonly`. The skill surfaces this in the response `warnings` list when applicable; for any intentional edit, call `report_unpublish` first to drop the report back to `drafting`, then patch, then `report_publish` again. Composite **summary widgets** and mount/folder operations are not blocked by finalize.
- **mount auto-transitions `drafting` → `reviewing`** — calling `report_mount` on a `drafting` report automatically advances `phase` to `reviewing` (one-way). Subsequent unmounts do not revert. If the user later wants the report back at `drafting`, call `report_unpublish` (no-op on non-finalized) or manually set `phase` via `report_update`.
- **`report_publish` is idempotent** — calling on an already-finalized report is a no-op that returns current state. Notification fan-out (`report.phase_to_finalized`) only fires on the actual transition, not on idempotent re-calls. Same for `report_unpublish` on an already-drafting report.

## v0.6.0 — new MCP tools

The following 11 MCP tools were added in 0.6.0. From any MCP client (Claude Desktop / Continue / Cursor) call them by name.

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

---

## v0.9.0 — widget styling + cross-references (RA defcb74 / 074233d / c2d9663)

ReportArchive widget surface gained 3 cross-cutting capabilities:

**Cell color tokens — table / comparison (RA c2d9663).** Both `table` and `comparison` widget `content` now accepts an optional `cell_styles` object: a side-table keyed by `"rowKey::columnKey"` (table) / `"rowKey::caseKey"` (comparison), each entry `{bg?, fg?}` referencing a color-token enum. Cell data itself is untouched. Adapters pass `cell_styles` through unchanged; the server validates per `_CELL_STYLES_SCHEMA` so unknown keys are rejected. Example: `{"rows": [...], "cell_styles": {"r0::c1": {"bg": "amber", "fg": "ink"}}}`.

**Color-token enum (RA c2d9663 + defcb74).** Valid tokens (use these *exactly*; server rejects unknown values): `ink`, `gray`, `slate`, `red`, `orange`, `amber`, `yellow`, `lime`, `green`, `teal`, `cyan`, `sky`, `blue`, `indigo`, `violet`, `purple`, `pink`, `rose` (18 tokens, dark-mode adaptive). The same enum drives `cell_styles.{bg,fg}` (table/comparison), `caption_color`, `note_color`, and the `color` mark inside `rich_text` body content. No shading suffixes (`amber-50`, `ink-700`) — base tokens only.

**Caption + note color (RA defcb74).** Almost every widget's `content` now accepts `caption_color` (color-token enum above) and `caption_html` (HTML string, ≤2000 chars). `table` and `image` also accept `note_color` + `note_html` (≤4000 chars). Adapter passthrough is wired across all 24 affected widgets — round-trip safe. Use `caption_html` instead of plain `caption` when you need inline color spans inside the caption.

**#widget cross-references in rich_text (RA 074233d).** The body now lets writers reference other blocks with `#` — "그림 3", "표 2" — numbered live at render time per category, not stored. New tool:

- `widget_ref_categories_list` — projection of `GET /api/widgets` → `ref_categories`. Returns `[{key, label}]` in display order: 그림 / 표 / 비교표 / 키-값 / RACI / 수식 / 목록 / 첨부 / 영상 / 임베드. The LLM can call this to know which categories exist before composing #widget refs.

Mapping rule (set in backend `registry.py` `REF_CATEGORY_BY_TYPE`): `table` is the only widget in category `"table"`; `comparison` / `key_value` / `raci_matrix` are their own categories so "표 N" counts only real tables. All visual widgets (`image` / `chart` / `scatter` / `heatmap` / `pie` / etc.) share category `"figure"`. `heading` / `rich_text` themselves are structural (None — not referenceable).

**Inline font + text-color tokens in rich_text (RA defcb74 + 074233d).** Body marks now include FontFamily (맑은 고딕 / 바탕 / 굴림 / 돋움 / 궁서 / 나눔 / Arial, etc.) and dark-mode-adaptive color tokens. These survive sanitize / round-trip via the existing rich_text mark allowlist — no LLM-side change needed for authoring, but worth knowing the surface exists if revising body content.

---

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

---

## v0.10.0 — soft delete + takedown queue + per-cell rich markup

ReportArchive shipped a 3-stage report-deletion redesign and a takedown-request queue so non-managers can ask a board manager to unmount a report they own. `report-skill` exposes the full surface as 6 new MCP tools (`_DISPATCH` 76 → 82).

**Soft delete (RA dc8bd45 + ff64778).** `DELETE /api/reports/{id}` is no longer the right way to delete a report. Use:

- `report_trash` — `POST /api/reports/{id}/trash`. Move to trash (soft delete). Blocked while the report is mounted to any board; surface the error and ask the user to unmount (or submit a takedown request) first.
- `report_restore` — `POST /api/reports/{id}/restore`. Recover from trash. Returns the report with `deleted_at` cleared.

**Takedown requests (RA 3e92860).** When a board's mount edit-policy puts the manager (not the owner) in control of unmounting, the owner can no longer pull their own report off that board. The takedown queue is the recovery path:

- `report_takedown_request` — `POST /api/reports/{id}/takedown-requests`. Owner submits a request naming the board (`workspace_slug`) and optionally a `reason`.
- `takedowns_list` — `GET /api/takedown-requests`. Manager / sys-admin view. Filter by `workspace_slug` + `status` (`pending` | `approved` | `rejected`).
- `takedown_approve` — `POST /api/takedown-requests/{id}/approve`. Unmounts the report from the board and closes the request.
- `takedown_reject` — `POST /api/takedown-requests/{id}/reject`. Leaves the mount in place; the `reason` surfaces back to the requester.

**Per-cell + per-char rich markup (RA a97d5b5 / 7976ff7 / d62af9d).** Three text-bearing widgets gained a sibling rich-markup field that complements the plain text:

- `heading.text_html` — plain `text` is kept as the TOC/export title; `text_html` carries per-char color + format (same sanitized HTML grammar as caption_html). Adapter pass-through wired.
- `table.cell_html` — side-table keyed by `"rowKey::columnKey"` (same key as `cell_styles`), values = sanitized HTML per cell. Lets the LLM author rich content like "**critical** path" inside a single cell without losing it on the next revise.
- `comparison.cell_html` — same idea, keyed by `"rowKey::caseKey"`.

All three pass through the adapter unchanged; the server validates via `_CELL_HTML_SCHEMA`.

---

## MCP tool inventory (v0.10.x)

The MCP server now exposes **82 tools via stdio** (`report-skill-mcp`). The full list grew from the initial 23 read/write/offline tools through the v0.5.0 / v0.6.0 / v0.7.0 inventory sections above — call any of them by name from Claude Desktop / Continue / Cursor / any MCP client. The exact set is the runtime `_DISPATCH` map in `mcp_server.py`; verify locally with:

```powershell
report-skill-mcp --help   # or:
python -c "from report_skill.mcp_server import _DISPATCH; print(len(_DISPATCH))"
```
