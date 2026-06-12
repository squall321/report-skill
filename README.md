# report-skill

External skill layer over **ReportArchive**. Takes content (organized by Claude in chat OR raw text fed directly to the skill's internal LLM) and turns it into structured reports via the ReportArchive HTTP API, using the existing widget/template system.

**Hard rule:** never modifies the ReportArchive repo. All integration is over HTTP, plus a read-only subprocess into the backend venv to extract widget content schemas (which are Python lambdas not exposed via the catalog API).

## Two execution paths (both produce the same result)

```
Path A — Claude Code drives                Path B — Internal LLM drives
─────────────────────────                  ─────────────────────────────
user chats with Claude in CLI              user pipes raw text to skill
        │                                          │
        ▼                                          ▼
Claude organizes content                  report from-prompt / adhoc
  in conversation                          ├─ auto-detect provider
        │                                  │   (Anthropic|OpenAI|Ollama)
        ▼                                  ├─ template_suggest picks one
/report-write skill                        ├─ widget_suggest auto-adds
        │                                  │   chart/milestone/pie extras
        ▼                                  ├─ per-block schema-constrained
report create / update / append /          │   prompt → block content
  milestone add / from-prompt                     │
        │                                         ▼
        └────────────────► orchestrator ◄─────────┘
                  (33 adapter normalize → validate → 
                   3-pass repair → cross-widget fallback)
                                │
                                ▼
                       POST/PATCH /api/reports
                  (expected_revision + edit-lock 
                   optimistic concurrency)
                                │
                                ▼
                        report URL returned
```

## Setup (maintainers — source checkout)

```powershell
cd <repo-root>
python -m venv venv
.\venv\Scripts\activate
pip install -e .
copy .env.example .env
# fill REPORT_API_PASSWORD in .env
report-skill ping
report-skill catalog sync
```

Optional — enable the internal LLM path. Three modes:

```powershell
# Mode 1: Anthropic API (paid, fast)
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# Mode 2: OpenAI API (paid)
$env:OPENAI_API_KEY = "sk-..."

# Mode 3: Ollama local (free, offline, 8B model)
# Install from https://ollama.com/download, then `ollama pull llama3.1:8b`

# Mode 4: Claude Code BRIDGE (no key — uses the Claude session you're in)
$env:SKILL_LLM_PROVIDER = "bridge"
# skill writes prompts to .skill-cache/bridge/
# in your Claude Code chat, say "process bridge queue" → Claude fulfills
```

## MCP server (any MCP client can call it)

In addition to the CLI and the Claude Code skill, `report-skill` ships an MCP server (`report-skill-mcp`) that exposes **100개 (v0.16.0 기준)** MCP tools via stdio. Wire it into Claude Desktop / Continue / Cursor / any MCP client. The cleanest config points the server at your `.env` and lets it read every credential from there:

```json
{
  "mcpServers": {
    "report-skill": {
      "command": "report-skill-mcp",
      "env": {
        "REPORT_SKILL_ENV": "C:\\path\\to\\.env"
      }
    }
  }
}
```

If you'd rather inline the credentials instead of pointing at `.env`, supply the **full** set — not just the password:

```json
{
  "mcpServers": {
    "report-skill": {
      "command": "report-skill-mcp",
      "env": {
        "REPORT_API_BASE_URL": "http://10.0.5.42:3000/api",
        "REPORT_API_EMAIL": "bot@reportskill.app",
        "REPORT_API_PASSWORD": "<service-account-password>",
        "REPORT_API_WORKSPACE_SLUG": "dx"
      }
    }
  }
}
```

For Claude Desktop the config file is `claude_desktop_config.json` under `%APPDATA%\Claude\`. For Claude Code, the equivalent one-liner is `claude mcp add report-skill -- report-skill-mcp` (use the absolute exe path if your MCP host scrubs the user PATH). An unconfigured install returns a structured `not_configured` error instead of hanging the client.

The full tool list (3 categories — read-only / write / offline export):
- read-only: `ping`, `templates_list`, `templates_show`, `templates_suggest`, `widgets_catalog`, `widgets_suggest_extras`, `report_show`, `examples_status`, `tier_show`
- write: `report_create`, `report_update`, `report_append`, `report_add_page`, `report_delete`, `report_milestone_add`, `report_milestone_remove`, `file_upload`
- offline/maintenance: `catalog_sync`, `catalog_sync_templates`, `report_export`, `report_import`, `examples_mine_from_report`, `tier_set`

All tools wrap the same functions the CLI uses — behavior identical across CLI / Claude Code skill / MCP.

## Offline workflow (no API access required)

When the ReportArchive server is unreachable but you've got the skill on your laptop:

```powershell
# 1. ONE-TIME (while online): cache the widget catalog + every template
report-skill catalog sync
report-skill catalog sync-templates

# 2. OFFLINE: write a draft, normalize + validate + dump the POST payload to a file
report-skill report export -t rfc -i my-draft.json -o my-payload.json --offline

# 3. LATER (back online): replay the saved payload
report-skill import my-payload.json
```

The export path bypasses HTTP entirely after the initial cache sync — the orchestrator uses the cached widget snapshot + cached template JSON to normalize + validate locally. Verified safe under genuine network failure (forced via wrong base URL → cache fallback → POST'd successfully on the other side as report id=5).

## CLI

```
report-skill
├── ping                              auth + API reachability check
├── import <payload.json>             POST a saved payload (offline-export round-trip)
├── catalog
│   ├── sync                          fetch /api/widgets + bridge + hash + diff
│   ├── sync-templates                cache every template body for offline export
│   ├── diff                          show changes vs previous snapshot
│   └── list                          table of cached widgets + hashes
├── templates
│   ├── list                          show all templates
│   ├── show <id>                     blocks + props per template
│   └── suggest "<text>"              keyword + LLM tie-break recommender
├── report
│   ├── draft  -t <tpl> -i draft      dry-run validation, no POST
│   ├── create -t <tpl> -i draft      POST /reports  (--partial, --mount-to <slug>...)
│   ├── export -t <tpl> -i draft -o payload.json  [--offline]   pure file output, no POST
│   ├── show <id>                     pretty-print metadata + per-block summary
│   ├── from-prompt "<text>" -t <tpl> --auto --with-extras --create
│   ├── adhoc "<text>"                = from-prompt --auto --with-extras --create
│   ├── mount <id> -w <slug>...       publish a personal report onto org board(s)
│   ├── unmount <id> -w <slug>        remove the publish from one board
│   ├── mounts <id>                   list boards where it's currently mounted
│   ├── update <id> -i patch          PATCH specific blocks (auto edit-lock)
│   ├── append <id> -i patch          MERGE into existing blocks (revision retry)
│   ├── milestone add <id> --date --label    incremental milestone event
│   ├── milestone remove <id> --date [--label]   delete by match
│   ├── add-page <id> -t <tpl> -i     append a new page (different template OK)
│   └── delete <id>                   DELETE /reports/{id}
├── tier
│   ├── show                          active S/M/W tier + policy knobs
│   └── set {S|M|W}                   persist tier override
├── examples
│   ├── status                        ok / stale / missing / orphan counts
│   ├── scaffold                      auto-create missing example files
│   ├── mine-from-report <id>         overwrite expected_content from a live report
│   └── check                         CI exit-code: 2 if stale/missing
├── llm
│   ├── probe                         test active provider connectivity
│   ├── probe-tier                    run calibration → auto-set tier
│   └── generate-block <type>         ad-hoc per-block generation test
└── files
    ├── upload <path>                 POST /api/files → file_id
    └── upload-dir <dir>              batch upload + JSON map output
```

## Draft JSON shapes

### Single page
```json
{
  "title": "5월 4주차 백엔드 주간보고",
  "report_date": "2026-05-26",
  "tags": ["weekly"],
  "blocks": {
    "<block_id>": "<anything-ish — string, list, dict — adapter handles it>"
  },
  "extra_blocks": [
    {"id": "extra_chart", "type": "chart", "props": {...}, "input": [...]}
  ]
}
```

### Multi-page
```json
{
  "title": "...",
  "pages": [
    {"template_id": "weekly-dev", "name": "백엔드", "blocks": {...}, "extra_blocks": [...]},
    {"template_id": "meeting-decision", "name": "주간 회의", "blocks": {...}}
  ]
}
```

### Append patch (incremental — preserves existing items)
```json
{
  "blocks": {
    "milestones": {"items": [{"date": "2026-08-01", "label": "..."}]},
    "progress":   ["new bullet item"],
    "issues":     [{"issue": "...", "severity": "보통"}]
  }
}
```

### Media inputs — local paths auto-upload
```json
{
  "extra_blocks": [
    {
      "id": "evidence_shot",
      "type": "image",
      "props": {"label": "근거 캡처"},
      "input": {"local_path": "d:/screenshots/issue-42.png", "caption": "..."}
    }
  ]
}
```

The CLI detects `local_path` / `path` / `src` in media-block inputs, uploads each via `POST /api/files`, and replaces the input with `{file_id, filename, ...}` before normalization.

## What each module does

| layer | file | role |
|---|---|---|
| HTTP client | `client.py` | auth + envelope unwrap; file upload; report CRUD |
| Catalog | `catalog.py` + `bridge/extract_content_schemas.py` | fetches /api/widgets, runs bridge in backend venv to pull `content_schema_for()` results, computes per-widget SHA256 hash, persists snapshot |
| Schemas | `schemas.py` | snapshot accessor, props resolution |
| Adapters | `adapters/*.py` (33) | "anything-ish input → widget content dict matching content_schema". Falls back via `fallback_to()` chain |
| Repair | `repair.py` | deterministic fixes — `to_slug`, `nearest_enum` (Levenshtein + middle-option tie-break), `force_enum` (aggressive — picks middle option for off-enum), `coerce_number/integer/iso_date/bool`, `truncate` |
| Validate | `validate.py` | jsonschema Draft7 + humanized error messages |
| Orchestrator | `orchestrator.py` | block-by-block: normalize → validate → tier-driven multi-pass repair → cross-widget fallback (via extra_blocks `<id>__fb`) |
| Builder | `report_builder.py` | template + content → ReportCreate POST payload (single- or multi-page) |
| Edit ops | `report_ops.py` | fetch/update/append/add-page/replace-page/delete/remove-items with **edit-lock retry** + **expected_revision optimistic concurrency** |
| Merge | `merge.py` | 12 per-widget-type append strategies (milestone dedupe-by-date, table append-rows, raci deep-merge per-role, sankey nodes+links dedupe, etc.) |
| Tier | `tier.py` | S/M/W policy knobs (blocks-per-call, max_repair_passes, fallback_chain_depth, aggressive_enum_match) actually consumed by orchestrator + repair |
| LLM | `llm.py` | provider abstraction (Anthropic / OpenAI / Ollama auto-detect) — pure httpx, SDK optional |
| Prompt | `prompt.py` | schema-constrained per-block / per-batch / per-page prompts (~300-500 tok at tier W) |
| Template suggest | `template_suggest.py` | keyword vocab scoring + LLM tie-break for ambiguous cases |
| Widget suggest | `widget_suggest.py` | 11 detectors incl. priority structured-chart pattern + cross-widget suppression |
| Upload chain | `upload_chain.py` | detect `local_path` in media inputs → `POST /api/files` → splice `file_id` back |
| Tags | `tags.py` | inference: hashtags → category vocab → date → keyword frequency |
| Examples | `examples.py` + `cli_examples.py` | hand-validated `examples/<type>.json` with `for_schema_hash` — stale detection + mine-from-report |
| Files CLI | `files.py` + `cli_files.py` | multipart upload to /api/files; detect_widget_type by extension |

## Robustness for local 8B models

The orchestrator's tier-W policy is now actually enforced:
- `blocks_per_call=1` (cli_llm uses single-block prompts)
- `max_repair_passes=3` (orchestrator loops post_repair 3× before failing)
- `aggressive_enum_match=True` (table adapter uses `force_enum` not `nearest_enum`)
- `fallback_chain_depth=2` (orchestrator walks `adapter.fallback_to()` up to 2 hops, materializes the result as `<id>__fb` extra_block)

```
1. STRUCTURE: one-block-per-LLM-call (schema in prompt + examples)
2. VALIDATE:  jsonschema mirrors backend exactly
3. REPAIR:    deterministic — iterative, up to max_repair_passes
4. RETRY:     LLM with prior errors injected ("fix only these")
5. FALLBACK:  adapter.fallback_to() → simpler widget as extra_block
```

Live regression tested via `tests/test_weak_llm_fixtures.py` with 6 fixture files mimicking Llama 3.1 8B / Qwen 2.5 7B failure patterns. False-positive filter for the structured-chart detector (reject prose with commas via avg cell length + sentence punctuation + numeric ratio).

## Personal workspace + mount model

ReportArchive (post phase-6 migration) routes every `POST /reports` into the author's `personal-<id>` workspace, regardless of the `X-Workspace-Slug` header. To make a report visible on a department board, **mount** it:

```powershell
report-skill report create -t weekly-dev -i draft.json --mount-to dx       # one-shot publish
# or, after the fact:
report-skill report mount 42 -w dx -w qa --edit-policy default
```

`edit_policy` options:
- `default` — author + board lead (Korean org default)
- `owner_only` — strictly the author
- `coauthor` — every board member can edit

Mounts are M:N (one report → many boards, each with its own folder + policy). MCP exposes `report_mount` / `report_unmount` / `report_mounts`, plus a `mount_to` arg on `report_create` for the one-shot path.

## Concurrency

Parallel append calls on the same report are safe via two layers:

1. **Edit-lock retry** — `edit_lock` context manager retries `POST /reports/{id}/lock` on transient 5xx with exponential backoff + jitter, up to 5 attempts.
2. **Expected-revision optimistic locking** — `append_to_blocks` sends `expected_revision` from the same fetch. On `revision_mismatch`, re-fetches, re-merges with latest state, retries up to 3 times. Exhaustion raises `RevisionConflict`.

Verified safe under 5 concurrent processes adding to the same milestone block: all 5 succeed, no items lost, revision increments monotonically.

## Widget change follow-up

```
ReportArchive developer adds/changes a widget
        ▼
$ report-skill catalog sync
  +  added: <new_widget>            → write adapter, register in __init__
  ~  modified: <type>  abc → def    → check examples/<type>.json staleness
  -  removed: <legacy>              → orphan example flagged
        ▼
$ report-skill examples check
  exit 2 if any stale/missing — wire into CI
```

## Claude Code skills

Three slash commands in `.claude/skills/` (each is a `<name>/SKILL.md` directory, not a flat `.md`):

- **`/report-write`** — orchestrates 6 flows: create / update / add-page / from-prompt / template-pick / append. See [.claude/skills/report-write/SKILL.md](.claude/skills/report-write/SKILL.md).
- **`/widgets-sync`** — refreshes cache + reports adapter/example gaps. See [.claude/skills/widgets-sync/SKILL.md](.claude/skills/widgets-sync/SKILL.md).
- **`/bridge-process`** — fulfills pending internal-LLM "bridge" requests (the `SKILL_LLM_PROVIDER=bridge` path). See [.claude/skills/bridge-process/SKILL.md](.claude/skills/bridge-process/SKILL.md).

The release installers copy these to your global skills dir by default. See [INSTALL_SKILLS.md](INSTALL_SKILLS.md) for manual / global install instructions.

## Test suite

```powershell
pytest tests -v
```

**280 deterministic tests**, no network/LLM, runs in <1s. Covers all 33 adapters, repair helpers, orchestrator E2E, weak-LLM fixture regressions, merge strategies, report_ops with mocked client + revision retry, template_suggest priority/confidence, widget_suggest detectors + suppression.

## Status

| area | status |
|---|---|
| 33 widget adapters | all wired + jsonschema validated |
| Multi-page reports (create / update / add-page / append / remove / delete) | E2E verified (ids 1, 2, 3) |
| Cross-widget fallback (table → bulleted_list as extra_block) | verified via `_verify_fallback.py` |
| Internal LLM path (from-prompt / adhoc) | code complete; needs `ANTHROPIC_API_KEY` or ollama for live E2E |
| Tier policy | actually enforced in orchestrator (repair passes) + table adapter (enum mode) |
| Examples mined from live reports | 27/33 widgets have real content; 6 media stay skeleton (file_id needed) |
| Auto-tag inference | wired into from-prompt path |
| Concurrency (lock retry + revision retry) | verified under 5 parallel processes |
| structured chart false-positive filter | verified against 8 prose/data cases |
