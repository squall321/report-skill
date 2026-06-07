# report-skill (release artifact)

External skill layer that publishes reports to a remote **ReportArchive** instance. The wheel ships a bundled widget catalog + template snapshot, so you can post documents with **only the server URL + credentials** — no `catalog sync`, no backend source code, no Python venv inside ReportArchive.

## What's in this archive

```
report-skill-vX.Y.Z/
├── report_skill-X.Y.Z-py3-none-any.whl    # the skill (code + bundled snapshot)
├── .claude/skills/                         # /report-write, /widgets-sync, /bridge-process
├── .env.example
├── install.ps1                             # one-shot setup
├── INSTALL_SKILLS.md                       # slash-command install guide
└── README.md  (this file)
```

## Install (Windows)

```powershell
# unzip the release somewhere stable, then:
cd report-skill-vX.Y.Z
.\install.ps1
```

The installer:
1. Creates a Python venv in `.\venv\`
2. Installs the wheel (`pip install report_skill-...-py3-none-any.whl`)
3. Asks for `REPORT_API_BASE_URL`, email, password — writes `.env`
4. Runs `report-skill ping` to verify connectivity
5. Points you at the bundled Claude Code skills

Re-runnable. Skip the interactive `.env` step by passing args:

```powershell
.\install.ps1 -ServerUrl "http://10.0.5.42:3000/api" -Email "bot@reportskill.app" -Password "..."
```

## Entry points (after install)

| command                                          | purpose                                              |
|--------------------------------------------------|------------------------------------------------------|
| `.\venv\Scripts\report-skill.exe …`              | CLI (full feature surface)                           |
| `.\venv\Scripts\report-skill-mcp.exe`            | MCP server over stdio — wire into Claude Desktop / Continue / Cursor |
| `.\venv\Scripts\report-skill.exe ping`           | health check                                         |
| `.\venv\Scripts\report-skill.exe templates list` | show templates the server has                        |

## Three usage paths (pick one)

### Path A — Claude Code chat (easiest)

If you use Claude Code locally:

```powershell
# global install of the slash commands (once)
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\skills" | Out-Null
Copy-Item .claude\skills\*.md "$env:USERPROFILE\.claude\skills\" -Force
```

Then in any Claude Code session say something like:

> /report-write 이번 주에 한 일 정리해서 weekly-dev 템플릿으로 보고서 만들어줘

Claude reads the chat context, builds a draft JSON, calls `report-skill report create`, and returns the URL.

### Path B — MCP server (Claude Desktop / Continue / any MCP client)

Add to your client's `mcp.json`:

```json
{
  "mcpServers": {
    "report-skill": {
      "command": "C:\\path\\to\\report-skill-vX.Y.Z\\venv\\Scripts\\report-skill-mcp.exe"
    }
  }
}
```

The server advertises 66 tools — including reads (`reports_search`, `workspaces_list`, `entities_list`, `report_types_list`, `folders_list`, `widget_relations_list`, `notifications_list`, …), writes (`report_create`, `report_update`, `report_append`, `report_copy`, `report_publish`, `composite_create`, `composite_items_set`, `preset_create`, `template_set_scope`, …), milestones (`report_milestone_add`/`remove`), and maintenance (`catalog_sync`, `widgets_suggest_extras`, `file_upload`, `report_lock_status`, …). Any LLM agent on the other side can call them with just the server URL stored in `.env`. Verify the live count with `python -c "from report_skill.mcp_server import _DISPATCH; print(len(_DISPATCH))"`.

### Path C — Pure CLI (scripting / automation)

```powershell
report-skill templates list                                          # what templates exist
report-skill report create -t weekly-dev -i draft.json                # POST → personal workspace
report-skill report create -t weekly-dev -i draft.json --mount-to dx  # POST + publish to dx board
report-skill report show 42                                           # inspect existing
report-skill report mounts 42                                         # list boards where it's published
report-skill report mount 42 -w dx -w qa                              # publish to multiple boards
report-skill report unmount 42 -w dx                                  # remove from one board
report-skill report milestone add 42 --date 2026-08-01 --label "GA Launch"
```

### Personal workspace + mount semantics (IMPORTANT)

New reports created via `report create` always land in the **author's personal workspace** (`personal-<user_id>`), not in the workspace named in `.env`'s `REPORT_API_WORKSPACE_SLUG`. That env var is now the *permission/read context*, not the create target.

To make a report visible on a team board (eg. `dx`, `qa`, `dept-mx`), publish it via a **mount**:

| step | command |
|---|---|
| just save (personal only) | `report create -t weekly-dev -i draft.json` |
| save + publish in one shot | `report create -t weekly-dev -i draft.json --mount-to dx` |
| publish later | `report mount 42 -w dx` |
| publish to several boards | `report mount 42 -w dx -w qa -w dept-mx` |
| see where it's published | `report mounts 42` |
| unpublish from one board | `report unmount 42 -w dx` |

Mount has three edit policies (`--edit-policy`):

- `default` (Korean org default) — author + that board's lead can edit
- `owner_only` — strictly author; board lead loses auto-edit
- `coauthor` — every board member can edit

If you skip `--mount-to`, the report exists only in your personal space — visible to you and system admins, but not to the team. That's intentional per ReportArchive's new "개인 작업공간과 조직 게시판 분리" design.

Draft JSON shape (`draft.json`):

```json
{
  "title": "5월 4주차 주간 보고",
  "report_date": "2026-05-26",
  "tags": ["weekly", "backend"],
  "blocks": {
    "summary": "이번 주는 API 통합과 PostgreSQL 마이그레이션을 진행했다.",
    "progress": ["REST API 5개 추가", "단위 테스트 85%"],
    "issues": [{"issue": "타사 결제 API 지연", "severity": "높음", "owner": "김철수", "due": "2026-05-31"}],
    "next_week": ["결제 API 통합", "캐시 최적화"]
  }
}
```

Anything-ish — string, list, dict — the adapter normalizes.

## Offline / no-server mode

When the ReportArchive server isn't reachable but you've got the skill on this laptop:

```powershell
# normalize + validate + dump the POST payload to a file (no API call needed)
report-skill report export -t rfc -i my-draft.json -o my-payload.json --offline

