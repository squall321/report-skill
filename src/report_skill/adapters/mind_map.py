"""Mind-map adapter — same data shape as tree (rows of {label, parent}).

Accepts nested dict { "Central": { "Branch": ["Leaf1", "Leaf2"] } } as well as
flat row list or pre-shaped passthrough.
"""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import truncate

_PASSTHROUGH = (
    "caption", "caption_skip_autofill", "layout", "branch_style",
    "color_by_group", "show_root_emphasis",
)
_ROW_KEYS = ("label", "parent", "color")


class MindMapAdapter(WidgetAdapter):
    type = "mind_map"

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
            raise NormalizeError(f"mind_map: unsupported input {type(raw).__name__}")

        rows = [r for r in rows if r and r.get("label")]
        if not rows:
            raise NormalizeError("mind_map: no rows after normalization")
        out["rows"] = rows
        return out

    def _coerce_row(self, raw: Any) -> dict:
        if not isinstance(raw, dict):
            return {}
        row: dict = {}
        for k in _ROW_KEYS:
            if k in raw and raw[k] is not None:
                row[k] = truncate(str(raw[k]), 200 if k != "color" else 32)
        return row

    def fallback_to(self) -> str:
        return "bulleted_list"


def _flatten_nested(node: Any, parent: str | None, out: list[dict]) -> None:
    """Walk nested dict/list into flat label/parent rows."""
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
