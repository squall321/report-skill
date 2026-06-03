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
| "just give it raw text, you figure it out"            | **Flow D** below (`report from-prompt` / `adhoc`)                |
| "I don't know what template fits"                     | **Flow E** below (`templates suggest` + adhoc)                   |

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

### E2. Or skip the picker entirely with `report adhoc`

```powershell
report-skill report adhoc "<user's raw text>" --create
```

This is shorthand for `report from-prompt --auto --with-extras --create`:
- `--auto`         — runs templates suggest internally; picks top if confidence≥medium, else aborts with suggestions
- `--with-extras`  — scans the text for chartable data / dates / hierarchies / etc. and auto-adds extra_blocks for visual widgets the template doesn't already cover
- `--create`       — POSTs after dry-run validates clean

Use `report adhoc` when the user just wants the report to exist and trusts the skill to organize it.

## Flow A — CREATE

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

### A3. Build the draft JSON

**Style note — rich_text content must NOT use markdown emphasis.**

The ReportArchive frontend's rich_text widget renders the `markdown` field as plain text — `**bold**` shows as literal asterisks, not bold. Also, heavy markdown formatting (`**term**` on every key phrase, `*italics*` peppered, `**Section:**` patterns inside body text) is the #1 visual tell for AI-generated content and makes reports look obviously machine-written.

When writing rich_text blocks: clean prose, the way a human analyst writes a paragraph. Reserve emphasis for genuinely critical phrases (≤1 per paragraph at most, and only when the reader truly needs the visual anchor). For lists with definitions, use a bulleted_list widget instead of bold-prefixed prose lines.



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
| rich_text       | markdown string                                                    |
| bulleted_list   | `["항목1", "항목2"]` or multi-line string with `-`/`*` bullets    |
| key_value       | flat dict `{"team": "백엔드", "lead": "..."}`                      |
| table           | list of dicts keyed by column.key, or markdown table string        |
| comparison      | dict-of-dicts `{"비용": {"as_is": "...", "to_be": "..."}}`         |
| chart/scatter   | `[{x: "Jan", revenue: 100}, ...]`                                  |
| pie/waffle      | dict label→value `{"북미": 40, "EMEA": 30}`                        |
| milestone       | dict date→label or list of `{date, label, status?}`                |
| flowchart       | list of step strings `["수집", "처리", "출력"]`                    |
| raci_matrix     | dict activity→{role: "R/A/C/I"}                                    |
| quadrant        | bucket `{q1:[...], q2:[...]}` or list of `{label, x, y}`           |
| tree/treemap    | nested dict `{"Tech": {"Eng": {...}}}`                             |
| sankey          | list of `{source, target, value}`                                  |
| equation        | `"E = mc^2"` (LaTeX, no $$ wrappers needed)                        |
| image/video/etc | requires `{file_id}` — adapter rejects raw paths/URLs              |

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

**Bridge mode** (uses the current Claude Code session as the LLM, no API key needed):
```powershell
$env:SKILL_LLM_PROVIDER = "bridge"
report-skill report adhoc "<text>"
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
