"""Flowchart adapter — sequential steps rendered horizontally or vertically."""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import nearest_enum, truncate

_ORIENTATION = ["horizontal", "vertical"]


class FlowchartAdapter(WidgetAdapter):
    type = "flowchart"

    def normalize(self, raw: Any, props: dict) -> dict:
        out: dict = {}
        items_in: list[Any]

        if isinstance(raw, dict):
            if "caption" in raw:
                out["caption"] = truncate(str(raw["caption"]), 200)
            # v0.9.2 — RA defcb74 caption color tokens
            if isinstance(raw.get("caption_color"), str):
                out["caption_color"] = raw["caption_color"]
            if isinstance(raw.get("caption_html"), str):
                out["caption_html"] = raw["caption_html"][:2000]
            if "orientation" in raw:
                o = nearest_enum(raw["orientation"], _ORIENTATION)
                if o is not None:
                    out["orientation"] = o
            if isinstance(raw.get("items"), list):
                items_in = raw["items"]
            elif isinstance(raw.get("steps"), list):
                items_in = raw["steps"]
            elif isinstance(raw.get("nodes"), list):
                items_in = raw["nodes"]
            else:
                raise NormalizeError("flowchart: dict input needs 'items'/'steps'/'nodes' list")
        elif isinstance(raw, list):
            items_in = raw
        elif isinstance(raw, str):
            # Single arrow-separated string → split into steps
            parts = [p.strip() for p in raw.replace("→", "->").split("->")]
            items_in = [p for p in parts if p]
        else:
            raise NormalizeError(f"flowchart: unsupported input type {type(raw).__name__}")

        items_out: list[dict] = []
        for entry in items_in:
            if isinstance(entry, str):
                label = entry.strip()
                if label:
                    items_out.append({"label": truncate(label, 200)})
            elif isinstance(entry, dict):
                label = str(entry.get("label", "")).strip()
                if not label:
                    continue
                item: dict = {"label": truncate(label, 200)}
                if entry.get("description"):
                    item["description"] = truncate(str(entry["description"]), 1000)
                items_out.append(item)

        if not items_out:
            raise NormalizeError("flowchart: no items after normalization")
        out["items"] = items_out
        return out

    def fallback_to(self) -> str:
        return "bulleted_list"
