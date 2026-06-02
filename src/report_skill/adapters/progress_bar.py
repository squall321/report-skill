"""Progress bar adapter — items with label, current value, and max."""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import coerce_number, truncate

_STATUS = {"pending", "in_progress", "done", "blocked"}


class ProgressBarAdapter(WidgetAdapter):
    type = "progress_bar"

    def normalize(self, raw: Any, props: dict) -> dict:
        default_max = coerce_number(props.get("default_max")) or 100.0

        if isinstance(raw, dict) and isinstance(raw.get("items"), list):
            items = _coerce_items(raw["items"], default_max)
            if not items:
                raise NormalizeError("progress_bar: no usable items")
            return {"items": items}

        entries: list[dict] = []
        if isinstance(raw, dict):
            for lbl, val in raw.items():
                if isinstance(val, dict):
                    entries.append({"label": lbl, **val})
                else:
                    entries.append({"label": lbl, "value": val, "max": 100})
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    entries.append(item)
        else:
            raise NormalizeError(f"progress_bar: unsupported input type {type(raw).__name__}")

        items = _coerce_items(entries, default_max)
        if not items:
            raise NormalizeError("progress_bar: no usable items")
        return {"items": items}

    def fallback_to(self) -> str:
        return "table"


def _coerce_items(items: list[Any], default_max: float) -> list[dict]:
    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        label = it.get("label") or it.get("name")
        if label is None or str(label).strip() == "":
            continue
        cur = it.get("value")
        if cur is None:
            cur = it.get("current")
        if cur is None:
            cur = it.get("percent")
        val = coerce_number(cur)
        if val is None:
            continue
        if val < 0:
            val = 0.0
        row: dict = {"label": truncate(str(label).strip(), 200), "value": val}
        mx = coerce_number(it.get("max"))
        if mx is not None and mx > 0:
            row["max"] = mx
        elif "percent" in it or (default_max and "max" not in it and "current" not in it):
            row["max"] = default_max
        note = it.get("note")
        if isinstance(note, str) and note.strip():
            row["note"] = truncate(note.strip(), 500)
        status = it.get("status")
        if isinstance(status, str) and status.strip().lower() in _STATUS:
            row["status"] = status.strip().lower()
        out.append(row)
    return out
