"""Scatter3d adapter — accepts list-of-dicts (numeric x/y/z) or list of
[x, y, z] triples, produces scatter3d content with columns + rows + series.
"""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import coerce_number, to_slug, truncate


class Scatter3dAdapter(WidgetAdapter):
    type = "scatter3d"

    def normalize(self, raw: Any, props: dict) -> dict:
        caption = props.get("caption") or props.get("label")
        columns_in = props.get("columns") or []

        passthrough: dict = {}
        if isinstance(raw, dict):
            for k in ("caption", "x_axis_title", "y_axis_title", "z_axis_title",
                      "colorscale"):
                if k in raw:
                    passthrough[k] = raw[k]
            if isinstance(raw.get("columns"), list) and not columns_in:
                columns_in = raw["columns"]
            if isinstance(raw.get("rows"), list):
                rows_raw = raw["rows"]
            else:
                raise NormalizeError("scatter3d: dict input must contain 'rows' list")
        elif isinstance(raw, list):
            rows_raw = raw
        else:
            raise NormalizeError(f"scatter3d: unsupported input type {type(raw).__name__}")

        if not rows_raw:
            raise NormalizeError("scatter3d: empty rows")

        if not columns_in:
            first = rows_raw[0]
            if isinstance(first, dict):
                columns_in = [{"key": to_slug(str(k)) or f"c{i}",
                               "label": str(k), "type": "number"}
                              for i, k in enumerate(first.keys())]
            elif isinstance(first, (list, tuple)) and len(first) >= 3:
                columns_in = [
                    {"key": "x", "label": "X", "type": "number"},
                    {"key": "y", "label": "Y", "type": "number"},
                    {"key": "z", "label": "Z", "type": "number"},
                ]
            else:
                raise NormalizeError("scatter3d: cannot infer columns from rows")

        columns = _normalize_number_columns(columns_in)
        if len(columns) < 3:
            raise NormalizeError("scatter3d: need at least 3 numeric columns (x, y, z)")

        # Resolve x/y/z keys: prefer props, else first three columns
        keys = [c["key"] for c in columns]
        x_key = props.get("x_column_key") if props.get("x_column_key") in keys else keys[0]
        y_key = props.get("y_column_key") if props.get("y_column_key") in keys else keys[1]
        z_key = props.get("z_column_key") if props.get("z_column_key") in keys else keys[2]

        rows = [_coerce_row(r, columns) for r in rows_raw]
        rows = [r for r in rows
                if r and all(coerce_number(r.get(k)) is not None
                             for k in (x_key, y_key, z_key))]
        if not rows:
            raise NormalizeError("scatter3d: no rows with valid x/y/z values")

        series = [{
            "label": str(props.get("label") or "series"),
            "kind": "scatter3d",
            "x_key": x_key,
            "y_key": y_key,
            "z_key": z_key,
        }]

        out: dict = {
            "mode": "scatter3d",
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