# later, on a machine that CAN reach the server:
report-skill import my-payload.json
```

The `--offline` flag uses the bundled template snapshot. The export is a complete `ReportCreate` body — replay anywhere with a working server connection.

## Updating

A new ReportArchive backend release usually updates a few widget content schemas. The bundled snapshot inside this release is frozen at the moment the release was built — that's *usually* fine because schema changes are additive (new optional fields). When a wider change lands, you'll get a new `report-skill-vX.Y.Z.zip` — same install flow, overwrites `venv\` and `.claude\skills\`.

To check if your bundled snapshot is current vs the live server:

```powershell
report-skill catalog sync   # fetches live, computes diff, persists to .skill-cache\
report-skill catalog diff   # shows what changed since last sync
```

This needs network access to the ReportArchive backend AND read access to the backend's Python venv (for content_schema extraction via subprocess). If you don't have that access, just wait for the next bundled release.

## Troubleshooting

| symptom | likely cause | fix |
|---|---|---|
| `report-skill: command not found` | venv not on PATH | use `.\venv\Scripts\report-skill.exe` with full path, OR `.\venv\Scripts\activate` first |
| `ping` returns auth error | wrong .env values | edit `.env` and retry |
| `fetch failed (500)` on report ops | backend out-of-sync with bundled snapshot (rare) | run `catalog sync` if you have backend access, OR file an issue for a new release |
| Claude Code says "no such skill" | slash command markdown not in scope | copy `.claude\skills\*.md` to `%USERPROFILE%\.claude\skills\` |

## Service account

Default service account in `.env.example` is `bot@reportskill.app`. Your admin should have created this user with `user` role on the target workspace before handing you the release. If not, ask the admin to:

1. Log in as `admin` (seeded account)
2. `POST /api/auth/register` with `{email: "bot@reportskill.app", name: "Skill Bot", password: "<strong-random>"}`
3. `POST /api/workspaces/<slug>/members` with `{email: "bot@reportskill.app", role: "user"}`
4. Give you the password to paste into `install.ps1`
