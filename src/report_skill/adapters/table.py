"""Table adapter — accepts list-of-dicts, list-of-lists, or markdown table."""
from __future__ import annotations

import re
from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import (
    coerce_integer,
    coerce_iso_date,
    coerce_number,
    force_enum,
    nearest_enum,
    to_slug,
    truncate,
)


class TableAdapter(WidgetAdapter):
    type = "table"

    def normalize(self, raw: Any, props: dict) -> dict:
        columns: list[dict] = props.get("columns", [])
        if not columns:
            raise NormalizeError("table: template block has no columns defined")
        col_by_key: dict[str, dict] = {c["key"]: c for c in columns}
        # Lower-case lookup so model can hand us either "Issue" or "issue".
        col_by_label: dict[str, dict] = {str(c.get("label", "")).strip().lower(): c
                                         for c in columns}

        rows: list[dict]
        extras: dict = {}
        if isinstance(raw, str):
            rows = _parse_markdown_table(raw, columns)
        elif isinstance(raw, list):
            rows = [self._coerce_row(r, columns, col_by_key, col_by_label) for r in raw]
            rows = [r for r in rows if r]
        elif isinstance(raw, dict):
            if not isinstance(raw.get("rows"), list):
                raise NormalizeError("table: dict input must contain 'rows' list")
            inner = self.normalize(raw["rows"], props)
            rows = inner.get("rows", [])
            extras = _extract_content_extras(raw)
        else:
            raise NormalizeError(f"table: unsupported input type {type(raw).__name__}")

        if not rows:
            raise NormalizeError("table: no rows after normalization")
        out: dict = {"rows": rows}
        out.update(extras)
        return out

    def _coerce_row(self, raw: Any, columns: list[dict],
                    by_key: dict[str, dict], by_label: dict[str, dict]) -> dict:
        if not isinstance(raw, dict):
            # Positional list → match by column order
            if isinstance(raw, (list, tuple)):
                return {columns[i]["key"]: _coerce_value(v, columns[i])
                        for i, v in enumerate(raw) if i < len(columns)}
            return {}

        cleaned: dict = {}
        for in_key, in_val in raw.items():
            col = by_key.get(in_key)
            if col is None:
                # Try slugified key, then by label
                slug = to_slug(str(in_key))
                col = by_key.get(slug)
                if col is None:
                    col = by_label.get(str(in_key).strip().lower())
                if col is None:
                    continue  # extra column from the LLM — drop silently
            cleaned[col["key"]] = _coerce_value(in_val, col)
        return cleaned

    def fallback_to(self) -> str:
        return "bulleted_list"


def _coerce_value(value: Any, col: dict) -> Any:
    ctype = col.get("type", "text")
    if value is None:
        return None
    if ctype == "text":
        s = str(value)
        return truncate(s, 4000) if len(s) > 4000 else s
    if ctype == "number":
        n = coerce_number(value)
        return n if n is not None else str(value)
    if ctype == "integer":
        n = coerce_integer(value)
        return n if n is not None else str(value)
    if ctype == "date":
        d = coerce_iso_date(value)
        return d if d is not None else str(value)
    if ctype == "select":
        options = col.get("options") or []
        # tier.S uses nearest_enum (strict — leaves uncertain values intact so
        # the validation error surfaces). tier.M/W use force_enum (aggressive —
        # falls back to the middle option to keep the block validating).
        try:
            from report_skill import tier as _tier
            aggressive = _tier.policy().aggressive_enum_match
        except Exception:
            aggressive = True
        coerce = force_enum if aggressive else nearest_enum
        if col.get("multi") and isinstance(value, list):
            picked: list[Any] = []
            for v in value:
                m = coerce(v, options)
                picked.append(m if m is not None else str(v))
            return picked
        m = coerce(value, options)
        return m if m is not None else str(value)
    return str(value)


def _clean_note(s: str) -> str:
    """Strip a leading ※ (with optional following whitespace) and truncate to
    1000 chars. The renderer auto-prefixes ※ so callers must not include it."""
    text = s.lstrip()
    if text.startswith("※"):
        text = text[1:].lstrip()
    return truncate(text, 1000)


_PASSTHROUGH = (
    "caption", "caption_skip_autofill", "columns",
    # v0.9.0 — RA c2d9663 — per-cell color tokens. Side-table keyed by
    # "rowKey::columnKey" with {bg?, fg?} token enum. Backend validates via
    # _CELL_STYLES_SCHEMA so unknown keys are rejected; we pass through dict
    # untouched and let the server reject anything malformed.
    "cell_styles",
    # v0.9.1 — RA defcb74 caption + note color tokens
    "caption_color", "caption_html", "note_color", "note_html",
)


def _extract_content_extras(raw: dict) -> dict:
    """Passthrough optional v0.5.0+ content fields from a dict input.

    Keys handled: note, column_widths, table_width_px, merges (v0.5.0) +
    caption, caption_skip_autofill, columns (v0.5.2 — per-report override) +
    cell_styles (v0.9.0 — RA c2d9663, per-cell bg/fg color tokens).
    Empty/invalid values are dropped silently."""
    out: dict = {}
    for k in _PASSTHROUGH:
        if k in raw:
            out[k] = raw[k]
    note = raw.get("note")
    if isinstance(note, str):
        cleaned = _clean_note(note)
        if cleaned:
            out["note"] = cleaned
    cw = raw.get("column_widths")
    if isinstance(cw, dict):
        widths: dict[str, int] = {}
        for k, v in cw.items():
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                widths[str(k)] = int(v)
        if widths:
            out["column_widths"] = widths
    tw = raw.get("table_width_px")
    if isinstance(tw, int) and not isinstance(tw, bool):
        out["table_width_px"] = tw
    merges = raw.get("merges")
    if isinstance(merges, list):
        cleaned_merges: list[dict] = []
        for m in merges:
            if not isinstance(m, dict):
                continue
            entry: dict = {}
            for k in ("r", "c", "rs", "cs"):
                v = m.get(k)
                if isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)):
                    entry[k] = int(v)
            if entry:
                cleaned_merges.append(entry)
        if cleaned_merges:
            out["merges"] = cleaned_merges
    return out


_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_SEP_ROW = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def _parse_markdown_table(text: str, columns: list[dict]) -> list[dict]:
    """Best-effort markdown table parser. Maps header cells to column.key by
    label match (case-insensitive); falls back to positional."""
    lines = [ln for ln in text.splitlines() if _TABLE_ROW.match(ln) and not _SEP_ROW.match(ln)]
    if len(lines) < 2:
        return []
    cells = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines]
    header_cells = cells[0]
    body = cells[1:]

    by_label = {str(c.get("label", "")).strip().lower(): c for c in columns}
    by_key = {c["key"]: c for c in columns}

    # Build header → column.key mapping
    header_to_key: list[str] = []
    for h in header_cells:
        col = by_label.get(h.strip().lower()) or by_key.get(to_slug(h))
        header_to_key.append(col["key"] if col else "")

    rows: list[dict] = []
    for row_cells in body:
        row: dict = {}
        for i, cell in enumerate(row_cells):
            if i >= len(header_to_key) or not header_to_key[i]:
                continue
            col = by_key[header_to_key[i]]
            row[col["key"]] = _coerce_value(cell, col)
        if row:
            rows.append(row)
    return rows
