from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import truncate


class HeadingAdapter(WidgetAdapter):
    type = "heading"

    def normalize(self, raw: Any, props: dict) -> dict:
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                raise NormalizeError("heading: empty text")
            return {"text": truncate(text, 200)}
        if isinstance(raw, dict) and "text" in raw:
            text = str(raw["text"]).strip()
            if not text:
                raise NormalizeError("heading: empty text")
            out: dict = {"text": truncate(text, 200)}
            if isinstance(raw.get("level"), int) and raw["level"] in (1, 2, 3):
                out["level"] = raw["level"]
            return out
        raise NormalizeError(f"heading: cannot derive text from {type(raw).__name__}")

    def fallback_to(self) -> str | None:
        return None
