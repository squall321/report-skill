# report-skill (release artifact)

External skill layer that publishes reports to a remote **ReportArchive** instance. The wheel ships a bundled widget catalog + template snapshot, so you can post documents with **only the server URL + credentials** — no `catalog sync`, no backend source code, no Python venv inside ReportArchive.

## What's in this archive

```
report-skill-vX.Y.Z/
├── report_skill-X.Y.Z-py3-none-any.whl    # the skill (code + bundled snapshot)
├── wheels/                                 # vendored runtime deps → OFFLINE install (no PyPI)
├── .claude/skills/                         # /report-write, /widgets-sync, /bridge-process (dir form)
├── .env.example
├── setup.bat                               # double-click first-run entry (bypasses ExecutionPolicy)
├── install.ps1                             # the actual installer setup.bat calls
├── uninstall.ps1                           # removes PATH / env vars / global skills
├── INSTALL_SKILLS.md                       # slash-command install guide
└── README.md  (this file)
```

## Prerequisites

- **Python 3.11+** on this machine — install from <https://www.python.org/downloads/>
  and **check "Add python.exe to PATH"** during install. The Microsoft Store
  python *stub* does not count; the installer detects and rejects it.
- If you can't install Python, use the **standalone .exe** variant instead
  (`report-skill-standalone-vX.Y.Z.zip` — no Python needed). See
  [RECEIVER-STANDALONE.md](RECEIVER-STANDALONE.md).
- **Offline / intranet OK.** The zip ships a `wheels\` folder with every
  runtime dependency, so the install needs **no PyPI / internet access** —
  `install.ps1` does an offline `--no-index --find-links wheels\` install when
  that folder is present.

## Install (Windows)

The simplest first run — **double-click `setup.bat`**. It launches the
installer with `powershell -NoProfile -ExecutionPolicy Bypass -File
install.ps1`, so you don't have to relax your PowerShell ExecutionPolicy
yourself, and it pauses on the result.

Prefer a terminal? Both of these work:

```powershell
# unzip the release somewhere stable, then:
cd report-skill-vX.Y.Z

# (a) bypass ExecutionPolicy explicitly:
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1

