"""Waffle adapter — label + percent share rows (sums ≈ 100)."""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import coerce_number, truncate

_PASSTHROUGH = (
    "caption", "caption_skip_autofill", "unit",
    "cols", "grid_rows", "shape", "fill_direction",
    "show_legend", "show_value_per_cell",
    # v0.9.1 — RA defcb74 caption color tokens
    "caption_color", "caption_html",
)


class WaffleAdapter(WidgetAdapter):
    type = "waffle"

    def normalize(self, raw: Any, props: dict) -> dict:
        if isinstance(raw, dict) and isinstance(raw.get("rows"), list):
            out: dict = {"rows": _coerce_rows(raw["rows"])}
            for k in _PASSTHROUGH:
                if k in raw:
                    out[k] = raw[k]
            return out

        entries: list[dict] = []
        if isinstance(raw, dict):
            for lbl, val in raw.items():
                if lbl in _PASSTHROUGH:
                    continue
                entries.append({"label": lbl, "value": val})
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    label = item.get("label") or item.get("name") or item.get("key")
                    value = item.get("percent")
                    if value is None:
                        value = item.get("value")
                    entries.append({"label": label, "value": value})
        else:
            raise NormalizeError(f"waffle: unsupported input type {type(raw).__name__}")

        rows = _coerce_rows(entries)
        if not rows:
            raise NormalizeError("waffle: no usable rows")
        out = {"rows": rows}
        if isinstance(raw, dict):
            for k in _PASSTHROUGH:
                if k in raw:
                    out[k] = raw[k]
        return out

    def fallback_to(self) -> str:
        return "table"


def _coerce_rows(items: list[Any]) -> list[dict]:
    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        label = it.get("label")
        if label is None or str(label).strip() == "":
            continue
        val = coerce_number(it.get("value"))
        if val is None:
            val = coerce_number(it.get("percent"))
        if val is None:
            continue
        row: dict = {"label": truncate(str(label).strip(), 200), "value": val}
        color = it.get("color")
        if isinstance(color, str) and color.strip():
            row["color"] = truncate(color.strip(), 32)
        out.append(row)
    return out
