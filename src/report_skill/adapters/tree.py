"""Tree adapter — hierarchy via parent labels.

Accepts: nested dict (label → children dict/list), flat list of {label, parent}
rows, or pre-shaped dict with `rows`.
"""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import truncate

_PASSTHROUGH = (
    "caption", "caption_skip_autofill", "orientation", "node_shape",
    "edge_style", "color_by_group", "node_padding_x", "node_padding_y",
    # v0.9.1 — RA defcb74 caption color tokens
    "caption_color", "caption_html",
)
_ROW_KEYS = ("label", "parent", "subtitle", "color")


class TreeAdapter(WidgetAdapter):
    type = "tree"

    def normalize(self, raw: Any, props: dict) -> dict:
        out: dict = {}
        rows: list[dict] = []

        if isinstance(raw, dict):
            for k in _PASSTHROUGH:
                if k in raw:
                    out[k] = raw[k]
            if isinstance(raw.get("rows"), list):
                rows = [self._coerce_row(r) for r in raw["rows"]]
            else:
                tree_part = {k: v for k, v in raw.items()
                             if k not in _PASSTHROUGH and k != "rows"}
                _flatten_nested(tree_part, None, rows)
        elif isinstance(raw, list):
            for r in raw:
                if isinstance(r, dict):
                    rows.append(self._coerce_row(r))
        else:
            raise NormalizeError(f"tree: unsupported input {type(raw).__name__}")

        rows = [r for r in rows if r and r.get("label")]
        if not rows:
            raise NormalizeError("tree: no rows after normalization")
        out["rows"] = rows
        return out

    def _coerce_row(self, raw: Any) -> dict:
        if not isinstance(raw, dict):
            return {}
        row: dict = {}
        for k in _ROW_KEYS:
            if k in raw and raw[k] is not None:
                row[k] = truncate(str(raw[k]), 200) if k != "color" else truncate(str(raw[k]), 32)
        return row

    def fallback_to(self) -> str:
        return "bulleted_list"


def _flatten_nested(node: Any, parent: str | None, out: list[dict]) -> None:
    """Walk a nested dict (label → child dict/list/None) producing flat rows."""
    if isinstance(node, dict):
        for label, child in node.items():
            row: dict = {"label": truncate(str(label), 200)}
            if parent is not None:
                row["parent"] = truncate(str(parent), 200)
            out.append(row)
            _flatten_nested(child, str(label), out)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, (dict, list)):
                _flatten_nested(item, parent, out)
            elif item is not None:
                row: dict = {"label": truncate(str(item), 200)}
                if parent is not None:
                    row["parent"] = truncate(str(parent), 200)
                out.append(row)
