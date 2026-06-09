"""Progress bar adapter — items with label, current value, and max."""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import coerce_number, truncate

_STATUS = {"pending", "in_progress", "done", "blocked"}

_PASSTHROUGH = (
    "caption", "caption_skip_autofill", "default_max", "unit",
    # v0.9.1 — RA defcb74 caption color tokens
    "caption_color", "caption_html",
)


class ProgressBarAdapter(WidgetAdapter):
    type = "progress_bar"

    def normalize(self, raw: Any, props: dict) -> dict:
        default_max = coerce_number(props.get("default_max")) or 100.0

        if isinstance(raw, dict) and isinstance(raw.get("items"), list):
            items = _coerce_items(raw["items"], default_max)
            if not items:
                raise NormalizeError("progress_bar: no usable items")
            out: dict = {"items": items}
            for k in _PASSTHROUGH:
                if k in raw:
                    out[k] = raw[k]
            return out

        entries: list[dict] = []
        passthrough_src: dict | None = None
        if isinstance(raw, dict):
            # Reserve known passthrough keys (widget-level fields) so they don't get
            # mistaken for per-item shorthand entries like {label: value} pairs.
            passthrough_src = raw
            for lbl, val in raw.items():
                if lbl in _PASSTHROUGH:
                    continue
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
        out = {"items": items}
        if passthrough_src is not None:
            for k in _PASSTHROUGH:
                if k in passthrough_src:
                    out[k] = passthrough_src[k]
        return out

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