# (b) or, if your policy already allows local scripts:
.\install.ps1
```

> **Mark-of-the-Web:** files unzipped from a downloaded archive may be
> "blocked". If a script refuses to run, unblock the folder first:
> `Get-ChildItem -Recurse . | Unblock-File`.

The installer:
1. Resolves a real **Python 3.11+** — tries `python`, then `py -3.13` / `-3.12`
   / `-3.11`, rejects the Microsoft Store stub, and fails with an actionable
   message if none is found.
2. Creates a Python venv in `.\venv\`.
3. Installs the wheel — **offline from `wheels\`** when that folder ships
   alongside (no PyPI round-trip), else from PyPI.
4. Asks for `REPORT_API_BASE_URL`, email, password — writes `.env`.
5. Sets the user-scope `REPORT_SKILL_ENV` variable to this `.env`'s absolute
   path automatically, so the CLI / MCP server find your config from any cwd.
6. Runs `report-skill ping` to verify connectivity.
7. **Copies the Claude Code slash commands to `%USERPROFILE%\.claude\skills\`
   by default** (opt out with `-SkipGlobalSkills`).

Re-runnable. Skip the interactive `.env` step by passing args:

```powershell
.\install.ps1 -ServerUrl "http://10.0.5.42:3000/api" -Email "bot@reportskill.app" -Password "..."
```

| flag | effect |
|------|--------|
| `-ServerUrl` / `-Email` / `-Password` / `-Workspace` | pre-fill `.env` (skip the prompts) |
| `-SkipGlobalSkills` | don't copy `.claude\skills\*` to your global skills dir |
| `-OverwriteEnvVar` | repoint user `REPORT_SKILL_ENV` even if it already points at another install |

## Entry points (after install)

| command                                          | purpose                                              |
|--------------------------------------------------|------------------------------------------------------|
| `.\venv\Scripts\report-skill.exe …`              | CLI (full feature surface)                           |
| `.\venv\Scripts\report-skill-mcp.exe`            | MCP server over stdio — wire into Claude Desktop / Continue / Cursor |
| `.\venv\Scripts\report-skill.exe ping`           | health check                                         |
| `.\venv\Scripts\report-skill.exe templates list` | show templates the server has                        |

## Three usage paths (pick one)

### Path A — Claude Code chat (easiest)

If you use Claude Code locally, `install.ps1` already copied the slash
commands to your global skills dir (unless you passed `-SkipGlobalSkills`).
To (re)install them by hand — note the skills ship in **directory form**
(`report-write/` with a `reference/` subfolder, `widgets-sync/`,
`bridge-process/`), so copy the whole tree, not flat `.md` files:

```powershell
# global install of the slash commands (once)
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\skills" | Out-Null
Copy-Item .claude\skills\* "$env:USERPROFILE\.claude\skills\" -Recurse -Force
```

This installs three skills: **/report-write**, **/widgets-sync**, and
**/bridge-process**. Then in any Claude Code session say something like:

> /report-write 이번 주에 한 일 정리해서 weekly-dev 템플릿으로 보고서 만들어줘

Claude reads the chat context, builds a draft JSON, calls `report-skill report create`, and returns the URL.

### Path B — MCP server (Claude Desktop / Continue / any MCP client)

Register the MCP server with your client. For **Claude Desktop** the config
file is `claude_desktop_config.json` under `%APPDATA%\Claude\`; other clients
use their own `mcp.json` / settings. Use the **absolute** path to the venv's
`report-skill-mcp.exe` in `command`, and keep an explicit `env` block pointing
at your `.env`:

```json
{
  "mcpServers": {
    "report-skill": {
      "command": "C:\\path\\to\\report-skill-vX.Y.Z\\venv\\Scripts\\report-skill-mcp.exe",
      "env": {
        "REPORT_SKILL_ENV": "C:\\path\\to\\report-skill-vX.Y.Z\\.env"
      }
    }
  }
}
```

`install.ps1` already sets `REPORT_SKILL_ENV` user-scope automatically, but
some MCP hosts scrub the environment before spawning the server (so the
user-scope variable isn't inherited) — keeping the explicit `env` block makes
the config self-contained regardless of host.

For **Claude Code**, the one-liner equivalent is:

```powershell
claude mcp add report-skill -- "C:\path\to\report-skill-vX.Y.Z\venv\Scripts\report-skill-mcp.exe"
```

The server advertises **100개 (v0.16.0 기준)** tools — including reads (`reports_search`, `workspaces_list`, `entities_list`, `report_types_list`, `folders_list`, `widget_relations_list`, `notifications_list`, …), writes (`report_create`, `report_update`, `report_append`, `report_copy`, `report_publish`, `composite_create`, `composite_items_set`, `preset_create`, `template_set_scope`, …), milestones (`report_milestone_add`/`remove`), and maintenance (`catalog_sync`, `widgets_suggest_extras`, `file_upload`, `report_lock_status`, …). Any LLM agent on the other side can call them with just the server URL stored in `.env`. Verify the live count with `python -c "from report_skill.mcp_server import _DISPATCH; print(len(_DISPATCH))"`.

If the install isn't configured yet (no reachable `.env`), MCP tool calls now
return a structured `not_configured` error — including the resolved env path
and a fix hint — instead of **hanging** the client. The server also reports
its real package version in `serverInfo`.

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

## Configuration (`.env`)

### Where the config is read from (discovery order)

The CLI / MCP server look for `.env` in this order, first hit wins (v0.16.0):

1. `REPORT_SKILL_ENV` — explicit absolute path (the installer sets this for you)
2. `./.env` in the current working directory
3. the repo / unzip root's `.env`
4. `%LOCALAPPDATA%\report-skill\.env` *(new — shared with the standalone install)*
5. `~/.report-skill/.env`

The file is read as **utf-8-sig** (a UTF-8 BOM is tolerated), and **every key
in it is exported into the process environment**, so `.env` now also drives
`REPORT_FRONTEND_URL`, the `SKILL_LLM_*` / `OLLAMA_*` knobs, and
`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` — not just the `REPORT_API_*` values.

### Corporate proxy + TLS knobs

Three transport settings (all live in `.env`, see `.env.example`):

| key | default | purpose |
|---|---|---|
| `REPORT_API_TRUST_ENV` | `false` | When `false` the client **ignores** `HTTP(S)_PROXY` so a corporate proxy can't hijack intranet/localhost API traffic. Set `true` to honor `HTTP(S)_PROXY` **and** `NO_PROXY`. |
| `REPORT_API_CA_BUNDLE` | *(blank)* | Path to a corporate root-CA bundle (PEM) — needed when the server is behind **TLS inspection** or uses a self-signed / internal cert. Blank = built-in certifi roots. |
| `REPORT_API_VERIFY_TLS` | `true` | Last-resort escape hatch; `false` skips TLS verification entirely. Prefer `REPORT_API_CA_BUNDLE` — only disable on a trusted LAN. |

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
| MCP tool returns a `not_configured` error | `.env` not found / not reachable | the error includes the resolved env path + a fix hint — set `REPORT_SKILL_ENV` or populate `.env` (v0.16.0 returns this structured error instead of **hanging** the client) |
| `fetch failed (500)` on report ops | backend out-of-sync with bundled snapshot (rare) | run `catalog sync` if you have backend access, OR file an issue for a new release |
| Claude Code says "no such skill" | slash commands not in scope | copy `.claude\skills\* "$env:USERPROFILE\.claude\skills\" -Recurse -Force` (dir form — `*.md` matches nothing) |
| TLS / cert errors against an https server | corporate TLS inspection or self-signed cert | set `REPORT_API_CA_BUNDLE` to your corporate PEM (see Configuration) |
| corporate proxy intercepts intranet API traffic | `HTTP(S)_PROXY` honored unexpectedly | leave `REPORT_API_TRUST_ENV=false` (the default ignores proxy env vars) |

