# report-skill (standalone .exe release)

External skill layer that publishes reports to a remote **ReportArchive**
instance. This is the **standalone .exe variant** — two PyInstaller
**onedir** binary trees that run on any Windows 10/11 machine with **zero
prerequisites**. No Python install, no pip, no venv.

## Which variant should I install?

Two distributions ship side-by-side on the GitHub Releases page. **Choose
the wheel if Python is available; choose standalone otherwise.** Both
expose the same CLI / MCP / slash-command surface — they only differ in
how the runtime is delivered.

| Situation | Pick |
|-----------|------|
| You have **Python 3.11+** on PATH (developer machine, admin / scripting environment) | **Wheel** — `report-skill-vX.Y.Z.zip` (small — wheel + a vendored `wheels\` dep folder). Double-click `setup.bat`; it creates a venv and installs the `.whl` **offline from `wheels\`** (no PyPI). The OS Python interpreter executes — no PyInstaller bootloader → less AV interaction, smaller install, easier patches. See [RECEIVER.md](RECEIVER.md). |
| Python is not available, or you can't install it (locked-down corporate machine, analyst / PM laptop, field deploy) | **Standalone** — `report-skill-standalone-vX.Y.Z.zip` (~37 MB). Two onedir trees, frozen Python runtime, **no internet ever**; double-click `setup.bat` to install. |
| Locked-down environment with aggressive AV | **Wheel preferred.** A signed Python.org interpreter has high AV trust, while frozen .exe bundles trigger heuristics more often. If standalone is the only option, pass `-AddDefenderExclusion` to `setup.bat` (Admin shell required) to register the install dir as a Defender exclusion. |

Both installs are **offline-capable**: the wheel zip ships its dependencies in
`wheels\` (no PyPI), and the standalone needs no internet at all.

Each receiver decides at install time — the GitHub Release page lists
both archives so you can hand the right one to the right user.

## What's in this archive

```
report-skill-standalone-vX.Y.Z/
├── report-skill/                 # CLI onedir tree
│   ├── report-skill.exe          #   entry .exe
│   └── _internal/                #   frozen Python runtime + deps + snapshot
├── report-skill-mcp/             # MCP server onedir tree
│   ├── report-skill-mcp.exe      #   entry .exe
│   └── _internal/
├── setup.bat                     # double-click first-run entry (bypasses ExecutionPolicy)
├── install-standalone.ps1        # the actual installer setup.bat calls
├── uninstall.ps1                 # removes PATH / env vars / global skills / state
├── .env.example
├── .claude/skills/               # /report-write, /widgets-sync, /bridge-process (dir form)
└── README.md  (this file)
```

No `.whl`, no Python, no `venv\` to manage, and **no internet ever** — the two
binaries are PyInstaller **onedir** trees (`--onedir`, *not* `--onefile`): each
is a directory holding the entry `.exe` next to an `_internal\` folder with the
frozen Python runtime, all dependencies, and the bundled widget catalog +
template snapshot. Keep each `.exe` together with its sibling `_internal\` — do
not move the lone `.exe` out of its folder.

> **Unsigned binaries:** these `.exe` files are unsigned (internal
> distribution). Windows SmartScreen may warn when you double-click them in
> Explorer — choose **추가 정보 → 실행** (More info → Run anyway), or just launch
> from a terminal / via `setup.bat`, which doesn't trip the Explorer warning.

## Install (Windows)

The simplest first run — **double-click `setup.bat`**. It launches the
installer with `powershell -NoProfile -ExecutionPolicy Bypass -File
install-standalone.ps1`, so you don't have to relax your PowerShell
ExecutionPolicy yourself, and it pauses on the result.

Prefer a terminal?

```powershell
# unzip the release somewhere stable, then:
cd report-skill-standalone-vX.Y.Z

# (a) bypass ExecutionPolicy explicitly:
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-standalone.ps1

# (b) or, if your policy already allows local scripts:
.\install-standalone.ps1
```

> **Mark-of-the-Web:** files unzipped from a downloaded archive may be
> "blocked". If a script refuses to run, unblock the folder first:
> `Get-ChildItem -Recurse . | Unblock-File`.

The installer:
1. Copies both onedir trees under `%LOCALAPPDATA%\report-skill\bin\` →
   `bin\report-skill\report-skill.exe` and
   `bin\report-skill-mcp\report-skill-mcp.exe` (each beside its `_internal\`)
2. Adds **both** entry dirs to your **user PATH** (persistent) so bare-name
   `report-skill` / `report-skill-mcp` invocation works
3. Sets `PYTHONIOENCODING=utf-8` and points `REPORT_SKILL_ENV` at the
   install's `.env` (both user env vars)
4. Copies the slash-command skills (directory form) to
   `%USERPROFILE%\.claude\skills\` — with backups if a same-named skill already
   existed, and cleaning up any legacy flat `.md` drops
5. Asks for `REPORT_API_BASE_URL`, email, password — writes `.env`
6. Runs `report-skill ping` as a smoke test
7. Drops a copy of `uninstall.ps1` at `%LOCALAPPDATA%\report-skill\uninstall.ps1`

Re-runnable. Skip the interactive `.env` step by passing args:

```powershell
.\install-standalone.ps1 -ServerUrl "http://10.0.5.42:3000/api" `
                         -Email "bot@reportskill.app" `
                         -Password "..."
```

Re-running with new `-ServerUrl` / `-Email` / `-Password` requires
`-Force` to overwrite an existing `.env` (so accidental re-runs don't
clobber your saved password). Without `-Force`, the existing `.env` is
kept as-is.

Other flags:

| flag | effect |
|------|--------|
| `-Force` | overwrite the existing `.env` with new args |
| `-InstallDir <path>` | use a different install dir (default `%LOCALAPPDATA%\report-skill`) |
| `-SkipPath` | don't touch the user PATH (you'll need to call the .exe by full path) |
| `-SkipGlobalSkills` | don't copy `.claude\skills\` to your global skills dir |
| `-Workspace <slug>` | set `REPORT_API_WORKSPACE_SLUG` (default `dx`) |
| `-AddDefenderExclusion` | (Admin shell) register the install dir as a Defender exclusion to cut .exe cold-start AV latency |

## Entry points (after install)

After install, **a new terminal** (so PATH refreshes) will see:

| command                                   | purpose                                              |
|-------------------------------------------|------------------------------------------------------|
| `report-skill ping`                       | health check — verifies login + server reachability  |
| `report-skill templates list`             | list templates the server has                        |
| `report-skill catalog list`               | list bundled widget catalog (33 widgets)             |
| `report-skill report …`                   | full report build / submit surface                   |
| `report-skill-mcp`                        | MCP server over stdio (for Claude Desktop, Cursor)   |

## Three usage paths (pick one)

### Path A — Claude Code chat (easiest)

The installer already copied the slash commands to your global skills
dir. In any Claude Code session, just say:

> /report-write 이번 주에 한 일 정리해서 weekly-dev 템플릿으로 보고서 만들어줘

Claude reads chat context, builds a draft JSON, calls `report-skill
report create`, and returns the URL.

### Path B — MCP server (Claude Desktop / Continue / Cursor / any MCP client)

Register the MCP server with your client. For **Claude Desktop** the config
file is `claude_desktop_config.json` under `%APPDATA%\Claude\`; other clients
use their own `mcp.json` / settings.

Because the installer puts both binary dirs on your user PATH, the **simple
form** is just the bare command:

```json
{
  "mcpServers": {
    "report-skill": {
      "command": "report-skill-mcp"
    }
  }
}
```

**Caveat:** some MCP hosts scrub the environment before spawning the server,
so they may not inherit your *user* PATH — then the bare name won't resolve.
The **safest** form is the absolute onedir path plus an explicit `env` block:

```json
{
  "mcpServers": {
    "report-skill": {
      "command": "C:\\Users\\<you>\\AppData\\Local\\report-skill\\bin\\report-skill-mcp\\report-skill-mcp.exe",
      "env": {
        "REPORT_SKILL_ENV": "C:\\Users\\<you>\\AppData\\Local\\report-skill\\.env"
      }
    }
  }
}
```

(Note the path is `...\bin\report-skill-mcp\report-skill-mcp.exe` — the entry
`.exe` lives **inside** its own onedir folder next to `_internal\`, not loose
in `bin\`.)

For **Claude Code**, the one-liner equivalent is:

```powershell
claude mcp add report-skill -- "C:\Users\<you>\AppData\Local\report-skill\bin\report-skill-mcp\report-skill-mcp.exe"
```

The server advertises **100개 (v0.16.0 기준)** tools (`report_create`,
`report_update`, `report_append`, `report_mount`, `report_milestone_add`,
`report_publish`, `composite_create`, `templates_suggest`,
`widgets_suggest_extras`, `file_upload`, …). Any LLM agent on the other side
can call them with just `.env` already set.

If the install isn't configured yet (no reachable `.env`), MCP tool calls now
return a structured `not_configured` error — with the resolved env path and a
fix hint — instead of **hanging** the client. `serverInfo` also reports the
real package version.

### Path C — Pure CLI (scripting / automation)

```powershell
report-skill templates list                                          # what templates exist
report-skill report create -t weekly-dev -i draft.json               # POST → personal workspace
report-skill report create -t weekly-dev -i draft.json --mount-to dx # POST + publish to dx board
report-skill report show 42                                          # inspect existing
report-skill report mounts 42                                        # list boards where it's published
report-skill report mount 42 -w dx -w qa                             # publish to multiple boards
report-skill report unmount 42 -w dx                                 # remove from one board
report-skill report milestone add 42 --date 2026-08-01 --label "GA Launch"
```

### Personal workspace + mount semantics (IMPORTANT)

New reports created via `report create` always land in the **author's
personal workspace** (`personal-<user_id>`), not in the workspace named
in `.env`'s `REPORT_API_WORKSPACE_SLUG`. That env var is now the
*permission/read context*, not the create target.

To make a report visible on a team board (eg. `dx`, `qa`, `dept-mx`),
publish it via a **mount**:

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

If you skip `--mount-to`, the report exists only in your personal space
— visible to you and system admins, but not to the team. That's
intentional per ReportArchive's "개인 작업공간과 조직 게시판 분리" design.

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

Note `blocks` is an object `{key: ...}`, **not** an array — the keys
match the template's block keys (see `report-skill templates show <id>`).

## Offline / no-server mode

When the ReportArchive server isn't reachable but you've got the .exe on
this laptop:

```powershell
# normalize + validate + dump the POST payload to a file (no API call needed)
report-skill report export -t rfc -i my-draft.json -o my-payload.json --offline

# later, on a machine that CAN reach the server:
report-skill import my-payload.json
```

The `--offline` flag uses the bundled template snapshot baked into the
.exe. The export is a complete `ReportCreate` body — replay anywhere
with a working server connection.

## Configuration (`.env`)

### Where the config is read from (discovery order)

The `.exe` looks for `.env` in this order, first hit wins (v0.16.0):

1. `REPORT_SKILL_ENV` — explicit absolute path (the installer sets this for you,
   pointing at `%LOCALAPPDATA%\report-skill\.env`)
2. `./.env` in the current working directory
3. the unzip root's `.env`
4. `%LOCALAPPDATA%\report-skill\.env` *(new — the standalone install's own copy)*
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

When you edit `.env` by hand, prefer **Notepad / VS Code**. Writing it with
PowerShell `Out-File` / `Set-Content` adds a UTF-8 BOM — v0.16.0 tolerates that
(the file is read as utf-8-sig), but a plain editor avoids surprises.

## Updating

A new ReportArchive backend release usually updates a few widget content
schemas. The bundled snapshot inside the .exe is frozen at the moment
the build was cut — *usually* fine because schema changes are additive
(new optional fields). When a wider change lands you'll get a new
`report-skill-standalone-vX.Y.Z.zip` — same install flow,
`install-standalone.ps1` overwrites the binaries and respects your
existing `.env`.

To check if your bundled snapshot is current vs the live server (needs
network access AND backend Python venv access for content_schema
extraction — wheel variant only):

```powershell
report-skill catalog list   # show the bundled baseline as-is
```

(Live `catalog sync` is wheel-variant-only because the .exe can't import
a separate ReportArchive Python venv at runtime.)

## Troubleshooting

| symptom | likely cause | fix |
|---|---|---|
| `report-skill: command not found` | new terminal not opened after install | close + reopen your terminal so PATH refreshes |
| `ping` returns auth error | wrong `.env` values | edit `%LOCALAPPDATA%\report-skill\.env` and retry, OR re-run installer with `-Force` |
| `ping` shows "no .env found" | `REPORT_SKILL_ENV` got unset | re-run `install-standalone.ps1` (it re-sets the env var idempotently) |
| MCP tool returns a `not_configured` error | `.env` not found / not reachable | the error includes the resolved env path + a fix hint — set `REPORT_SKILL_ENV` or populate `.env` (v0.16.0 returns this structured error instead of **hanging** the client) |
| SmartScreen blocks the .exe on double-click | unsigned internal build | choose **추가 정보 → 실행** (More info → Run anyway), or launch from a terminal / via `setup.bat` |
| Mojibake (한글 깨짐) in output | console code page is cp949, `PYTHONIOENCODING` not set | re-run installer (sets `PYTHONIOENCODING=utf-8` as User env var), open new terminal |
| TLS / cert errors against an https server | corporate TLS inspection or self-signed cert | set `REPORT_API_CA_BUNDLE` to your corporate PEM (see Configuration) |
| corporate proxy intercepts intranet API traffic | `HTTP(S)_PROXY` honored unexpectedly | leave `REPORT_API_TRUST_ENV=false` (the default ignores proxy env vars) |
| `fetch failed (500)` on report ops | backend out-of-sync with bundled snapshot (rare) | wait for next bundled release |
| Claude Code says "no such skill" | global skills dir not populated | re-run `install-standalone.ps1` (handles the copy with backups) |

## 제거 (Uninstall)

Run `uninstall.ps1` — the standalone installer dropped a copy at
`%LOCALAPPDATA%\report-skill\uninstall.ps1` (it also ships in this zip):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\report-skill\uninstall.ps1"
```

It removes the `...\report-skill\bin` PATH entries, clears the user-scope
`REPORT_SKILL_ENV` and `PYTHONIOENCODING`, drops the Windows Defender exclusion
(if `-AddDefenderExclusion` had been used — needs an Admin shell), and
optionally removes the `%LOCALAPPDATA%\report-skill` state and the global
Claude Code skills. Flags:

| flag | effect |
|---|---|
| `-Purge` | delete saved `.env` (+ backups) and `logs\` without prompting |
| `-Skills` | remove the global skills (report-write / widgets-sync / bridge-process) |
| `-Force` | kill any running `report-skill` / `report-skill-mcp` process first (they hold file locks under `bin\`) |

## Service account

Default service account in `.env.example` is `bot@reportskill.app`. Your
admin should have created this user with the `user` role on the target
workspace before handing you the release. If not, ask the admin to:

1. Log in as `admin` (seeded account)
2. `POST /api/auth/register` with `{email: "bot@reportskill.app", name: "Skill Bot", password: "<strong-random>"}`
3. `POST /api/workspaces/<slug>/members` with `{email: "bot@reportskill.app", role: "user"}`
4. Give you the password to paste into `install-standalone.ps1`
