from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import truncate

# Optional widget-content fields the adapter passes through alongside text/level.
# Note: backend heading schema (registry.py) does NOT define a `tag` field — it is
# intentionally excluded from passthrough. Caption fields are also not in the
# heading schema (additionalProperties=False) and therefore omitted.
_PASSTHROUGH = (
    "text_style", "margin_bottom_px",
)


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
            for k in _PASSTHROUGH:
                if k in raw:
                    out[k] = raw[k]
            return out
        raise NormalizeError(f"heading: cannot derive text from {type(raw).__name__}")

    def fallback_to(self) -> str | None:
        return None
