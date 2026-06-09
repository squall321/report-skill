"""Chart adapter — accepts list-of-dicts (or dict with rows/columns) and
produces a bar/line chart content payload (columns + rows + x_column_key).
"""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import coerce_number, to_slug, truncate

_VALID_CHART_TYPES = ("bar", "line")
_VALID_COL_TYPES = ("text", "number")

_PASSTHROUGH = (
    "caption", "caption_skip_autofill", "chart_type",
    "x_axis_title", "y_axis_title",
    "x_min", "x_max", "y_min", "y_max",
    "annotations",
    # v0.9.1 — RA defcb74 caption color tokens
    "caption_color", "caption_html",
)


class ChartAdapter(WidgetAdapter):
    type = "chart"

    def normalize(self, raw: Any, props: dict) -> dict:
        caption = props.get("caption") or props.get("label")
        columns_in = props.get("columns") or []

        # Unwrap dict input
        passthrough: dict = {}
        if isinstance(raw, dict):
            for k in _PASSTHROUGH:
                if k in raw:
                    passthrough[k] = raw[k]
            if isinstance(raw.get("columns"), list) and not columns_in:
                columns_in = raw["columns"]
            if isinstance(raw.get("rows"), list):
                rows_raw = raw["rows"]
            else:
                raise NormalizeError("chart: dict input must contain 'rows' list")
        elif isinstance(raw, list):
            rows_raw = raw
        else:
            raise NormalizeError(f"chart: unsupported input type {type(raw).__name__}")

        if not rows_raw or not isinstance(rows_raw[0], dict):
            raise NormalizeError("chart: rows must be a non-empty list of dicts")

        # Infer columns from first row when not provided
        if not columns_in:
            columns_in = _infer_columns(rows_raw[0])
        columns = _normalize_columns(columns_in, _VALID_COL_TYPES, default_type="number")
        if len(columns) < 2:
            raise NormalizeError("chart: need at least 2 columns (x + 1 series)")

        # Resolve x_column_key
        x_key = props.get("x_column_key") or (
            raw.get("x_column_key") if isinstance(raw, dict) else None
        )
        col_keys = [c["key"] for c in columns]
        if x_key not in col_keys:
            x_key = columns[0]["key"]

        rows = [_coerce_row(r, columns, x_key) for r in rows_raw if isinstance(r, dict)]
        rows = [r for r in rows if r]
        if not rows:
            raise NormalizeError("chart: no rows after normalization")

        chart_type = passthrough.pop("chart_type", None) or props.get("chart_type") or "bar"
        if chart_type not in _VALID_CHART_TYPES:
            chart_type = "bar"

        out: dict = {
            "chart_type": chart_type,
            "x_column_key": x_key,
            "columns": columns,
            "rows": rows,
        }
        if caption:
            out["caption"] = truncate(str(caption), 200)
        out.update(passthrough)
        return out

    def fallback_to(self) -> str:
        return "table"


def _infer_columns(first_row: dict) -> list[dict]:
    cols: list[dict] = []
    for k, v in first_row.items():
        slug = to_slug(str(k)) or "col"
        ctype = "number" if coerce_number(v) is not None else "text"
        cols.append({"key": slug, "label": str(k), "type": ctype})
    return cols


def _normalize_columns(columns: list[dict], allowed_types: tuple[str, ...],
                       default_type: str) -> list[dict]:
    out: list[dict] = []
    for c in columns:
        if not isinstance(c, dict) or "key" not in c:
            continue
        key = to_slug(str(c["key"])) or "col"
        ctype = c.get("type") if c.get("type") in allowed_types else default_type
        out.append({
            "key": key,
            "label": str(c.get("label") or c["key"]),
            "type": ctype,
        })
    return out


def _coerce_row(raw: dict, columns: list[dict], x_key: str) -> dict:
    by_key = {c["key"]: c for c in columns}
    by_label = {str(c.get("label", "")).strip().lower(): c for c in columns}
    row: dict = {}
    for in_key, in_val in raw.items():
        col = by_key.get(in_key) or by_key.get(to_slug(str(in_key))) \
            or by_label.get(str(in_key).strip().lower())
        if col is None:
            continue
        if col["type"] == "number" and col["key"] != x_key:
            n = coerce_number(in_val)
            row[col["key"]] = n if n is not None else None
        else:
            row[col["key"]] = str(in_val) if in_val is not None else None
    return row
