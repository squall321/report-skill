"""Extract widget content_schemas from a live ReportArchive backend.

Why this exists
---------------
The HTTP catalog (`GET /api/widgets`) returns props_schema + default_props
per widget, but `content_schema_for(props)` is a Python callable that is
only invoked server-side (during report validation). The skill app needs
the actual content_schema to (a) build LLM prompts, (b) validate locally
without round-tripping every block.

This bridge is run as a one-shot subprocess by the skill — using the
backend's own venv so all of registry.py's imports resolve. It is
read-only: no DB writes, no app boot.

Output
------
JSON to stdout, shape:

    {
      "schema_version": "widget-v1",
      "extracted_at": "<isoformat utc>",
      "widgets": {
        "<type>": {
          "label": "...",
          "description": "...",
          "default_props": {...},
          "props_schema": {...},
          "content_schema": {...} | null,
          "content_schema_error": "<traceback>" | null,
          "has_content": true|false
        },
        ...
      }
    }

Usage (from inside the skill, with backend venv path resolved):

    backend_python = "<backend_venv>/python"
    backend_root   = "<ReportArchive/backend>"
    subprocess.run([backend_python, "bridge/extract_content_schemas.py",
                    "--backend-root", backend_root], ...)
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend-root", required=True,
                    help="Path to ReportArchive/backend (so app.* imports resolve).")
    args = ap.parse_args()

    backend_root = Path(args.backend_root).resolve()
    if not (backend_root / "app" / "widgets" / "registry.py").is_file():
        print(f"[bridge] not a valid backend root: {backend_root}", file=sys.stderr)
        return 2

    # Prepend backend root so `import app.widgets.registry` works.
    sys.path.insert(0, str(backend_root))

    try:
        from app.widgets.registry import WIDGET_REGISTRY  # type: ignore
    except Exception:
        print("[bridge] failed to import WIDGET_REGISTRY:", file=sys.stderr)
        traceback.print_exc()
        return 3

    out: dict = {
        "schema_version": "widget-v1",
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "widgets": {},
    }

    for wtype, desc in WIDGET_REGISTRY.items():
        entry: dict = {
            "label": desc.get("label"),
            "description": desc.get("description"),
            "default_props": desc.get("default_props", {}),
            "props_schema": desc.get("props_schema"),
            "has_content": bool(desc.get("content_schema_for")),
            "content_schema": None,
            "content_schema_error": None,
        }
        fn = desc.get("content_schema_for")
        if callable(fn):
            try:
                entry["content_schema"] = fn(desc.get("default_props", {}))
            except Exception:
                entry["content_schema_error"] = traceback.format_exc(limit=4)
        out["widgets"][wtype] = entry

    json.dump(out, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
