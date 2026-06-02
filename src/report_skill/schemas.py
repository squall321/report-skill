"""Snapshot loader — thin accessor over .skill-cache/widgets.json."""
from __future__ import annotations

from typing import Any, Optional

from report_skill import catalog


class SnapshotMissing(RuntimeError):
    pass


def load() -> dict:
    """Return the current widget snapshot dict, or raise SnapshotMissing."""
    snap = catalog.load_current()
    if snap is None:
        raise SnapshotMissing(
            "No widget snapshot. Run `report-skill catalog sync` first."
        )
    return snap


def widget(snapshot: dict, widget_type: str) -> Optional[dict]:
    return snapshot.get("widgets", {}).get(widget_type)


def content_schema(snapshot: dict, widget_type: str) -> Optional[dict]:
    w = widget(snapshot, widget_type)
    return w.get("content_schema") if w else None


def resolved_props(block: dict, snapshot: dict) -> dict:
    """Merge widget default_props with the template block's own props.

    Template block props take precedence over widget defaults. This is the
    effective props seen at runtime when the backend validates content.
    """
    w = widget(snapshot, block.get("type", ""))
    defaults: dict[str, Any] = (w or {}).get("default_props", {}) or {}
    own: dict[str, Any] = block.get("props", {}) or {}
    merged = dict(defaults)
    merged.update(own)
    return merged
