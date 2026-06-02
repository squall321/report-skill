"""Quadrant adapter — 2×2 matrix in bucket mode (SWOT-ish) or plot mode (BCG-ish)."""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import coerce_number, to_slug, truncate

_QUADRANT_ALIASES = {
    "tl": "tl", "tr": "tr", "bl": "bl", "br": "br",
    "top_left": "tl", "top-right": "tr", "top_right": "tr", "top-left": "tl",
    "bottom_left": "bl", "bottom-left": "bl", "bottom_right": "br", "bottom-right": "br",
    "q1": "tr", "q2": "tl", "q3": "bl", "q4": "br",  # standard math convention
}


class QuadrantAdapter(WidgetAdapter):
    type = "quadrant"

    def normalize(self, raw: Any, props: dict) -> dict:
        out: dict = {}

        # Plot mode: list of point dicts with x/y
        if isinstance(raw, list):
            if raw and isinstance(raw[0], dict) and "x" in raw[0] and "y" in raw[0]:
                out["mode"] = "plot"
                out["plot_items"] = self._coerce_plot(raw)
                return out
            # Otherwise treat list as bucket items needing quadrant key — fail
            raise NormalizeError("quadrant: list input must be plot points with x/y")

        if not isinstance(raw, dict):
            raise NormalizeError(f"quadrant: unsupported input type {type(raw).__name__}")

        if "caption" in raw:
            out["caption"] = truncate(str(raw["caption"]), 200)

        # Pre-shaped passthrough
        if "plot_items" in raw and isinstance(raw["plot_items"], list):
            out["mode"] = "plot"
            out["plot_items"] = self._coerce_plot(raw["plot_items"])
            return out
        if "bucket_items" in raw and isinstance(raw["bucket_items"], list):
            out["mode"] = "bucket"
            out["bucket_items"] = self._coerce_bucket_items(raw["bucket_items"])
            return out

        # Bucket dict: {tl|tr|bl|br|q1..q4|top_right: [items]}
        bucket: dict[str, list[Any]] = {}
        for k, v in raw.items():
            if k == "caption":
                continue
            q = _QUADRANT_ALIASES.get(str(k).strip().lower())
            if q and isinstance(v, list):
                bucket[q] = v
        if bucket:
            items: list[dict] = []
            counter = 0
            for q, entries in bucket.items():
                for e in entries:
                    text = str(e).strip() if not isinstance(e, dict) else str(e.get("text", "")).strip()
                    if not text:
                        continue
                    counter += 1
                    item: dict = {
                        "id": to_slug(text)[:48] + f"_{counter}" if to_slug(text) else f"item_{counter}",
                        "quadrant": q,
                        "text": truncate(text, 500),
                    }
                    items.append(item)
            if not items:
                raise NormalizeError("quadrant: bucket mode had no items")
            out["mode"] = "bucket"
            out["bucket_items"] = items
            return out

        raise NormalizeError("quadrant: could not infer mode (need plot points or bucket dict)")

    def _coerce_plot(self, raw_list: list[Any]) -> list[dict]:
        out: list[dict] = []
        for i, entry in enumerate(raw_list, 1):
            if not isinstance(entry, dict):
                continue
            x = coerce_number(entry.get("x"))
            y = coerce_number(entry.get("y"))
            if x is None or y is None:
                continue
            label = str(entry.get("label", "")).strip()
            pid = str(entry.get("id") or to_slug(label) or f"p_{i}")[:64]
            item: dict = {"id": pid, "x": x, "y": y}
            if label:
                item["label"] = truncate(label, 200)
            for k in ("size", "color", "group", "note"):
                if entry.get(k) is not None:
                    item[k] = entry[k]
            out.append(item)
        if not out:
            raise NormalizeError("quadrant: plot mode had no valid points")
        return out

    def _coerce_bucket_items(self, raw_list: list[Any]) -> list[dict]:
        out: list[dict] = []
        for i, entry in enumerate(raw_list, 1):
            if not isinstance(entry, dict):
                continue
            q = _QUADRANT_ALIASES.get(str(entry.get("quadrant", "")).strip().lower())
            if not q:
                continue
            text = str(entry.get("text", "")).strip()
            iid = str(entry.get("id") or to_slug(text) or f"b_{i}")[:64]
            item: dict = {"id": iid, "quadrant": q}
            if text:
                item["text"] = truncate(text, 500)
            for k in ("color", "weight"):
                if entry.get(k) is not None:
                    item[k] = entry[k]
            out.append(item)
        return out

    def fallback_to(self) -> str:
        return "table"
