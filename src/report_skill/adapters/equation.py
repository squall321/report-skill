"""Equation adapter — LaTeX string with optional caption / display mode / number."""
from __future__ import annotations

import re
from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import nearest_enum, truncate

_DISPLAY_ENUM = ["display", "inline"]
# Single backslash not part of an existing double-backslash, escape sequence,
# or known TeX command — leave intact; the goal is just to repair lone `\` from
# JSON un-escaping.
_LONE_BACKSLASH = re.compile(r"(?<!\\)\\(?![\\a-zA-Z{}\[\]()|^&%$#_~ ])")


class EquationAdapter(WidgetAdapter):
    type = "equation"

    def normalize(self, raw: Any, props: dict) -> dict:
        if isinstance(raw, str):
            latex = _clean_latex(raw)
            if not latex:
                raise NormalizeError("equation: empty latex")
            return {"latex": truncate(latex, 5000)}

        if isinstance(raw, dict):
            src = raw.get("latex") or raw.get("formula") or raw.get("equation")
            if not isinstance(src, str) or not src.strip():
                raise NormalizeError("equation: dict input needs non-empty 'latex'")
            out: dict = {"latex": truncate(_clean_latex(src), 5000)}
            if raw.get("caption"):
                out["caption"] = truncate(str(raw["caption"]), 200)
            # v0.9.2 — RA defcb74 caption color tokens
            if isinstance(raw.get("caption_color"), str):
                out["caption_color"] = raw["caption_color"]
            if isinstance(raw.get("caption_html"), str):
                out["caption_html"] = raw["caption_html"][:2000]
            if raw.get("display_mode"):
                dm = nearest_enum(raw["display_mode"], _DISPLAY_ENUM)
                if dm is not None:
                    out["display_mode"] = dm
            if raw.get("number"):
                out["number"] = truncate(str(raw["number"]), 64)
            return out

        raise NormalizeError(f"equation: unsupported input type {type(raw).__name__}")

    def fallback_to(self) -> str:
        return "rich_text"


def _clean_latex(s: str) -> str:
    """Strip $$/\\[\\] wrappers, double-up suspicious lone backslashes."""
    t = s.strip()
    # Strip outer $$...$$ or $...$
    if t.startswith("$$") and t.endswith("$$") and len(t) >= 4:
        t = t[2:-2].strip()
    elif t.startswith("$") and t.endswith("$") and len(t) >= 2:
        t = t[1:-1].strip()
    # Strip \[ ... \] or \( ... \)
    if t.startswith("\\[") and t.endswith("\\]"):
        t = t[2:-2].strip()
    elif t.startswith("\\(") and t.endswith("\\)"):
        t = t[2:-2].strip()
    # Repair lone backslashes (rare; JSON usually normalizes already)
    t = _LONE_BACKSLASH.sub(r"\\\\", t)
    return t
