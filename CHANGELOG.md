# Changelog

## 0.2.0 — 2026-06-03

Second batch of field-reported fixes (`report-skill_수정요청서_2026-06-03.md`).
Addresses the intermittent .exe startup failures that blocked CLI use during
the v0.1.0 verification window, plus six smaller refinements.

### PyInstaller migration (root cause of "Access is denied" / no-output runs)

- **CR-3 — `--onefile` → `--onedir`.** Onefile bundles re-extract ~19 MB to
  `%TEMP%\_MEIxxxxx` on every launch, racing Defender's real-time scanner.
  About half of launches lost the race on the test machine, surfacing as
  silent no-ops or `Access is denied`. Onedir lays the runtime out once at
  install time so subsequent launches just exec the entry .exe — no
  per-run extract, no race. Two trees ship:
  `bin\report-skill\report-skill.exe` and `bin\report-skill-mcp\report-skill-mcp.exe`,
  each with a sibling `_internal\` directory.
- **CR-5 — `--noupx`.** PyInstaller auto-packs with UPX when it's on PATH;
  UPX'd binaries are an AV-heuristic magnet. Build now passes `--noupx`
  to both pyinstaller invocations regardless of host state.

### Distribution + installer

- **Installer copies directory trees, not single files.** `install-standalone.ps1`
  now copies the two onedir trees into `%LOCALAPPDATA%\report-skill\bin\`,
  hard-replacing any prior `_internal\` (PyInstaller hashes pyd/pyc names
  per build — merging would leave dangling stragglers). Both entry
  directories are appended to user PATH so `report-skill` and
  `report-skill-mcp` still work bare-name.
- **CR-6 — `-AddDefenderExclusion` opt-in switch.** Off by default. When
  passed (and the shell is elevated), the installer calls
  `Add-MpPreference -ExclusionPath $InstallDir`. Non-admin shell: prints a
  warning and continues. `setup.bat` forwards all args (`%*`) so
  `setup.bat -AddDefenderExclusion` works.
- **CR-7 — Receiver doc divergence.** `docs/RECEIVER-STANDALONE.md` now
  opens with a "Which variant should I install?" table:
  wheel preferred when Python is available, standalone otherwise. The
  AV-heavy environment row explicitly recommends the wheel + offers
  `-AddDefenderExclusion` as the standalone fallback.

### Render order

- **CR-8 — Heading extras float to top of `blocks_order` by default.** A
  `heading` widget added as an `extra_block` used to land at the bottom
  of the page (extras appended after template blocks). Now the auto-
  computed `blocks_order` partitions extras into `heading` vs
  `non-heading`: headings prepend, non-headings append. Explicit per-page
  `blocks_order` still wins. Relative order among headings (and among
  non-headings) is preserved from the input extras list.

### Version observability

- **CR-9 — Single source of truth for `__version__`.** Replaced the
  hardcoded `__version__ = "0.0.1"` in `src/report_skill/__init__.py` with
  `importlib.metadata.version("report-skill")`. Falls back to
  `"0.0.0+unknown"` when imported from a source tree without `pip install
  -e .`. No more drift between `pyproject.toml` and the runtime constant.
- **CR-10 — `--version` flag + MCP version surfacing.** `report-skill
  --version` now prints `report-skill X.Y.Z` and exits (Typer eager
  callback on the root app). The MCP `ping` tool now returns
  `{"status":"ok","version":"X.Y.Z","logged_in_as":...}` so clients can
  verify the deployed version without OOB checks.

### Known limitations (deferred to a later release)

- **CR-4 — Authenticode code signing** is still pending. Requires a PFX
  certificate the project doesn't currently hold. Once a cert is
  available, add `signtool sign /fd SHA256 /tr <timestamp>` to the tail
  of `build_exe.ps1` for both entry .exe files. Signed onedir binaries
  combined with `--noupx` should drop SmartScreen / Defender heuristic
  flags close to zero.

### Verified status from v0.1.0 (no action needed)

- CR-1 (`report update` data loss) — fix from 0.1.0 stands.
- CR-2 (empty template blocks rendered as blank boxes) — fix from 0.1.0
  stands.
- key_value Korean keys → items[] auto-conversion — fix from 0.1.0 stands.

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
