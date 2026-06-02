"""Milestone adapter — timeline of (date, label) markers with optional status."""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import coerce_iso_date, nearest_enum, truncate

_STATUS_ENUM = ["pending", "done", "delayed"]
_PASSTHROUGH = ("caption", "start_date", "end_date", "annotations")


class MilestoneAdapter(WidgetAdapter):
    type = "milestone"

    def normalize(self, raw: Any, props: dict) -> dict:
        out: dict = {}
        items_in: list[Any]

        if isinstance(raw, dict):
            for k in _PASSTHROUGH:
                if k in raw:
                    out[k] = raw[k]
            if isinstance(raw.get("items"), list):
                items_in = raw["items"]
            else:
                # date → label mapping (skip keys we already consumed)
                rest = {k: v for k, v in raw.items()
                        if k not in _PASSTHROUGH and k != "items"}
                if rest and all(not isinstance(v, (list, dict)) for v in rest.values()):
                    items_in = [{"date": k, "label": v} for k, v in rest.items()]
                else:
                    raise NormalizeError("milestone: dict input needs 'items' or date→label mapping")
        elif isinstance(raw, list):
            items_in = raw
        else:
            raise NormalizeError(f"milestone: unsupported input type {type(raw).__name__}")

        items_out: list[dict] = []
        for entry in items_in:
            item = self._coerce_item(entry)
            if item is not None:
                items_out.append(item)
        if not items_out:
            raise NormalizeError("milestone: no items after normalization")

        # Coerce start_date / end_date if present
        for k in ("start_date", "end_date"):
            if k in out:
                d = coerce_iso_date(out[k])
                if d is not None:
                    out[k] = d
                else:
                    out.pop(k)
        if "caption" in out:
            out["caption"] = truncate(str(out["caption"]), 200)
        out["items"] = items_out
        return out

    def _coerce_item(self, entry: Any) -> dict | None:
        if not isinstance(entry, dict):
            return None
        date = coerce_iso_date(entry.get("date"))
        label = str(entry.get("label", "")).strip()
        if not date or not label:
            return None
        item: dict = {"date": date, "label": truncate(label, 200)}
        if entry.get("note"):
            item["note"] = truncate(str(entry["note"]), 500)
        if entry.get("status"):
            s = nearest_enum(entry["status"], _STATUS_ENUM)
            if s is not None:
                item["status"] = s
        return item

    def fallback_to(self) -> str:
        return "table"
