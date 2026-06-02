"""Refresh the bundled widget catalog + template baseline.

Runs against the live ReportArchive backend pointed to by `.env`:
  1. catalog sync   → .skill-cache/widgets.json (live)
  2. templates sync → .skill-cache/templates/*.json
  3. copies both into src/report_skill/data/ so the next wheel build
     ships them as the bundled baseline.

Receivers of the wheel never need to run any `sync` — they just set the
server URL + creds and start posting. Re-run this script before each
release so the baseline matches what's in the field.

No side effects on the local `.skill-cache` rotation logic.
"""
from __future__ import annotations

# Force UTF-8 stdout/stderr so the success-line em-dash and any Korean
# text from the backend (template names, error messages) render on
# legacy Windows consoles (cp949 default). Must precede project imports.
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, OSError):
    pass

import json
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src" / "report_skill"
sys.path.insert(0, str(SRC.parent))

from report_skill import catalog  # noqa: E402
from report_skill.client import ApiError, ReportArchiveClient  # noqa: E402

DATA_DIR = SRC / "data"
TEMPLATES_DIR = DATA_DIR / "templates"


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

    print("[refresh] catalog sync (widgets + bridge)...")
    try:
        snapshot, diff = catalog.sync()
    except Exception as e:
        print(f"[refresh] catalog sync FAILED: {e}", file=sys.stderr)
        return 1
    print(f"[refresh]   widgets: {len(snapshot.widgets)} ({len(diff.added)} added, "
          f"{len(diff.modified)} modified, {len(diff.removed)} removed)")

    print("[refresh] templates sync (all)...")
    try:
        with ReportArchiveClient() as client:
            cached, errors = catalog.sync_templates(client)
    except ApiError as e:
        print(f"[refresh] templates sync FAILED ({e.status_code}): {e}", file=sys.stderr)
        return 2
    print(f"[refresh]   templates cached: {cached}  errors: {errors}")

    # Copy widgets snapshot into the bundled baseline path.
    bundled_widgets = DATA_DIR / "widgets.snapshot.json"
    shutil.copyfile(catalog.CACHE_FILE_CURRENT, bundled_widgets)
    print(f"[refresh] bundled widgets → {bundled_widgets.relative_to(PROJECT_ROOT)}  "
          f"({bundled_widgets.stat().st_size} bytes)")

    # Mirror templates dir entirely (overwriting any stale baseline entries).
    if TEMPLATES_DIR.exists():
        for f in TEMPLATES_DIR.glob("*.json"):
            f.unlink()
    copied = 0
    for src_file in catalog.TEMPLATES_CACHE_DIR.glob("*.json"):
        shutil.copyfile(src_file, TEMPLATES_DIR / src_file.name)
        copied += 1
    print(f"[refresh] bundled templates → {TEMPLATES_DIR.relative_to(PROJECT_ROOT)}/  "
          f"({copied} files)")

    # Sanity: dump the new bundled summary
    with bundled_widgets.open(encoding="utf-8") as fh:
        snap = json.load(fh)
    print(f"[refresh] bundled snapshot widget_count={len(snap.get('widgets') or {})}  "
          f"fetched_at={snap.get('fetched_at')}")
    print("[refresh] DONE — next `pip wheel .` will ship the new baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
