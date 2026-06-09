"""Comparison-table adapter.

Accepts either of these input shapes, given props.cases = [{key, label}...]:

  1. List of row dicts:
       [
         {"label": "비용", "values": {"as_is": "월 200만원", "to_be": "월 80만원"}},
         {"label": "위험도", "values": {"as_is": "높음", "to_be": "낮음"}}
       ]

  2. Dict-of-dicts (row label → case values):
       {
         "비용":    {"as_is": "월 200만원", "to_be": "월 80만원"},
         "위험도":   {"as_is": "높음",       "to_be": "낮음"}
       }

Image-kind rows (with file_id) are not supported by adapter; pass through if
the caller already produced them.
"""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.adapters.table import _clean_note
from report_skill.repair import to_slug, truncate

# Optional content fields the comparison widget accepts beyond `rows`.
# Anything in this tuple is copied verbatim from a dict-input into the
# normalized payload (after type coercion, if needed). Used both for the
# top-level dict-input branch and the {"rows": [...], ...} envelope branch.
_PASSTHROUGH_SIMPLE = (
    "caption",
    "caption_skip_autofill",
    "horizontal_scroll",
    # v0.9.0 — RA c2d9663 — per-cell color tokens. Side-table keyed by
    # "rowKey::caseKey" with {bg?, fg?} token enum.
    "cell_styles",
    # v0.9.1 — RA defcb74 caption color tokens
    "caption_color", "caption_html",
    # v0.10.0 — RA d62af9d per-cell rich markup. Side-table keyed by
    # "rowKey::caseKey" (same as cell_styles), values = sanitized HTML.
    "cell_html",
)


class ComparisonAdapter(WidgetAdapter):
    type = "comparison"

    def normalize(self, raw: Any, props: dict) -> dict:
        cases = props.get("cases") or []
        case_keys = {c["key"] for c in cases}
        if not case_keys:
            raise NormalizeError("comparison: template block has no cases defined")

        rows_in: list[Any]
        extras: dict | None = None
        if isinstance(raw, list):
            rows_in = raw
        elif isinstance(raw, dict):
            if isinstance(raw.get("rows"), list):
                out = self.normalize(raw["rows"], props)
                _apply_passthrough(out, raw)
                return out
            # dict-of-dicts form: row label → case values. Reserved keys
            # (note / column_widths / row_label_width / table_width_px / merges
            #  plus B13 passthrough: caption, caption_skip_autofill, cases,
            #  horizontal_scroll, max_cases, image_max_height_px) are stripped
            # from the row map so they aren't mistaken for rows.
            _reserved = {"note", "column_widths", "row_label_width",
                         "table_width_px", "merges",
                         "caption", "caption_skip_autofill", "cases",
                         "horizontal_scroll", "max_cases",
                         "image_max_height_px"}
            rows_in = [{"label": k, "values": v}
                       for k, v in raw.items() if k not in _reserved]
            extras = raw
        else:
            raise NormalizeError(f"comparison: unsupported input {type(raw).__name__}")

        rows_out: list[dict] = []
        used_keys: set[str] = set()
        for i, r in enumerate(rows_in):
            row = self._coerce_row(r, case_keys, used_keys, i)
            if row:
                rows_out.append(row)
                used_keys.add(row["key"])

        if not rows_out:
            raise NormalizeError("comparison: no rows after normalization")
        out: dict = {"rows": rows_out}
        if extras is not None:
            _apply_passthrough(out, extras)
        return out

    def _coerce_row(self, raw: Any, case_keys: set[str],
                    used_keys: set[str], idx: int) -> dict | None:
        if not isinstance(raw, dict):
            return None
        label = str(raw.get("label", "")).strip()
        # Build a stable key: prefer explicit, else slug from label, else row_<i>
        key = str(raw.get("key", "")).strip() or to_slug(label) or f"row_{idx}"
        # Ensure uniqueness
        base = key
        n = 2
        while key in used_keys:
            key = f"{base}_{n}"
            n += 1

        # Values: accept dict keyed by case.key (or slug-able variants)
        values_in = raw.get("values", {})
        if not isinstance(values_in, dict):
            return None
        values_out: dict = {}
        for k, v in values_in.items():
            target = k if k in case_keys else _match_case_key(k, case_keys)
            if not target:
                continue
            if isinstance(v, dict) and "file_id" in v:
                # Passthrough image-kind value
                values_out[target] = {k2: v2 for k2, v2 in v.items()
                                      if k2 in {"file_id", "alt", "caption"}}
            else:
                values_out[target] = truncate(str(v), 4000)

        out: dict = {"key": key, "kind": "text", "values": values_out}
        if label:
            out["label"] = truncate(label, 200)
        return out

    def fallback_to(self) -> str:
        return "table"


def _match_case_key(name: str, case_keys: set[str]) -> str | None:
    slug = to_slug(name)
    if slug in case_keys:
        return slug
    return None


def _apply_passthrough(out: dict, raw: dict) -> None:
    """Copy optional content fields from a dict-input into the normalized
    comparison block. Silently ignores wrong types.

    Covers v0.5.0 fields (note/column_widths/row_label_width/table_width_px/
    merges) AND v0.5.2 B13 additions (caption/caption_skip_autofill/cases/
    horizontal_scroll/max_cases/image_max_height_px)."""
    # v0.5.0 fields ---------------------------------------------------------
    note = raw.get("note")
    if isinstance(note, str):
        cleaned = _clean_note(note)
        if cleaned:
            out["note"] = cleaned
    cw = raw.get("column_widths")
    if isinstance(cw, dict):
        out["column_widths"] = {str(k): int(v) for k, v in cw.items()
                                if isinstance(v, (int, float))}
    rlw = raw.get("row_label_width")
    if isinstance(rlw, int) and not isinstance(rlw, bool):
        out["row_label_width"] = rlw
    twp = raw.get("table_width_px")
    if isinstance(twp, int) and not isinstance(twp, bool):
        out["table_width_px"] = twp
    merges = raw.get("merges")
    if isinstance(merges, list):
        out["merges"] = [
            {k: int(v) for k, v in m.items()
             if k in {"r", "c", "rs", "cs"} and isinstance(v, (int, float))}
            for m in merges if isinstance(m, dict)
        ]

    # v0.5.2 B13 simple passthroughs (caption/caption_skip_autofill/
    # horizontal_scroll) — copy verbatim when present.
    for k in _PASSTHROUGH_SIMPLE:
        if k in raw:
            out[k] = raw[k]

    # cases: list of {key, label} (passthrough; orchestrator/server still
    # validates against block props). Coerce only minimal field shape so
    # silent garbage doesn't slip in.
    cases = raw.get("cases")
    if isinstance(cases, list):
        coerced_cases: list[dict] = []
        for c in cases:
            if not isinstance(c, dict):
                continue
            entry: dict = {}
            if "key" in c:
                entry["key"] = str(c["key"])
            if "label" in c:
                entry["label"] = str(c["label"])
            if entry:
                coerced_cases.append(entry)
        if coerced_cases:
            out["cases"] = coerced_cases

    # max_cases (int 2..30) — keep simple int coerce; server clamps range.
    mc = raw.get("max_cases")
    if isinstance(mc, int) and not isinstance(mc, bool):
        out["max_cases"] = mc

    # image_max_height_px (int 80..600).
    imh = raw.get("image_max_height_px")
    if isinstance(imh, int) and not isinstance(imh, bool):
        out["image_max_height_px"] = imh
