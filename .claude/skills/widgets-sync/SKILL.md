---
name: widgets-sync
description: Refresh the cached widget catalog and flag what changed in the ReportArchive widget system. Use after the user pulls new ReportArchive code, when they ask "what widgets are new?", "did any widget schemas change?", or "/widgets-sync". Drives `report-skill catalog sync` + the examples staleness check.
---

# /widgets-sync — track ReportArchive widget changes

You are running the change-followup loop after the ReportArchive widget registry may have changed. The skill answers: what's new, what changed, and which adapters/examples are now out of date.

## Flow

### 1. Refresh the cache

```powershell
report-skill catalog sync
```

Re-fetches `/api/widgets`, re-runs the bridge to extract content schemas, recomputes per-widget hashes, rotates the previous snapshot. The command prints an inline diff (added / removed / modified) at the end.

### 2. Show the diff

The output has these sections:
```
+ added  (N)
    <widget_type>
- removed  (N)
~ modified  (N)
    <type>  <old_hash> -> <new_hash>
```

If `no changes vs previous snapshot` shows up, say so and stop — nothing to do.

### 3. For each ADDED widget

The widget exists in the backend but report-skill has no adapter. Read its content_schema from `.skill-cache\widgets.json` (typically `%LOCALAPPDATA%\report-skill\.skill-cache\widgets.json` on standalone installs) and tell the user:
- **Type:** `<type>` (label `<label>`)
- **Required content fields:** from `content_schema.required`
- **Suggested fallback widget** (bulleted_list / table / rich_text)
- **Next step (maintainer-only, source checkout required):** add `src/report_skill/adapters/<type>.py` + register in `__init__.py`. Standalone (.exe) installs cannot add adapters — open an issue against report-skill instead.

### 4. For each MODIFIED widget

Check whether the hand-validated example is stale:

```powershell
report-skill examples check
```

Exit code 2 means stale or missing examples. List which stale widgets overlap with the modified set from step 2 — those need human re-validation. Inspect with:

```powershell
report-skill examples status -v
```

### 5. For each REMOVED widget

If `examples status -v` lists it as `orphan` AND the user has a source checkout (maintainer flow), suggest deleting `examples/<type>.json`. Standalone (.exe) installs have no `examples/` dir on disk.

### 6. Summary

End with a checklist:
- [ ] Added widgets needing adapters: `<list>`
- [ ] Modified widgets with stale examples (re-validate): `<list>`
- [ ] Removed widgets with orphan files (safe to delete): `<list>`

If everything is green, just say so.
