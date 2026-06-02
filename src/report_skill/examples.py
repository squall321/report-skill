"""Examples store — hash-pinned per-widget reference inputs.

For each widget type we keep one JSON file under `examples/<type>.json` that
documents:

  * the natural input shape a caller can hand to the adapter
  * what `adapter.normalize()` is expected to produce for that input
  * the widget schema hash this example was hand-validated against

When the cached widget schema's hash diverges from `for_schema_hash` in the
example file, the example is considered **stale** and should be re-reviewed
by a human before being trusted.

This module never modifies the cache; it only reads `report_skill.schemas`
and writes under `examples/`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from report_skill import schemas
from report_skill.config import PROJECT_ROOT

EXAMPLES_DIR: Path = PROJECT_ROOT / "examples"
EXAMPLE_VERSION: int = 1


# --------------------------------------------------------------------------- #
# File locations + IO
# --------------------------------------------------------------------------- #
def example_path(widget_type: str) -> Path:
    """Return the absolute path where `<widget_type>.json` should live.

    The path is returned regardless of whether the file currently exists.
    """
    if not widget_type or not isinstance(widget_type, str):
        raise ValueError(f"widget_type must be non-empty string, got {widget_type!r}")
    return EXAMPLES_DIR / f"{widget_type}.json"


def load_example(widget_type: str) -> Optional[dict]:
    """Read and parse the example file for `widget_type`.

    Returns None when the file does not exist. Raises `json.JSONDecodeError`
    when the file is unreadable so corrupt files surface loudly.
    """
    path = example_path(widget_type)
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def write_example(widget_type: str, payload: dict, schema_hash: str) -> Path:
    """Persist an example, pinning it to `schema_hash`.

    The on-disk shape is:
        {
          "version": 1,
          "widget_type": "<type>",
          "for_schema_hash": "<hash>",
          "input": {...},
          "expected_content": {...},
          "notes": "..."
        }

    `payload` may already contain `input`, `expected_content`, or `notes`
    keys — they are kept as-is. `widget_type` and `for_schema_hash` are
    always overwritten with the explicit arguments so callers cannot
    accidentally desync them.
    """
    EXAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    record: dict = {
        "version": EXAMPLE_VERSION,
        "widget_type": widget_type,
        "for_schema_hash": schema_hash,
        "input": payload.get("input", {}),
        "expected_content": payload.get("expected_content", {}),
        "notes": payload.get("notes", ""),
    }
    path = example_path(widget_type)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path


# --------------------------------------------------------------------------- #
# Status / staleness
# --------------------------------------------------------------------------- #
@dataclass
class ExamplesStatus:
    """Summary of example coverage versus the current widget snapshot.

    * ok      — example file exists and `for_schema_hash` matches the cache
    * stale   — example file exists but the hash differs
    * missing — widget is in cache but no example file exists yet
    * orphan  — example file exists but the widget is no longer in cache
    """

    ok: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    orphan: list[str] = field(default_factory=list)

    # Detail tables (per-widget) so the CLI can render richer output
    # without re-reading every file. Keys are widget_type.
    hashes: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def has_problems(self) -> bool:
        return bool(self.stale or self.missing)

    def to_dict(self) -> dict:
        return {
            "ok": list(self.ok),
            "stale": list(self.stale),
            "missing": list(self.missing),
            "orphan": list(self.orphan),
            "hashes": dict(self.hashes),
        }


def _iter_example_files() -> list[Path]:
    if not EXAMPLES_DIR.is_dir():
        return []
    return sorted(p for p in EXAMPLES_DIR.iterdir()
                  if p.is_file() and p.suffix == ".json")


def status() -> ExamplesStatus:
    """Compare the cached widget snapshot against the examples/ directory.

    Raises `schemas.SnapshotMissing` if no snapshot has been built yet.
    """
    snapshot = schemas.load()
    widgets: dict[str, dict] = snapshot.get("widgets", {}) or {}

    out = ExamplesStatus()

    # Walk every widget the cache knows about.
    for wtype, w in sorted(widgets.items()):
        cache_hash = w.get("hash", "")
        ex = None
        try:
            ex = load_example(wtype)
        except json.JSONDecodeError:
            # Treat a corrupt file as stale so it shows up loudly without
            # crashing the CLI. The hash recorded is empty -> never matches.
            ex = {"for_schema_hash": "<corrupt>"}

        if ex is None:
            out.missing.append(wtype)
            out.hashes[wtype] = {"cache": cache_hash, "example": ""}
            continue
        example_hash = str(ex.get("for_schema_hash", ""))
        out.hashes[wtype] = {"cache": cache_hash, "example": example_hash}
        if example_hash == cache_hash and cache_hash:
            out.ok.append(wtype)
        else:
            out.stale.append(wtype)

    # Walk every on-disk example, flagging anything not in the cache.
    in_cache = set(widgets)
    for path in _iter_example_files():
        wtype = path.stem
        if wtype not in in_cache:
            out.orphan.append(wtype)

    return out


# --------------------------------------------------------------------------- #
# Skeleton generation
# --------------------------------------------------------------------------- #
def generate_skeleton(widget_type: str, widget_entry: dict) -> dict:
    """Build an initial example payload for a widget.

    The result is the *body* portion only (input / expected_content /
    notes) — the caller passes it to `write_example()` together with the
    current hash so the schema-pin is recorded.

    The skeleton picks a reasonable placeholder per content_schema shape
    and includes the widget's `default_props` so a hand-reviewer can tell
    at a glance what was assumed. The output is intentionally minimal —
    it is meant to be edited by a human, not used verbatim.
    """
    content_schema = widget_entry.get("content_schema") or {}
    default_props = widget_entry.get("default_props") or {}
    label = widget_entry.get("label") or widget_type

    placeholder_input = _placeholder_for_schema(content_schema)
    expected = _placeholder_for_schema(content_schema)

    notes = (
        f"TODO: hand-review this example for `{widget_type}` ({label}). "
        f"The input/expected_content fields are auto-generated placeholders "
        f"and almost certainly need to be replaced with realistic data."
    )

    return {
        "input": {
            "raw": placeholder_input,
            "props": default_props,
        },
        "expected_content": expected,
        "notes": notes,
    }


# --------------------------------------------------------------------------- #
# Internals — JSON-schema placeholder builder
# --------------------------------------------------------------------------- #
_PLACEHOLDERS: dict[str, Any] = {
    "string": "TODO",
    "integer": 0,
    "number": 0,
    "boolean": False,
    "null": None,
}


def _placeholder_for_schema(schema: Any, depth: int = 0) -> Any:
    """Build a minimal value satisfying `schema` as best as possible.

    This is a pragmatic walk over JSON-schema, not a full validator. It
    only needs to produce something a human can recognise and edit; we
    cover the common keywords (`type`, `properties`, `required`, `enum`,
    `items`, `oneOf`/`anyOf`).
    """
    if depth > 6 or not isinstance(schema, dict):
        return None

    # enum / const short-circuit
    if "const" in schema:
        return schema["const"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]

    # oneOf / anyOf — pick the first branch
    for key in ("oneOf", "anyOf"):
        branches = schema.get(key)
        if isinstance(branches, list) and branches:
            return _placeholder_for_schema(branches[0], depth + 1)

    stype = schema.get("type")
    if isinstance(stype, list):
        # Pick the first non-null type so the placeholder is informative.
        stype = next((t for t in stype if t != "null"), stype[0])

    if stype == "object" or (stype is None and "properties" in schema):
        props = schema.get("properties", {}) or {}
        required = set(schema.get("required") or [])
        out: dict = {}
        # Always include required, plus a couple more so the example
        # looks complete enough to read.
        keys = list(required)
        for k in props:
            if k not in required and len(keys) < max(len(required) + 3, 5):
                keys.append(k)
        for k in keys:
            out[k] = _placeholder_for_schema(props.get(k, {}), depth + 1)
        return out

    if stype == "array":
        items = schema.get("items") or {}
        min_items = int(schema.get("minItems") or 1)
        n = max(min_items, 1)
        return [_placeholder_for_schema(items, depth + 1) for _ in range(min(n, 3))]

    if stype == "string":
        if schema.get("format") == "date":
            return "2026-01-01"
        if schema.get("format") == "date-time":
            return "2026-01-01T00:00:00Z"
        if schema.get("format") == "uri":
            return "https://example.com"
        return "TODO"

    if stype in _PLACEHOLDERS:
        return _PLACEHOLDERS[stype]

    return None
