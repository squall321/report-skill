# Changelog

## 0.3.1 — 2026-06-04

CR-11 fix — `blocks_order` is now maintained on the edit paths
(`report add-page`, `report update`, MCP `report_add_page`, MCP
`report_update`), not just on `report create`. Previously the CR-2 +
CR-8 auto-compute logic lived inside `build_create_payload_multi` and
nothing called it from the edit flows, so:

- new pages added via `add-page` shipped with no `blocks_order`, which
  the backend interprets as "show every template block" — and the
  receiver saw the empty `progress`/`issues`/`next_week` blank-box
  render that CR-2 was supposed to fix
- new extras added via `report update` got their content stored but
  never showed up in the rendered report because their ids were
  missing from the page's `blocks_order` (backend hides anything not
  listed)

### What changed

- New helpers in `report_builder.py`:
  - `compute_blocks_order(template, content, extras, explicit=None)` —
    extracted from the inline create-flow code so add-page can call
    the exact same logic. Honors `explicit` verbatim when set.
  - `merge_blocks_order(existing, add_ids)` — appends new ids that
    aren't already present, preserving the user's ordering. Used by
    `update_blocks` when new extras are introduced.
- `report_ops.add_page` now accepts `template` + `blocks_order`. When
  `template` is supplied (the CLI/MCP always passes the fetched template
  dict now), the new page's `blocks_order` is auto-computed via
  `compute_blocks_order`. Explicit `blocks_order` overrides.
- `report_ops.update_blocks` now accepts `blocks_order`. When set, it
  REPLACES the page's blocks_order verbatim. When unset and
  `add_extra_blocks` is supplied, new extra ids are auto-merged into
  the page's existing order at the end via `merge_blocks_order`.
- CLI `report add-page` passes the fetched template + `draft.blocks_order`
  to `add_page`. CLI `report update` passes `draft.blocks_order` to
  `update_blocks`.
- MCP `report_add_page` + `report_update` tools gain an optional
  `blocks_order` argument with the same semantics.

### Why this is a 0.3.1 (patch) not 0.4.0 (minor)

Pure correctness fix on existing surfaces. No new commands, no new MCP
tools, no breaking changes. The two new args (`blocks_order` on update
and add-page) are optional with backward-compatible defaults — old
callers continue to work, just now with correct render order.

### Verified

- pytest 280/280 stays green.
- compute_blocks_order: heading extras float to top + filled template
  blocks middle + non-heading extras bottom, explicit override honored.
- merge_blocks_order: appends new ids past the existing order, idempotent
  on duplicates.

## 0.3.0 — 2026-06-03

LLM-driven revision flow — closes the gap between v0.2.0's two halves
("LLM creates from raw text" + "user/Claude Code crafts a patch and
PATCHes"). Now a single command takes an existing report + a Korean/English
revision instruction and applies the change with full CR-1 safety.

### New: `report-skill report revise`

```
report-skill report revise <id> "<자연어 수정 지시>"
  (--block-ids X,Y | --all) [--page N] [--dry-run] [--max-tokens 800]
```

For each target block: fetch the current content, prompt the LLM
(Anthropic / OpenAI / Ollama / bridge) with `(current + instruction +
content_schema)`, validate the LLM's output against the schema, and
PATCH only the blocks whose content actually changed.

CR-1 `scoped_content` protection applies — blocks not listed in
`--block-ids` (and unchanged when `--all`) stay intact on the server.
When `--all` and the instruction only touches one block, the others
come back unchanged from the LLM and are silently skipped — no false
patches.

`--dry-run` prints the resulting patch JSON without POSTing, so the
caller can review (and feed into `report update -i` later, or just sanity
check) before committing.

### New MCP tool: `report_revise`

Same surface as the CLI command, exposed to Claude Desktop / Cursor /
Continue / any MCP client. Schema:

```json
{
  "report_id": 42,
  "instruction": "summary에 PostgreSQL 15 마이그레이션 결과 한 줄 추가",
  "block_ids": ["summary"],   // OR set revise_all=true
  "page_index": 0,
  "dry_run": false,
  "max_tokens": 800
}
```

Returns `{id, title, revision, patched_blocks, results, view_url}` on
success; `{id, dry_run: true, patch, results}` on dry-run; `{id, patched:
[], results, note: "no blocks changed"}` when the instruction didn't
apply to any of the target blocks.

### New prompt builder: `build_block_revise_prompt`

`prompt.py` gains a fourth builder alongside `build_single_block_prompt`
/ `build_batch_prompt` / `build_page_prompt`. The revise prompt explicitly
includes:

- the block's authoritative JSON-schema
- the current block content (so the LLM knows what to preserve)
- the user's verbatim revision instruction
- explicit instructions: "preserve every field the user did NOT ask to
  change", "if the request does not apply to THIS block, return the
  current content unchanged"

That last rule is what makes `--all` cheap and safe: blocks the
instruction doesn't touch return verbatim, get equality-checked against
the current state, and never reach the patch dict.

### `/report-write` slash command updated

Flow B now has two sub-flows:
- **B1 — LLM-driven revision** (preferred when the user described the
  change in prose): `report revise <id> "<지시>" --block-ids ...`
- **B2 — Manual patch** (when the user handed you exact JSON):
  `report update <id> -i patch.json` — the previous Flow B verbatim.

Claude Code now picks B1 by default for vague / prose revision requests
and B2 only when the user explicitly provided patch content.

### Verified status from earlier releases

- v0.1.0: CR-1 (data loss on update), CR-2 (blocks_order auto), key_value
  Korean keys — all stand.
- v0.2.0: CR-3/5 onedir+noupx, CR-6 Defender opt-in, CR-7 receiver doc,
  CR-8 heading floating, CR-9 __version__ single source, CR-10 --version
  flag + MCP version — all stand.
- 280/280 deterministic tests pass.

### Not pursued

- **CR-4 Authenticode code signing** — out of scope for this project.
  Sufficient for the intended internal distribution: CR-3 (onedir) +
  CR-5 (--noupx) already eliminate the practical AV issues (verified
  20/20 cold-start). The remaining SmartScreen "Unknown publisher"
  warning is a one-time-per-machine click for internal users. If a
  wider-audience release ever needs Authenticode, the cost (~$300/yr
  EV cert or ~$10/mo Azure Trusted Signing) and the matching one-line
  `signtool sign /fd SHA256 /tr <timestamp> ...` addition to
  `build_exe.ps1` are well-understood — but not warranted for the
  current scope.

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

### Not pursued

- **CR-4 — Authenticode code signing** — out of scope. Sufficient for
  the internal distribution this project targets: CR-3 (onedir) + CR-5
  (--noupx) already remove the practical AV issues. The remaining
  SmartScreen "Unknown publisher" warning is a one-time click per
  receiver. See the v0.3.0 "Not pursued" note for the rationale and the
  one-line signtool integration path if a future wider release ever
  needs it.

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
