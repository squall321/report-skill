"""Pie / donut adapter — label + value slices."""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import coerce_number, truncate

# Fields the backend pie content_schema accepts that we forward as-is
# when the caller supplies them on a dict input. (Source of truth:
# d:/ReportArchive/backend/app/widgets/registry.py pie descriptor.)
_PASSTHROUGH = (
    "caption",
    "caption_skip_autofill",
    "unit",
    "chart_type",
    "hole",
    "colorscale",
    "reverse_scale",
    "text_info",
    "text_position",
    "sort",
    "show_legend",
)


class PieAdapter(WidgetAdapter):
    type = "pie"

    def normalize(self, raw: Any, props: dict) -> dict:
        if isinstance(raw, dict) and isinstance(raw.get("rows"), list):
            # Pre-shaped passthrough — still re-coerce values.
            out: dict = {"rows": _coerce_rows(raw["rows"])}
            for k in _PASSTHROUGH:
                if k in raw:
                    out[k] = raw[k]
            return out

        entries: list[tuple[Any, Any]] = []
        carry: dict = {}
        if isinstance(raw, dict):
            for k in _PASSTHROUGH:
                if k in raw:
                    carry[k] = raw[k]
            entries = [
                (lbl, val) for lbl, val in raw.items() if lbl not in _PASSTHROUGH
            ]
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    label = item.get("label") or item.get("name") or item.get("key")
                    value = item.get("value")
                    if value is None:
                        value = item.get("percent")
                    entries.append((label, value))
        else:
            raise NormalizeError(f"pie: unsupported input type {type(raw).__name__}")

        rows = _coerce_rows([{"label": lbl, "value": val} for lbl, val in entries])
        if not rows:
            raise NormalizeError("pie: no usable slices")
        out = {"rows": rows}
        out.update(carry)
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
            continue
        row: dict = {"label": truncate(str(label).strip(), 200), "value": val}
        color = it.get("color")
        if isinstance(color, str) and color.strip():
            row["color"] = truncate(color.strip(), 32)
        out.append(row)
    return out
