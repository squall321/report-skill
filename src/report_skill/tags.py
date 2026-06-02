"""Lightweight tag inference for report drafts.

Pure-stdlib (regex + counting). No NLP packages — the goal is "good enough
defaults so the orchestrator can suggest tags when the user hasn't supplied
any", not topic modelling.

Strategy summary (ordered, highest priority first):

  1. **Explicit hashtags** in title/body (`#weekly`, `#백엔드`) — taken verbatim.
  2. **Category vocab** match against a curated set
     (weekly / monthly / incident / release / rfc / meeting / retro / postmortem).
  3. **Date markers** detected in the text → emit `YYYY` and `YYYY-Q{1..4}` tags.
  4. **Existing tags** passed in by caller — merged after dedupe.
  5. **Frequency keywords** — top tokens (Korean + English) after stopword
     filtering, used to fill remaining slots up to `max_tags`.

The result is a slug-style list (lowercased, hyphen-separated, dedup,
order-preserving) capped to `max_tags`. Hangul tokens are preserved as-is.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, Optional

# --------------------------------------------------------------------------- #
# vocabularies
# --------------------------------------------------------------------------- #

# Stopwords kept intentionally short. We err on the side of NOT filtering
# something potentially meaningful — false-positive stopword removal silently
# loses signal, whereas a noisy candidate is visible and easy to override.
_STOPWORDS: frozenset[str] = frozenset({
    # English
    "the", "a", "an", "and", "or", "but", "of", "in", "on", "at", "to", "for",
    "from", "by", "with", "as", "is", "are", "was", "were", "be", "been",
    "being", "this", "that", "these", "those", "it", "its", "we", "you",
    "they", "i", "he", "she", "him", "her", "them", "us", "our", "your",
    "their", "my", "me", "not", "no", "do", "does", "did", "has", "have",
    "had", "will", "would", "could", "should", "can", "may", "might",
    "than", "then", "so", "if", "about", "into", "over", "out", "up",
    "down", "all", "any", "each", "more", "most", "other", "some",
    "such", "only", "own", "same", "very", "just", "via", "etc",
    # Korean particles + filler — single chars dominate, but a few common
    # bigrams ("그리고", "하지만") sneak in often enough to be worth listing.
    "는", "은", "이", "가", "을", "를", "에", "와", "과", "의", "도",
    "로", "으로", "에서", "에게", "께", "께서", "한테", "보다", "처럼",
    "마다", "조차", "마저", "뿐", "이다", "있다", "없다", "한다",
    "이나", "거나", "라고", "라며", "라는", "이라는", "그리고", "하지만",
    "그러나", "또한", "또는", "즉", "위", "아래", "안", "밖",
    "것", "수", "등", "및", "위한", "통한", "대한", "통해", "위해",
})

# Known template-category hints. If a keyword is present, the canonical
# tag fires regardless of frequency. Matches are case-insensitive substring
# on the joined title+body string (Korean tokens included for the common
# templates we know exist in ReportArchive).
_CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "weekly":     ("weekly", "주간", "weekly report"),
    "monthly":    ("monthly", "월간"),
    "daily":      ("daily", "일간", "일일"),
    "incident":   ("incident", "장애", "outage", "사고"),
    "postmortem": ("postmortem", "post-mortem", "사후분석", "회고"),
    "retro":      ("retrospective", "retro", "회고"),
    "release":    ("release", "릴리스", "릴리즈", "배포"),
    "rfc":        ("rfc", "design doc", "design-doc", "제안서"),
    "meeting":    ("meeting", "회의록", "미팅", "minutes"),
    "kickoff":    ("kickoff", "킥오프"),
    "demo":       ("demo", "데모"),
    "research":   ("research", "연구", "조사"),
    "experiment": ("experiment", "실험"),
}

# Hashtag extractor — captures the token after `#`. Allows Hangul + ASCII +
# digits + hyphen + underscore; stops at whitespace or punctuation.
_HASHTAG_RE = re.compile(r"#([\w가-힣\-]{2,})", re.UNICODE)

# Word tokenizer for frequency counting. Splits on anything that's not
# a letter/digit/underscore/hyphen, then we filter further below.
_TOKEN_RE = re.compile(r"[\w가-힣\-]+", re.UNICODE)

# Date markers — supports `2026-05-26`, `2026/05/26`, `2026.05`,
# `2026년 5월`, plain `2026`. Year capture is always group 1.
_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2}|19\d{2})(?:[\-/.]\s*(\d{1,2}))?(?:년|\s*월)?"
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _slugify(tok: str) -> str:
    """Lowercase, collapse internal whitespace to '-', strip edge punctuation.

    Hangul + ASCII letters/digits are preserved; everything else becomes
    a hyphen, with consecutive hyphens collapsed and edges stripped.
    """
    s = tok.strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^\w가-힣\-]+", "-", s, flags=re.UNICODE)
    s = re.sub(r"-{2,}", "-", s).strip("-_")
    return s


def _quarter_for(month: int) -> int:
    return (month - 1) // 3 + 1


def _dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


# --------------------------------------------------------------------------- #
# strategy 1 — explicit hashtags
# --------------------------------------------------------------------------- #
def _extract_hashtags(text: str) -> list[str]:
    return [_slugify(m) for m in _HASHTAG_RE.findall(text) if _slugify(m)]


# --------------------------------------------------------------------------- #
# strategy 2 — category vocab
# --------------------------------------------------------------------------- #
def _extract_categories(text: str) -> list[str]:
    haystack = text.lower()
    hits: list[str] = []
    for canonical, needles in _CATEGORY_HINTS.items():
        if any(n.lower() in haystack for n in needles):
            hits.append(canonical)
    return hits


# --------------------------------------------------------------------------- #
# strategy 3 — date markers
# --------------------------------------------------------------------------- #
def _extract_date_tags(text: str) -> list[str]:
    tags: list[str] = []
    for m in _DATE_RE.finditer(text):
        year = m.group(1)
        month_raw = m.group(2)
        tags.append(year)
        if month_raw:
            try:
                month = int(month_raw)
                if 1 <= month <= 12:
                    tags.append(f"{year}-q{_quarter_for(month)}")
            except ValueError:
                pass
    return _dedupe_preserve_order(tags)


# --------------------------------------------------------------------------- #
# strategy 5 — frequency keywords
# --------------------------------------------------------------------------- #
def _extract_keywords(text: str, *, limit: int) -> list[str]:
    if limit <= 0:
        return []
    tokens = _TOKEN_RE.findall(text)
    counts: Counter[str] = Counter()
    for raw in tokens:
        tok = _slugify(raw)
        if not tok:
            continue
        # Drop short noise + numeric-only tokens (years are already
        # handled by the date strategy; bare numbers add no signal).
        if len(tok) < 2:
            continue
        if tok.isdigit():
            continue
        if tok in _STOPWORDS:
            continue
        counts[tok] += 1
    # `most_common` is deterministic for ties because Counter preserves
    # first-insertion order.
    return [tok for tok, _ in counts.most_common(limit)]


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #
def infer_tags(
    *,
    title: str = "",
    body_text: str = "",
    existing_tags: Optional[list[str]] = None,
    max_tags: int = 5,
) -> list[str]:
    """Suggest tags from `title` + `body_text`, capped at `max_tags`.

    See module docstring for the strategy stack. Priority order when more
    candidates exist than `max_tags`:

        1. hashtags > 2. categories > 3. date markers >
        4. caller's `existing_tags` > 5. frequency keywords (fills the tail).

    Returns a fresh list; never mutates `existing_tags`.
    """
    if max_tags <= 0:
        return []

    text = f"{title}\n{body_text}".strip()
    if not text and not existing_tags:
        return []

    hashtags = _extract_hashtags(text)
    categories = _extract_categories(text)
    dates = _extract_date_tags(text)
    pre_existing = [_slugify(t) for t in (existing_tags or []) if _slugify(t)]

    # Ordered merge of the deterministic strategies first…
    merged = _dedupe_preserve_order(
        [*hashtags, *categories, *dates, *pre_existing]
    )

    # …then top up with keyword candidates if we still have room. We over-
    # request and re-dedupe so already-claimed slots don't shrink the
    # final keyword headroom.
    remaining = max_tags - len(merged)
    if remaining > 0:
        keyword_pool = _extract_keywords(text, limit=remaining + len(merged) + 5)
        merged = _dedupe_preserve_order([*merged, *keyword_pool])

    return merged[:max_tags]
