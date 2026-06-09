"""Rich-text adapter — converts markdown-flavored prose into the widget's
`items[].text/html` shape so the frontend (DOMPurify sanitizing `<strong>` /
`<em>` / `<u>` etc) actually renders the emphasis instead of showing the
raw `**` markers.

In addition to inline emphasis, the adapter understands the synthetic
`mention://` URL scheme on markdown links and emits the exact `<a>` shape
ReportArchive's ReportLinkMark expects. Three mention forms (see SKILL.md
A3.5 for the user-facing guide):
  - `[label](mention://report/<int_id>?ws=<workspace_slug>)`
  - `[label](mention://dept/<workspace_slug>)`
  - `[label](mention://entity/<int_id>?axis=<entity_type_slug>)`

Ids must come from a prior resolver call (reports_search / workspaces_list
/ entity_types_list / entities_list MCP tools or the equivalent CLI). The
`mention://` URL never reaches the DOM — DOMPurify in
ReportArchive/frontend/src/modules/templates/widgets/RichText.jsx strips
`href`, and navigation runs through a delegated SPA click handler on the
`data-mention-*` attrs.
"""
from __future__ import annotations

import re
from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter


# Allowed HTML output tags match the frontend DOMPurify whitelist (see
# <ReportArchive>/frontend/src/modules/templates/widgets/RichText.jsx):
#   p, span, strong, em, u, s, del, br,
#   a[data-mention-type][data-mention-id][data-mention-ws?][data-mention-axis?].
# The `href` is intentionally `#` — DOMPurify strips it; the frontend
# delegates navigation via a click handler keyed off the data-* attrs.
_RE_BOLD = re.compile(r"\*\*([^\n*][^*]*?)\*\*", re.DOTALL)
_RE_ITALIC = re.compile(r"(?<!\*)\*([^\n*][^*]*?)\*(?!\*)", re.DOTALL)
_RE_STRIKE = re.compile(r"~~([^\n~][^~]*?)~~", re.DOTALL)
_RE_UNDERLINE = re.compile(r"__([^\n_][^_]*?)__", re.DOTALL)

# Mention link form: `[label](mention://<type>/<id>[?<qs>])`. The label may
# contain anything except `]` or a newline (to keep the regex anchored).
# Id is restricted to URL-safe chars; the query string allows `=` `&` plus
# the same id alphabet. _mention_to_html() rejects ids that fail a strict
# `^[A-Za-z0-9_-]+$` after parse, so a malformed match degrades to literal
# escaped text rather than producing a broken anchor.
_RE_MENTION = re.compile(
    r"\[([^\]\n]+)\]\(mention://(report|dept|entity)/([A-Za-z0-9_-]+)(?:\?([A-Za-z0-9_=&-]+))?\)"
)
_RE_MID_SAFE = re.compile(r"^[A-Za-z0-9_-]+$")


def _escape_html(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))


def _parse_mention_query(qs: str | None) -> dict[str, str]:
    """Parse the mention URL's query string into a `ws`/`axis` dict.

    Silently ignores unknown keys and empty values so an LLM sending
    `?ws=&axis=foo` doesn't break — the irrelevant key just drops. Only
    `ws` and `axis` are honored (those are the only data-* attrs
    ReportLinkMark.js reads beyond type+id)."""
    if not qs:
        return {}
    out: dict[str, str] = {}
    for kv in qs.split("&"):
        if "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        if k in ("ws", "axis") and v:
            out[k] = v
    return out


def _mention_to_html(label: str, mtype: str, mid: str, qs: str | None) -> str:
    """Emit the exact `<a>` shape ReportLinkMark's parseHTML expects.

    Attribute order is fixed (type, id, ws?, axis?, class, href) so unit
    tests can do byte-level substring assertions. The `class` is the CSS
    hook the frontend uses (`{report|dept|entity}-mention`) and is always
    emitted. `href="#"` is emitted for parity with the frontend renderHTML
    but DOMPurify strips it — keeping it preserves the editor roundtrip.

    If `mid` fails the strict URL-safe charset check, we DON'T emit an
    anchor — we return the escaped literal markdown so the operator sees
    the mistake instead of getting silent-broken navigation.
    """
    if not _RE_MID_SAFE.match(mid):
        literal = f"[{label}](mention://{mtype}/{mid}" + (f"?{qs}" if qs else "") + ")"
        return _escape_html(literal)
    q = _parse_mention_query(qs)
    attrs = [f'data-mention-type="{mtype}"', f'data-mention-id="{mid}"']
    if mtype == "report" and q.get("ws"):
        attrs.append(f'data-mention-ws="{q["ws"]}"')
    if mtype == "entity" and q.get("axis"):
        attrs.append(f'data-mention-axis="{q["axis"]}"')
    attrs.append(f'class="{mtype}-mention"')
    attrs.append('href="#"')
    return f'<a {" ".join(attrs)}>{_escape_html(label)}</a>'


_SENTINEL_RE = re.compile(r"\x00([a-z]+)(\d+)\x00")


