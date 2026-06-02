# Changelog

## 0.1.0 — 2026-06-02

First receiver-ready release. Resolves the two field-reported data/render
bugs from `report-skill_수정요청서_2026-06-02.md` and removes all
dev-machine path leakage from receiver-facing surfaces.

### Bug fixes (data correctness)

- **CR-1 — `report update` data loss (High).** Partial patches no longer
  silently overwrite template blocks the user omitted. `block_patches` is
  now restricted to the block ids the user actually named in the draft
  file; everything else is left intact on the server.
  (`cli.py`, `report_ops.update_blocks` callsite.)
- **CR-2 — empty template blocks rendered as blank boxes (Medium).** The
  builder now sets `blocks_order` per page to the union of (template
  blocks the user filled, in template natural order) + (extras in input
  order). Backend hides any template block not in `blocks_order`, so the
  carrier-template + extra_blocks workflow renders cleanly. Explicit
  per-page `blocks_order` in the draft still wins.
  (`report_builder.build_create_payload_multi`.)
- **key_value silent fallback on Korean keys.** ASCII-only `to_slug()`
  dropped Korean keys entirely, raising `NormalizeError`, falling back
  to `rich_text`, and losing the labels. The adapter now redirects any
  Korean / non-ASCII / reserved key to the items[] form so labels render
  as the literal Korean text. Mixed input emits a unified items[] list.
  (`adapters/key_value.py`.)
- **Stale REPORT_SKILL_ENV footgun.** Running the installer with a
  custom `-InstallDir` (eg a temp sandbox) used to clobber the
  User-scope `REPORT_SKILL_ENV` env var that pointed at the real
  install, leaving the user's actual binaries unreachable. Installer
  now only touches User scope when InstallDir is the default
  `%LOCALAPPDATA%\report-skill`. (`install-standalone.ps1`.)

### Receiver-readiness

- **41 dev-path edits across 12 files.** Removed every `cd d:\report-skill;
  .\venv\Scripts\report-skill.exe` prefix in the three shipped slash
  command markdowns and replaced with bare `report-skill` (the installer
  puts it on user PATH). Same treatment for INSTALL_SKILLS.md, README,
  MCP server config example, the bridge cache paths in
  `bridge-process.md`, and the `REPORT_BACKEND_PATH` default in
  `config.py` / `.env.example`. Author name `박국진` in a sample payload
  replaced with `홍길동`. Snapshot `api_base_url` scrubbed.
- **`setup.bat` double-click installer.** ASCII-only batch wrapper that
  invokes `install-standalone.ps1` with `-ExecutionPolicy Bypass` and
  pauses at the end. Non-technical receivers no longer need to know
  PowerShell.
- **Three-field interactive setup with current-value defaults.** Running
  `setup.bat` against an existing install re-prompts for server URL /
  email / password; pressing Enter keeps the current value. No silent
  skip, no clobbered passwords. Skips the write entirely if nothing
  changed.
- **Slash command directory format.** `~/.claude/skills/<name>.md` (flat)
  is not how Claude Code loads skills — it requires
  `~/.claude/skills/<name>/SKILL.md` (directory). Restructured source
  and installer; legacy flat `.md` drops auto-migrate to backup.
- **UTF-8 BOM on PS1 + SKILL.md.** Windows PowerShell 5.1 reads BOM-less
  .ps1 as cp949, mojibake-ing Korean string literals at parse time.
  Added BOM to all shipped scripts and SKILL.md files.
- **PYTHONIOENCODING=utf-8 set by installer.** Belt-and-suspenders for
  pipe / redirect contexts where `sys.stdout.reconfigure()` silently
  fails.
- **Lazy `Settings()`.** `report-skill --help` no longer dumps a pydantic
  ValidationError when REPORT_API_PASSWORD is unset. Real config errors
  print a friendly diagnostic naming the `.env` path + missing vars.

### Build / distribution

- `setup.bat`, `RECEIVER-STANDALONE.md`, and the directory-format
  `.claude/skills/<name>/SKILL.md` tree all ship in
  `dist/report-skill-standalone-v0.1.0.zip`.
- `build_release.ps1 -ExeOnly` chains into `build_exe.ps1` for the
  standalone variant; `-WithExe` produces both wheel + standalone in
  one run.

## 0.0.1 — initial bootstrap

- 33 widget adapters, orchestrator with cross-widget fallback chain,
  template suggest + widget suggest, MCP server, Claude Code slash
  commands, offline export/import, edit-lock + optimistic concurrency,
  S/M/W tier policy, bundled widget catalog + template baseline.
