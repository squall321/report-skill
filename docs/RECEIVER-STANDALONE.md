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
| You have Python 3.11+ on PATH (developer machine, admin / scripting environment) | **Wheel** — `report-skill-vX.Y.Z.zip` (~170 KB). `pip install` the bundled `.whl`; the OS Python interpreter executes — no PyInstaller bootloader → less AV interaction, smaller install, easier patches. |
| Python is not available, or you can't install it (locked-down corporate machine, analyst / PM laptop, field deploy) | **Standalone** — `report-skill-standalone-vX.Y.Z.zip` (~37 MB). Two onedir trees, frozen Python runtime, double-click `setup.bat` to install. |
| Locked-down environment with aggressive AV | **Wheel preferred.** A signed Python.org interpreter has high AV trust, while frozen .exe bundles trigger heuristics more often. If standalone is the only option, pass `-AddDefenderExclusion` to `setup.bat` (Admin shell required) to register the install dir as a Defender exclusion. |

Each receiver decides at install time — the GitHub Release page lists
both archives so you can hand the right one to the right user.

## What's in this archive

```
report-skill-standalone-vX.Y.Z/
├── report-skill.exe              # CLI (~19 MB, fully self-contained)
├── report-skill-mcp.exe          # MCP server over stdio (~19 MB)
├── install-standalone.ps1        # one-shot installer (PATH + .env + slash cmds)
├── .env.example
├── .claude/skills/               # /report-write, /widgets-sync, /bridge-process
└── README.md  (this file)
```

No `.whl`, no `install.ps1`, no `venv\` to manage. The two `.exe` files are
fully self-contained PyInstaller `--onefile` bundles that include the Python
runtime, all dependencies, and the bundled widget catalog + template
snapshot.

## Install (Windows)

```powershell
# unzip the release somewhere stable, then:
cd report-skill-standalone-vX.Y.Z
.\install-standalone.ps1
```

The installer:
1. Copies both `.exe` files to `%LOCALAPPDATA%\report-skill\bin\`
2. Adds that directory to your **user PATH** (persistent)
3. Sets `PYTHONIOENCODING=utf-8` and `REPORT_SKILL_ENV` user env vars
4. Copies `.claude\skills\*.md` to `%USERPROFILE%\.claude\skills\` (with
   backups if any of the same names already existed)
5. Asks for `REPORT_API_BASE_URL`, email, password — writes `.env`
6. Runs `report-skill ping` as a smoke test

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
| `-SkipGlobalSkills` | don't copy `.claude\skills\*.md` to your global skills dir |
| `-Workspace <slug>` | set `REPORT_API_WORKSPACE_SLUG` (default `dx`) |

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

Add to your client's `mcp.json`:

```json
{
  "mcpServers": {
    "report-skill": {
      "command": "C:\\Users\\<you>\\AppData\\Local\\report-skill\\bin\\report-skill-mcp.exe"
    }
  }
}
```

The server advertises 26 tools (`report_create`, `report_update`,
`report_append`, `report_mount`, `report_milestone_add`,
`templates_suggest`, `widgets_suggest_extras`, `file_upload`, …). Any LLM
agent on the other side can call them with just `.env` already set.

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
| Mojibake (한글 깨짐) in output | console code page is cp949, `PYTHONIOENCODING` not set | re-run installer (sets `PYTHONIOENCODING=utf-8` as User env var), open new terminal |
| `fetch failed (500)` on report ops | backend out-of-sync with bundled snapshot (rare) | wait for next bundled release |
| Claude Code says "no such skill" | global skills dir not populated | re-run `install-standalone.ps1` (handles the copy with backups) |

## Service account

Default service account in `.env.example` is `bot@reportskill.app`. Your
admin should have created this user with the `user` role on the target
workspace before handing you the release. If not, ask the admin to:

1. Log in as `admin` (seeded account)
2. `POST /api/auth/register` with `{email: "bot@reportskill.app", name: "Skill Bot", password: "<strong-random>"}`
3. `POST /api/workspaces/<slug>/members` with `{email: "bot@reportskill.app", role: "user"}`
4. Give you the password to paste into `install-standalone.ps1`
