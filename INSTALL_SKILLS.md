# Installing the report-skill slash commands

This project ships three Claude Code skills:

- **`/report-write`** — turn the current chat into a ReportArchive report
- **`/widgets-sync`** — refresh widget cache + flag stale adapters/examples
- **`/bridge-process`** — fulfill pending report-skill LLM "bridge" requests

They live at `<install-dir>\.claude\skills\` (the directory you extracted the
zip into, or the source repo root for dev checkouts). Each skill is a
**directory**, not a flat `.md` file — for example `report-write\SKILL.md` with
a `reference\` subfolder, `widgets-sync\SKILL.md`, `bridge-process\SKILL.md`.
Copy the whole tree, never `*.md`.

> **You usually don't need to do any of this by hand.** As of v0.16.0 both
> installers (`install.ps1` for the wheel, `install-standalone.ps1` for the
> standalone) copy these skills to your global skills dir **by default** —
> opt out with `-SkipGlobalSkills`. The steps below are for manual / dev
> setups or to re-sync after editing a skill.

## Option 1 — Project-scoped (default)

Leave them where they are. Auto-discovered by Claude Code **only when cwd is `<install-dir>\`** (or any subdirectory). Nothing to install.

```powershell
cd <install-dir>
claude
# /report-write, /widgets-sync, /bridge-process are available
```

Recommended if you only use these while working in this project.

## Option 2 — Global (any cwd)

### Copy (one-shot)

Copy every shipped skill directory in one go:

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.claude\skills" | Out-Null
Copy-Item "<install-dir>\.claude\skills\*" "$env:USERPROFILE\.claude\skills\" -Recurse -Force
```

Repeat (same command) when the skill files change.

### Symlink (auto-sync — needs admin OR Developer Mode)

Link each skill directory so edits in the repo are picked up live:

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.claude\skills" | Out-Null
foreach ($s in "report-write","widgets-sync","bridge-process") {
    New-Item -ItemType SymbolicLink `
        -Path   "$env:USERPROFILE\.claude\skills\$s" `
        -Target "<install-dir>\.claude\skills\$s"
}
```

If you get "A required privilege is not held", run PowerShell as Administrator or enable Developer Mode in Windows Settings → Privacy & security → For developers.

## Verifying

Inside `claude`, type `/` and confirm the three commands appear. All three skills assume `report-skill` is on PATH (standalone installer adds `%LOCALAPPDATA%\report-skill\bin\report-skill\` + `...\report-skill-mcp\` to user PATH; wheel / source checkouts use `<install-dir>\venv\Scripts\report-skill.exe`) and `.env` is populated — run `report-skill ping` first if unsure.
