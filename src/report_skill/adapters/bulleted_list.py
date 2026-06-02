from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import strip_bullet


class BulletedListAdapter(WidgetAdapter):
    type = "bulleted_list"

    def normalize(self, raw: Any, props: dict) -> dict:
        items: list[str] = []

        if isinstance(raw, str):
            # Split lines, strip leading bullets, drop empties
            for line in raw.splitlines():
                cleaned = strip_bullet(line)
                if cleaned:
                    items.append(cleaned)
        elif isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, str):
                    s = strip_bullet(entry)
                    if s:
                        items.append(s)
                elif isinstance(entry, dict):
                    txt = entry.get("text") or entry.get("label") or entry.get("value")
                    if isinstance(txt, str) and txt.strip():
                        items.append(txt.strip())
        elif isinstance(raw, dict) and isinstance(raw.get("items"), list):
            return self.normalize(raw["items"], props)
        else:
            raise NormalizeError(f"bulleted_list: unsupported input type {type(raw).__name__}")

        if not items:
            raise NormalizeError("bulleted_list: no usable items found")

        return {"items": items}

    def fallback_to(self) -> str:
        return "rich_text"