def _md_inline_to_html(text: str) -> str:
    """Convert inline markdown emphasis + mention links to HTML.

    Two-phase design:
      1. STASH — every marker is replaced with a `\\x00<tag><n>\\x00`
         sentinel and the original body (and mention extras) saved in a
         shared `placeholders` dict. Order matters: mentions stashed
         BEFORE bold so emphasis regexes can't chew into the URL or
         label. Bold before italic so italic doesn't eat bold markers.
         Underline (`__x__`) before italic-underscore for the same
         separation.
      2. DECODE — a single recursive `_decode` walks the stashed string;
         plain text between sentinels gets html-escaped, sentinels get
         replaced with their decoded form (a mention `<a>` or a `<tag>`
         wrapping recursively-decoded body). The shared `placeholders`
         dict is reachable from the recursion via closure, so a mention
         nested inside bold (e.g. `**[label](mention://...)**`) decodes
         correctly — the OLD design recursed `_md_inline_to_html(body)`
         and created a fresh placeholders scope that couldn't see the
         outer mention sentinel.
    """
    placeholders: dict[str, tuple[str, tuple]] = {}
    counter = {"n": 0}

    def _stash(prefix: str, body: str, extras: tuple = ()) -> str:
        counter["n"] += 1
        token = f"\x00{prefix}{counter['n']}\x00"
        placeholders[token] = (body, extras)
        return token

    def _sub_emphasis(pattern: re.Pattern, tag: str, source: str) -> str:
        def repl(m: re.Match) -> str:
            return _stash(tag, m.group(1))
        return pattern.sub(repl, source)

    def _sub_mention(source: str) -> str:
        def repl(m: re.Match) -> str:
            label, mtype, mid, qs = m.group(1), m.group(2), m.group(3), m.group(4)
            return _stash("mention", label, extras=(mtype, mid, qs))
        return _RE_MENTION.sub(repl, source)

    stashed = text
    stashed = _sub_mention(stashed)
    stashed = _sub_emphasis(_RE_BOLD, "strong", stashed)
    stashed = _sub_emphasis(_RE_STRIKE, "del", stashed)
    stashed = _sub_emphasis(_RE_UNDERLINE, "u", stashed)
    stashed = _sub_emphasis(_RE_ITALIC, "em", stashed)

    def _decode(s: str) -> str:
        # Split on sentinel pattern. re.split with a 2-capture pattern
        # returns: [literal, tag, num, literal, tag, num, ..., literal].
        parts = _SENTINEL_RE.split(s)
        out: list[str] = []
        i = 0
        while i < len(parts):
            if i % 3 == 0:
                out.append(_escape_html(parts[i]))
                i += 1
            else:
                tag, num = parts[i], parts[i + 1]
                token = f"\x00{tag}{num}\x00"
                body, extras = placeholders.get(token, ("", ()))
                if tag == "mention":
                    out.append(_mention_to_html(body, *extras))
                else:
                    out.append(f"<{tag}>{_decode(body)}</{tag}>")
                i += 2
        return "".join(out)

    return _decode(stashed)


def _has_markdown_emphasis(text: str) -> bool:
    """Quick check whether a paragraph contains any markdown markers worth
    converting. Plain-text paragraphs stay in the `markdown` field (smaller
    payload) — only marker-bearing text gets promoted to the items+html shape.

    Mentions count too: a paragraph that contains ONLY a `[label](mention://...)`
    must promote to the html-bearing form, otherwise the chip never renders."""
    return any(p.search(text) for p in (_RE_BOLD, _RE_ITALIC, _RE_STRIKE, _RE_UNDERLINE, _RE_MENTION))


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
        # so search / accessibility tools see clean content. Mention links
        # collapse to their label so search sees the human prose instead
        # of the `mention://...` URL.
        plain = re.sub(r"\*\*|~~|__", "", para)
        plain = re.sub(r"(?<!\*)\*(?!\*)", "", plain)
        plain = _RE_MENTION.sub(lambda m: m.group(1), plain)
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
                    # B15: preserve per-item `relation` slug (1-32) so the
                    # widget's relation chip (resolved against the report's
                    # related-info entities) survives the adapter round-trip.
                    if "relation" in entry and isinstance(entry["relation"], str):
                        rel = entry["relation"].strip()
                        if rel:
                            item["relation"] = rel[:32]
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
            # B15: surface caption_skip_autofill (bool) so the user-authored
            # caption isn't silently overwritten by the backend's autofill.
            if "caption_skip_autofill" in raw:
                out["caption_skip_autofill"] = bool(raw["caption_skip_autofill"])
            # v0.9.2 — RA defcb74 caption color tokens
            if isinstance(raw.get("caption_color"), str):
                out["caption_color"] = raw["caption_color"]
            if isinstance(raw.get("caption_html"), str):
                out["caption_html"] = raw["caption_html"][:2000]
            if not out:
                import json as _json
                out["markdown"] = _json.dumps(raw, ensure_ascii=False, indent=2)
            return out

        raise NormalizeError(f"rich_text: unsupported input type {type(raw).__name__}")
