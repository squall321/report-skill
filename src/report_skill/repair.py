"""Deterministic repair helpers for common adapter / LLM output mistakes.

These are all *deterministic* — no LLM calls. They handle the obvious
near-misses that show up in real model output: wrong-case enum values,
strings that should be numbers, missing required fields with sensible
defaults, slug-ifying labels into keys, etc.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any, Optional

# --------------------------------------------------------------------------- #
# Slug / key helpers
# --------------------------------------------------------------------------- #
_SLUG_BAD = re.compile(r"[^a-z0-9_]+")


def to_slug(text: str) -> str:
    """ASCII-only slug that matches `^[a-z][a-z0-9_]*$` (or empty if no match)."""
    s = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    s = s.lower().strip()
    s = _SLUG_BAD.sub("_", s)
    s = s.strip("_")
    if not s:
        return ""
    if not s[0].isalpha():
        s = "x_" + s
    return s[:64]


# --------------------------------------------------------------------------- #
# Enum near-match
# --------------------------------------------------------------------------- #
def nearest_enum(value: Any, options: list[Any]) -> Optional[Any]:
    """Return the option closest to `value` if confidently similar, else None.

    Strategy:
      1. Exact match (case-insensitive for strings).
      2. Substring containment either direction.
      3. Levenshtein distance ≤ 2 for short strings.
    """
    if value is None:
        return None
    sval = str(value).strip().lower()
    if not sval:
        return None

    # 1. exact (case-insensitive)
    for opt in options:
        if str(opt).strip().lower() == sval:
            return opt

    # 2. substring
    for opt in options:
        sopt = str(opt).strip().lower()
        if sval in sopt or sopt in sval:
            return opt

    # 3. distance — accept if within 2 edits AND within len(value)+1 (relaxed
    # for short strings like 2-char Korean enums where strict <-len fails).
    # Tie-break: prefer the option closest to the middle of the list. For ordered
    # enums like [낮음, 보통, 높음] this avoids picking 낮음 just because it's first.
    mid_idx = len(options) // 2
    best, best_d, best_mid = None, 999, 999
    for i, opt in enumerate(options):
        d = _levenshtein(sval, str(opt).strip().lower())
        mid_dist = abs(i - mid_idx)
        if (d, mid_dist) < (best_d, best_mid):
            best, best_d, best_mid = opt, d, mid_dist
    if best is not None and best_d <= 2 and best_d <= max(len(sval), 2):
        return best
    return None


def force_enum(value: Any, options: list[Any]) -> Optional[Any]:
    """Aggressive enum coercion: tries nearest_enum, falls back to the
    middle option if options is non-empty. Used when the downstream widget
    UI can't render off-enum values gracefully (eg select columns)."""
    m = nearest_enum(value, options)
    if m is not None:
        return m
    if options:
        # Middle option is usually the "neutral" choice for ordered enums
        # like severity (낮음/보통/높음).
        return options[len(options) // 2]
    return None


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(
                prev[j] + 1,
                cur[j - 1] + 1,
                prev[j - 1] + (ca != cb),
            ))
        prev = cur
    return prev[-1]


# --------------------------------------------------------------------------- #
# Type coercion
# --------------------------------------------------------------------------- #
_TRUE_TOKENS = {"true", "yes", "y", "예", "네", "참", "o", "ㅇ", "on", "1"}
_FALSE_TOKENS = {"false", "no", "n", "아니오", "아니요", "거짓", "x", "ㅌ", "off", "0"}

_KO_DATE = re.compile(r"^\s*(\d{4})[./\-]\s*(\d{1,2})[./\-]\s*(\d{1,2})\s*$")
_KO_DATE_HANGEUL = re.compile(r"^\s*(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*$")


def coerce_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _TRUE_TOKENS:
            return True
        if v in _FALSE_TOKENS:
            return False
    return None


def coerce_number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def coerce_integer(value: Any) -> Optional[int]:
    n = coerce_number(value)
    if n is None:
        return None
    return int(round(n))


def coerce_iso_date(value: Any) -> Optional[str]:
    """Coerce common date formats to ISO YYYY-MM-DD."""
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    for r in (_KO_DATE, _KO_DATE_HANGEUL):
        m = r.match(s)
        if m:
            y, mo, d = (int(x) for x in m.groups())
            try:
                return date(y, mo, d).strftime("%Y-%m-%d")
            except ValueError:
                return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# String hygiene
# --------------------------------------------------------------------------- #
def truncate(value: str, max_len: int) -> str:
    if len(value) <= max_len:
        return value
    return value[: max(0, max_len - 1)].rstrip() + "…"


_BULLET_PREFIX = re.compile(r"^\s*([-*•·●○▶▪]|\d+[.)])\s+")


def strip_bullet(line: str) -> str:
    return _BULLET_PREFIX.sub("", line).strip()
