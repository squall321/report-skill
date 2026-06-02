"""Pie / donut adapter — label + value slices."""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import coerce_number, truncate


class PieAdapter(WidgetAdapter):
    type = "pie"

    def normalize(self, raw: Any, props: dict) -> dict:
        if isinstance(raw, dict) and isinstance(raw.get("rows"), list):
            # Pre-shaped passthrough — still re-coerce values.
            return {"rows": _coerce_rows(raw["rows"])}

        entries: list[tuple[Any, Any]] = []
        if isinstance(raw, dict):
            entries = list(raw.items())
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
        return {"rows": rows}

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
