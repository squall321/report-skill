"""Adapter base class + shared exceptions."""
from __future__ import annotations

from typing import Any, Optional


class NormalizeError(ValueError):
    """Adapter could not coerce input into widget content shape."""


class WidgetAdapter:
    """Stateless converter: anything-ish raw input → widget content dict.

    Subclasses override `normalize()`. Optionally override `fallback_to()`
    to declare a simpler widget to retry with when this one fails.
    """

    type: str = ""

    def normalize(self, raw: Any, props: dict) -> dict:
        raise NotImplementedError

    def fallback_to(self) -> Optional[str]:
        """Type name of a simpler widget that can handle the same input
        less faithfully. Used by orchestrator's fallback chain. None = no
        fallback (block fails outright)."""
        return None