When you edit `.env` by hand, prefer **Notepad / VS Code**. Writing it with
PowerShell `Out-File` / `Set-Content` adds a UTF-8 BOM — v0.16.0 tolerates that
(the file is read as utf-8-sig), but a plain editor avoids surprises.

## 제거 (Uninstall)

Run the bundled `uninstall.ps1` (ships in this zip):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1
```

It reverses the machine-level traces: removes the `...\report-skill\bin` PATH
entries, clears the user-scope `REPORT_SKILL_ENV` and `PYTHONIOENCODING`, drops
the Windows Defender exclusion (if one was added), and optionally removes the
`%LOCALAPPDATA%\report-skill` state and the global Claude Code skills. Flags:

| flag | effect |
|---|---|
| `-Purge` | delete saved `.env` (+ backups) and `logs\` without prompting |
| `-Skills` | remove the global skills (report-write / widgets-sync / bridge-process) |
| `-Force` | kill any running `report-skill` / `report-skill-mcp` process first |

The wheel install also leaves your **unzip folder** (with `venv\`, the `.whl`,
and its `.env`) — `uninstall.ps1` can't know where that is, so delete it
yourself: `Remove-Item -Recurse -Force <your-unzip-folder>`.

## Service account

Default service account in `.env.example` is `bot@reportskill.app`. Your admin should have created this user with `user` role on the target workspace before handing you the release. If not, ask the admin to:

1. Log in as `admin` (seeded account)
2. `POST /api/auth/register` with `{email: "bot@reportskill.app", name: "Skill Bot", password: "<strong-random>"}`
3. `POST /api/workspaces/<slug>/members` with `{email: "bot@reportskill.app", role: "user"}`
4. Give you the password to paste into `install.ps1`
