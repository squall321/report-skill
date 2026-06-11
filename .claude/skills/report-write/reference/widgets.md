# report-write reference — widget input formats

Referenced from SKILL.md (Flow A/B). Read this when authoring or patching block content.

## Per-widget input guidance — adapters are tolerant

| widget          | natural input shape                                                |
|-----------------|--------------------------------------------------------------------|
| heading         | `"제목 문자열"` or `{text, level?}`                                |
| rich_text       | markdown string; supports @-mentions (see mention:// spec below)   |
| bulleted_list   | `["항목1", "항목2"]` or multi-line string with `-`/`*` bullets    |
| key_value       | flat dict `{"team": "백엔드", "lead": "..."}`                      |
| table           | list of dicts keyed by column.key, or markdown table string; dict form also accepts `note` (※-prefix auto), `column_widths`, `table_width_px`, `merges`, `header`, `expanded` (v0.15.0, see below) |
| comparison      | dict-of-dicts `{"비용": {"as_is": "...", "to_be": "..."}}`; dict form also accepts `note` (※-prefix auto), `column_widths`, `row_label_width`, `table_width_px`, `merges`, `header`, `expanded` (v0.15.0, see below) |
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

## Mentions in rich_text — mention:// full spec

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

Style: reserve mention chips for GENUINE cross-references. Linkifying every team name in a status report (e.g. converting every appearance of "DX팀" into a chip) is an AI tell — same rationale as the `**bold**` warning in SKILL.md. One or two mentions per paragraph at most.

## File uploads (for image/video/cad_3d widgets)

Media widgets require a `file_id`. Upload local files first:

```powershell
report-skill files upload <path>
# prints: file_id=abc123  ready to paste into draft
```

Then reference the `file_id` in the draft's media block.

## Widget styling + cross-references (v0.9.0, RA defcb74 / 074233d / c2d9663)

ReportArchive widget surface gained 3 cross-cutting capabilities:

**Cell color tokens — table / comparison (RA c2d9663).** Both `table` and `comparison` widget `content` now accepts an optional `cell_styles` object: a side-table keyed by `"rowKey::columnKey"` (table) / `"rowKey::caseKey"` (comparison), each entry `{bg?, fg?}` referencing a color-token enum. Cell data itself is untouched. Adapters pass `cell_styles` through unchanged; the server validates per `_CELL_STYLES_SCHEMA` so unknown keys are rejected. Example: `{"rows": [...], "cell_styles": {"r0::c1": {"bg": "amber", "fg": "ink"}}}`.

**Color-token enum (RA c2d9663 + defcb74).** Valid tokens (use these *exactly*; server rejects unknown values): `ink`, `gray`, `slate`, `red`, `orange`, `amber`, `yellow`, `lime`, `green`, `teal`, `cyan`, `sky`, `blue`, `indigo`, `violet`, `purple`, `pink`, `rose` (18 tokens, dark-mode adaptive). The same enum drives `cell_styles.{bg,fg}` (table/comparison), `caption_color`, `note_color`, and the `color` mark inside `rich_text` body content. No shading suffixes (`amber-50`, `ink-700`) — base tokens only.

**Caption + note color (RA defcb74).** Almost every widget's `content` now accepts `caption_color` (color-token enum above) and `caption_html` (HTML string, ≤2000 chars). `table` and `image` also accept `note_color` + `note_html` (≤4000 chars). Adapter passthrough is wired across all 24 affected widgets — round-trip safe. Use `caption_html` instead of plain `caption` when you need inline color spans inside the caption.

**#widget cross-references in rich_text (RA 074233d).** The body now lets writers reference other blocks with `#` — "그림 3", "표 2" — numbered live at render time per category, not stored. New tool:

- `widget_ref_categories_list` — projection of `GET /api/widgets` → `ref_categories`. Returns `[{key, label}]` in display order: 그림 / 표 / 비교표 / 키-값 / RACI / 수식 / 목록 / 첨부 / 영상 / 임베드. The LLM can call this to know which categories exist before composing #widget refs.

Mapping rule (set in backend `registry.py` `REF_CATEGORY_BY_TYPE`): `table` is the only widget in category `"table"`; `comparison` / `key_value` / `raci_matrix` are their own categories so "표 N" counts only real tables. All visual widgets (`image` / `chart` / `scatter` / `heatmap` / `pie` / etc.) share category `"figure"`. `heading` / `rich_text` themselves are structural (None — not referenceable).

**Inline font + text-color tokens in rich_text (RA defcb74 + 074233d).** Body marks now include FontFamily (맑은 고딕 / 바탕 / 굴림 / 돋움 / 궁서 / 나눔 / Arial, etc.) and dark-mode-adaptive color tokens. These survive sanitize / round-trip via the existing rich_text mark allowlist — no LLM-side change needed for authoring, but worth knowing the surface exists if revising body content.

## Per-cell + per-char rich markup (v0.10.0, RA a97d5b5 / 7976ff7 / d62af9d)

Three text-bearing widgets gained a sibling rich-markup field that complements the plain text:

- `heading.text_html` — plain `text` is kept as the TOC/export title; `text_html` carries per-char color + format (same sanitized HTML grammar as caption_html). Adapter pass-through wired.
- `table.cell_html` — side-table keyed by `"rowKey::columnKey"` (same key as `cell_styles`), values = sanitized HTML per cell. Lets the LLM author rich content like "**critical** path" inside a single cell without losing it on the next revise.
- `comparison.cell_html` — same idea, keyed by `"rowKey::caseKey"`.

All three pass through the adapter unchanged; the server validates via `_CELL_HTML_SCHEMA`.

## Multi-row header + expanded read mode — table / comparison (v0.15.0, RA 795c60c / 0c4e4bc / 8b5788f)

Both `table` and `comparison` `content` accept two more optional fields (adapter pass-through, server-validated):

- `header` — multi-row / merged header. Exact shape: `{row_count: 1-8 (required), cells: {"<headerRowIdx>::<colKey>": {text?, html?, bg?, fg?}}, merges: [{r, c, rs, cs}]}`. Cell keys: header row index (0-based) `::` `columns[].key` (table) / `cases[].key` (comparison). Per cell: `text` ≤2000 chars, `html` ≤4000 chars (same sanitized grammar as `caption_html`), `bg`/`fg` from the same 18-token color enum as `cell_styles`. `merges` entries are `{r, c, rs, cs}` — 0-based row/col + rowspan/colspan, all four required. Omit `header` entirely → classic single-row header derived from `columns[].label` / `cases[].label`.
- `expanded` — bool. `true` = read mode starts with multiline cells unfolded (기본 펼침); omitted/`false` = compact hover mode.

Example (table; 2-row header, "상반기" spans the two quarter columns):

```json
{
  "rows": [{"item": "매출", "q1": "1.2억", "q2": "1.5억"}],
  "header": {
    "row_count": 2,
    "cells": {
      "0::item": {"text": "항목"},
      "0::q1": {"text": "2026 상반기", "bg": "slate"},
      "1::q1": {"text": "1분기"},
      "1::q2": {"text": "2분기"}
    },
    "merges": [
      {"r": 0, "c": 0, "rs": 2, "cs": 1},
      {"r": 0, "c": 1, "rs": 1, "cs": 2}
    ]
  },
  "expanded": true
}
```
