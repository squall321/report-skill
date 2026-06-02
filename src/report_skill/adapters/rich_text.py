"""Rich-text adapter — converts markdown-flavored prose into the widget's
`items[].text/html` shape so the frontend (DOMPurify sanitizing `<strong>` /
`<em>` / `<u>` etc) actually renders the emphasis instead of showing the
raw `**` markers.
"""
from __future__ import annotations

import re
from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter


# Allowed HTML output tags match the frontend DOMPurify whitelist (see
# <ReportArchive>/frontend/src/modules/templates/widgets/RichText.jsx):
#   p, span, strong, em, u, s, del, br
# We only emit the inline marks that markdown naturally carries.
_RE_BOLD = re.compile(r"\*\*([^\n*][^*]*?)\*\*", re.DOTALL)
_RE_ITALIC = re.compile(r"(?<!\*)\*([^\n*][^*]*?)\*(?!\*)", re.DOTALL)
_RE_STRIKE = re.compile(r"~~([^\n~][^~]*?)~~", re.DOTALL)
_RE_UNDERLINE = re.compile(r"__([^\n_][^_]*?)__", re.DOTALL)


def _escape_html(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))


def _md_inline_to_html(text: str) -> str:
    """Convert inline markdown emphasis to HTML.

    Order matters: bold (`**x**`) before italic (`*x*`) so the italic
    regex doesn't greedily eat the bold markers. Underline (`__x__`)
    before italic-underscore to keep the same separation.
    """
    # Tokenize markers BEFORE escaping so html-escaping the raw text
    # doesn't corrupt our regex matches. We do this by replacing each
    # marker with a sentinel, escaping the body, then re-inserting tags.
    placeholders: dict[str, str] = {}
    counter = {"n": 0}

    def _stash(prefix: str, body: str) -> str:
        counter["n"] += 1
        token = f"\x00{prefix}{counter['n']}\x00"
        placeholders[token] = body
        return token

    def _sub(pattern: re.Pattern, tag: str, source: str) -> str:
        def repl(m: re.Match) -> str:
            inner = m.group(1)
            return _stash(tag, inner)
        return pattern.sub(repl, source)

    out = text
    out = _sub(_RE_BOLD, "strong", out)
    out = _sub(_RE_STRIKE, "del", out)
    out = _sub(_RE_UNDERLINE, "u", out)
    out = _sub(_RE_ITALIC, "em", out)

    out = _escape_html(out)

    for token, body in placeholders.items():
        # Decode the tag name from token (we encoded it as the prefix).
        # Token shape: \x00<tag><n>\x00 where <tag> is alpha and <n> digits.
        m = re.match(r"\x00([a-z]+)(\d+)\x00", token)
        if not m:
            continue
        tag = m.group(1)
        body_html = _md_inline_to_html(body)  # recurse so nested marks work
        out = out.replace(token, f"<{tag}>{body_html}</{tag}>")
    return out


def _has_markdown_emphasis(text: str) -> bool:
    """Quick check whether a paragraph contains any markdown markers worth
    converting. Plain-text paragraphs stay in the `markdown` field (smaller
    payload) — only marker-bearing text gets promoted to the items+html shape."""
    return any(p.search(text) for p in (_RE_BOLD, _RE_ITALIC, _RE_STRIKE, _RE_UNDERLINE))


def _split_paragraphs(text: str) -> list[str]:
    """Split on blank lines. Preserve internal single \\n as <br>-worthy
    soft breaks within a paragraph (we leave them as literal newlines —
    DOMPurify allows `<br>` but we only emit `<p>` blocks; the editor
    handles soft breaks itself)."""
    parts = re.split(r"\n\s*\n", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _string_to_items(text: str) -> list[dict]:
    """Convert a markdown-flavored string into rich_text `items` entries.

    One paragraph (separated by blank line) becomes one item with depth 0.
    Inline emphasis is converted to allowed HTML inline tags wrapped in
    a single `<p>...</p>`. The original plain text (markers stripped) is
    kept in the `text` field for fallback rendering / search."""
    items: list[dict] = []
    for para in _split_paragraphs(text):
        # Plain text for the `text` field — strip the markdown markers
        # so search / accessibility tools see clean content.
        plain = re.sub(r"\*\*|~~|__", "", para)
        plain = re.sub(r"(?<!\*)\*(?!\*)", "", plain)
        plain = plain[:2000]

        if _has_markdown_emphasis(para):
            html_body = _md_inline_to_html(para)
            items.append({
                "depth": 0,
                "text": plain,
                "html": f"<p>{html_body}</p>"[:8000],
            })
        else:
            # No emphasis → no need for an HTML payload; the frontend
            # falls back to a plain <p>{text}</p> render.
            items.append({"depth": 0, "text": plain})
    return items


class RichTextAdapter(WidgetAdapter):
    type = "rich_text"

    def normalize(self, raw: Any, props: dict) -> dict:
        # String → markdown-aware items (so `**bold**` renders as <strong>).
        if isinstance(raw, str):
            stripped = raw.strip()
            if not stripped:
                raise NormalizeError("rich_text: empty input")
            items = _string_to_items(stripped)
            if not items:
                raise NormalizeError("rich_text: produced no items")
            return {"items": items}

        # List → items shorthand (each entry becomes one item, depth 0).
        if isinstance(raw, list):
            items: list[dict] = []
            for entry in raw:
                if isinstance(entry, str) and entry.strip():
                    items.extend(_string_to_items(entry.strip()))
                elif isinstance(entry, dict) and entry.get("text"):
                    item = {
                        "depth": int(entry.get("depth", 0)),
                        "text": str(entry["text"])[:2000],
                    }
                    if "html" in entry and isinstance(entry["html"], str):
                        item["html"] = entry["html"][:8000]
                    items.append(item)
            if not items:
                raise NormalizeError("rich_text: list had no usable items")
            return {"items": items}

        # Dict — accept passthrough of known fields.
        if isinstance(raw, dict):
            out: dict = {}
            if "items" in raw and isinstance(raw["items"], list):
                out["items"] = raw["items"]
            elif "markdown" in raw and isinstance(raw["markdown"], str) and raw["markdown"].strip():
                # Convert dict-with-markdown into items so emphasis renders.
                out["items"] = _string_to_items(raw["markdown"].strip())
            if "caption" in raw and isinstance(raw["caption"], str):
                out["caption"] = raw["caption"][:200]
            if not out:
                import json as _json
                out["markdown"] = _json.dumps(raw, ensure_ascii=False, indent=2)
            return out

        raise NormalizeError(f"rich_text: unsupported input type {type(raw).__name__}")
