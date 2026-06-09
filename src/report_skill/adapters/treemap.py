"""Treemap adapter — hierarchical rows with optional parent label."""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import coerce_number, truncate

_PASSTHROUGH = (
    "caption", "caption_skip_autofill", "unit",
    "colorscale", "reverse_scale", "text_info", "branchvalues",
    # v0.9.1 — RA defcb74 caption color tokens
    "caption_color", "caption_html",
)


class TreemapAdapter(WidgetAdapter):
    type = "treemap"

    def normalize(self, raw: Any, props: dict) -> dict:
        out: dict = {}
        if isinstance(raw, dict):
            for k in _PASSTHROUGH:
                if k in raw:
                    out[k] = raw[k]

        if isinstance(raw, dict) and isinstance(raw.get("rows"), list):
            rows = _coerce_rows(raw["rows"])
            if not rows:
                raise NormalizeError(f"{self.type}: no usable rows")
            out["rows"] = rows
            return out

        entries: list[dict] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    entries.append(item)
        elif isinstance(raw, dict):
            entries = _flatten_nested(raw, parent=None)
        else:
            raise NormalizeError(f"{self.type}: unsupported input type {type(raw).__name__}")

        rows = _coerce_rows(entries)
        if not rows:
            raise NormalizeError(f"{self.type}: no usable rows")
        out["rows"] = rows
        return out

    def fallback_to(self) -> str:
        return "table"


def _flatten_nested(node: Any, parent: str | None) -> list[dict]:
    out: list[dict] = []
    if not isinstance(node, dict):
        return out
    for label, child in node.items():
        row: dict = {"label": label, "parent": parent}
        if isinstance(child, dict):
            # Branch — no value, recurse with this label as parent.
            out.append(row)
            out.extend(_flatten_nested(child, parent=str(label)))
        else:
            row["value"] = child
            out.append(row)
    return out


def _coerce_rows(items: list[Any]) -> list[dict]:
    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        label = it.get("label") or it.get("name")
        if label is None or str(label).strip() == "":
            continue
        row: dict = {"label": truncate(str(label).strip(), 200)}
        parent = it.get("parent")
        if parent is not None and str(parent).strip():
            row["parent"] = truncate(str(parent).strip(), 200)
        if "value" in it and it["value"] is not None:
            val = coerce_number(it.get("value"))
            if val is None:
                # Skip rows with an explicit but uncoerceable value.
                continue
            row["value"] = val
        color = it.get("color")
        if isinstance(color, str) and color.strip():
            row["color"] = truncate(color.strip(), 32)
        out.append(row)
    return out
