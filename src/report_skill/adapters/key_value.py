from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import to_slug

# Reserved top-level keys per the content schema's patternProperties exclusion.
_RESERVED = {"caption", "caption_skip_autofill", "items"}


class KeyValueAdapter(WidgetAdapter):
    type = "key_value"

    def normalize(self, raw: Any, props: dict) -> dict:
        if isinstance(raw, list):
            # List of [key, value] pairs or list of {key, value} objects
            return self._from_pairs(raw)
        if isinstance(raw, dict):
            # If it has an "items" array, treat as structured form
            if "items" in raw and isinstance(raw["items"], list):
                # Pass through after light cleanup
                out: dict = {"items": raw["items"]}
                if isinstance(raw.get("caption"), str):
                    out["caption"] = raw["caption"][:200]
                return out
            # Otherwise treat the dict as flat key→value (patternProperties path)
            return self._from_flat_dict(raw)
        raise NormalizeError(f"key_value: unsupported input type {type(raw).__name__}")

    def _from_flat_dict(self, raw: dict) -> dict:
        # Korean / non-ASCII / reserved keys cannot survive slugification.
        # Instead of dropping them (which used to trigger fallback to
        # rich_text and lose the Korean label entirely), redirect any
        # such key to the items[] form which the schema also accepts and
        # which preserves the literal label as the rendered key column.
        slugged: dict = {}
        items: list = []
        for k, v in raw.items():
            slug = to_slug(str(k))
            value = _coerce_scalar(v)
            if slug and slug not in _RESERVED:
                slugged[slug] = value
            else:
                items.append({"key": str(k), "value": value})
        if items and not slugged:
            return {"items": items}
        if slugged and not items:
            return slugged
        if slugged and items:
            # Mixed input — emit unified items[] so labels render uniformly.
            return {"items": [{"key": k, "value": v} for k, v in slugged.items()] + items}
        raise NormalizeError("key_value: empty input")

    def _from_pairs(self, raw: list) -> dict:
        # Pairs form already carries the literal label as `key` — preserve
        # non-slug-friendly (Korean / mixed) labels by emitting items[].
        slugged: dict = {}
        items: list = []
        for entry in raw:
            if isinstance(entry, dict):
                k = entry.get("key") or entry.get("label")
                v = entry.get("value")
            elif isinstance(entry, (list, tuple)) and len(entry) == 2:
                k, v = entry
            else:
                continue
            if not k:
                continue
            slug = to_slug(str(k))
            value = _coerce_scalar(v)
            if slug and slug not in _RESERVED:
                slugged[slug] = value
            else:
                items.append({"key": str(k), "value": value})
        if items and not slugged:
            return {"items": items}
        if slugged and not items:
            return slugged
        if slugged and items:
            return {"items": [{"key": k, "value": v} for k, v in slugged.items()] + items}
        raise NormalizeError("key_value: no usable pairs")

    def fallback_to(self) -> str:
        return "rich_text"


def _coerce_scalar(v: Any):
    """Schema accepts string/number/boolean/null, or arrays thereof."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, list):
        return [x if (x is None or isinstance(x, (str, int, float, bool))) else str(x) for x in v]
    # Dict / unknown → stringify (so the value survives, validator accepts string)
    return str(v)
