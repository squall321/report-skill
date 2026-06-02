# Installing the report-skill slash commands

This project ships two Claude Code skills:

- **`/report-write`** — turn the current chat into a ReportArchive report
- **`/widgets-sync`** — refresh widget cache + flag stale adapters/examples

Both live at `<install-dir>\.claude\skills\` (the directory you extracted the zip into, or the source repo root for dev checkouts).

## Option 1 — Project-scoped (default)

Leave them where they are. Auto-discovered by Claude Code **only when cwd is `<install-dir>\`** (or any subdirectory). Nothing to install.

```powershell
cd <install-dir>
claude
# /report-write  and  /widgets-sync are available
```

Recommended if you only use these while working in this project.

## Option 2 — Global (any cwd)

### Copy (one-shot)

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.claude\skills" | Out-Null
Copy-Item "<install-dir>\.claude\skills\report-write.md"  "$env:USERPROFILE\.claude\skills\report-write.md"  -Force
Copy-Item "<install-dir>\.claude\skills\widgets-sync.md"  "$env:USERPROFILE\.claude\skills\widgets-sync.md"  -Force
```

Repeat when the skill files change.

### Symlink (auto-sync — needs admin OR Developer Mode)

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.claude\skills" | Out-Null
New-Item -ItemType SymbolicLink -Path "$env:USERPROFILE\.claude\skills\report-write.md" -Target "<install-dir>\.claude\skills\report-write.md"
New-Item -ItemType SymbolicLink -Path "$env:USERPROFILE\.claude\skills\widgets-sync.md" -Target "<install-dir>\.claude\skills\widgets-sync.md"
```

If you get "A required privilege is not held", run PowerShell as Administrator or enable Developer Mode in Windows Settings → Privacy & security → For developers.

## Verifying

Inside `claude`, type `/` and confirm both commands appear. Both skills assume `report-skill` is on PATH (standalone installer adds `%LOCALAPPDATA%\report-skill\bin\` to user PATH; source checkouts use `<install-dir>\venv\Scripts\report-skill.exe`) and `.env` is populated — run `report-skill ping` first if unsure.
