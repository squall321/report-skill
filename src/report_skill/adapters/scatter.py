"""Scatter adapter — accepts list-of-dicts (numeric x + y series) or
list of [x, y] pairs, produces scatter content with columns + rows + series.
"""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import coerce_number, to_slug, truncate

_VALID_MODES = ("scatter", "line", "scatter_line")


class ScatterAdapter(WidgetAdapter):
    type = "scatter"

    def normalize(self, raw: Any, props: dict) -> dict:
        caption = props.get("caption") or props.get("label")
        columns_in = props.get("columns") or []

        passthrough: dict = {}
        if isinstance(raw, dict):
            for k in ("caption", "x_axis_title", "y_axis_title",
                      "x_min", "x_max", "y_min", "y_max", "mode"):
                if k in raw:
                    passthrough[k] = raw[k]
            if isinstance(raw.get("columns"), list) and not columns_in:
                columns_in = raw["columns"]
            if isinstance(raw.get("rows"), list):
                rows_raw = raw["rows"]
            else:
                raise NormalizeError("scatter: dict input must contain 'rows' list")
        elif isinstance(raw, list):
            rows_raw = raw
        else:
            raise NormalizeError(f"scatter: unsupported input type {type(raw).__name__}")

        if not rows_raw:
            raise NormalizeError("scatter: empty rows")

        # Normalize columns (scatter only allows type='number')
        if not columns_in:
            first = rows_raw[0]
            if isinstance(first, dict):
                columns_in = [{"key": to_slug(str(k)) or f"c{i}",
                               "label": str(k), "type": "number"}
                              for i, k in enumerate(first.keys())]
            elif isinstance(first, (list, tuple)) and len(first) >= 2:
                columns_in = [{"key": "x", "label": "X", "type": "number"},
                              {"key": "y", "label": "Y", "type": "number"}]
            else:
                raise NormalizeError("scatter: cannot infer columns from rows")
        columns = _normalize_number_columns(columns_in)
        if len(columns) < 2:
            raise NormalizeError("scatter: need at least 2 numeric columns")

        x_key = props.get("x_column_key") or (
            raw.get("x_column_key") if isinstance(raw, dict) else None
        )
        col_keys = [c["key"] for c in columns]
        if x_key not in col_keys:
            x_key = columns[0]["key"]

        rows = [_coerce_row(r, columns) for r in rows_raw]
        rows = [r for r in rows if r and coerce_number(r.get(x_key)) is not None]
        if not rows:
            raise NormalizeError("scatter: no rows with valid x value")

        # Build one series per non-x numeric column
        series = [{"label": c["label"], "x_key": x_key, "y_key": c["key"]}
                  for c in columns if c["key"] != x_key]

        mode = passthrough.pop("mode", None) or props.get("mode") or "scatter_line"
        if mode not in _VALID_MODES:
            mode = "scatter_line"

        out: dict = {
            "mode": mode,
            "x_column_key": x_key,
            "columns": columns,
            "rows": rows,
            "series": series,
        }
        if caption:
            out["caption"] = truncate(str(caption), 200)
        out.update(passthrough)
        return out

    def fallback_to(self) -> str:
        return "table"


def _normalize_number_columns(columns: list[dict]) -> list[dict]:
    out: list[dict] = []
    for c in columns:
        if not isinstance(c, dict) or "key" not in c:
            continue
        key = to_slug(str(c["key"])) or "col"
        out.append({
            "key": key,
            "label": str(c.get("label") or c["key"]),
            "type": "number",
        })
    return out


def _coerce_row(raw: Any, columns: list[dict]) -> dict:
    if isinstance(raw, (list, tuple)):
        row: dict = {}
        for i, v in enumerate(raw):
            if i >= len(columns):
                break
            n = coerce_number(v)
            row[columns[i]["key"]] = n if n is not None else None
        return row
    if not isinstance(raw, dict):
        return {}
    by_key = {c["key"]: c for c in columns}
    by_label = {str(c.get("label", "")).strip().lower(): c for c in columns}
    row = {}
    for in_key, in_val in raw.items():
        col = by_key.get(in_key) or by_key.get(to_slug(str(in_key))) \
            or by_label.get(str(in_key).strip().lower())
        if col is None:
            continue
        n = coerce_number(in_val)
        row[col["key"]] = n if n is not None else None
    return row
