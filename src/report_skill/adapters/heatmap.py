"""Heatmap adapter — 2D numeric matrix with optional row/col labels.
Accepts 2D list, list-of-{x,y,value} dicts, or pre-shaped dict.
"""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import coerce_number, truncate

_PASSTHROUGH = (
    "caption", "caption_skip_autofill", "colorscale", "reverse_scale",
    "z_min", "z_max", "x_axis_title", "y_axis_title", "show_values",
    # v0.9.1 — RA defcb74 caption color tokens
    "caption_color", "caption_html",
)


class HeatmapAdapter(WidgetAdapter):
    type = "heatmap"

    def normalize(self, raw: Any, props: dict) -> dict:
        out: dict = {}
        x_labels: list[str] | None = None
        y_labels: list[str] | None = None
        matrix: list[list[float | None]] | None = None

        if isinstance(raw, dict):
            for k in _PASSTHROUGH:
                if k in raw:
                    out[k] = raw[k]
            if isinstance(raw.get("x_labels"), list):
                x_labels = [str(v) for v in raw["x_labels"]]
            if isinstance(raw.get("y_labels"), list):
                y_labels = [str(v) for v in raw["y_labels"]]
            if isinstance(raw.get("matrix"), list):
                matrix = _coerce_matrix(raw["matrix"])
            elif isinstance(raw.get("rows"), list):
                matrix, x_labels, y_labels = _rows_to_matrix(raw["rows"], x_labels, y_labels)
            else:
                raise NormalizeError("heatmap: dict input needs 'matrix' or 'rows'")
        elif isinstance(raw, list):
            if raw and isinstance(raw[0], dict):
                matrix, x_labels, y_labels = _rows_to_matrix(raw, None, None)
            elif raw and isinstance(raw[0], (list, tuple)):
                matrix = _coerce_matrix(raw)
            else:
                raise NormalizeError("heatmap: list must contain rows or dicts")
        else:
            raise NormalizeError(f"heatmap: unsupported input type {type(raw).__name__}")

        if not matrix or not any(matrix):
            raise NormalizeError("heatmap: empty matrix")

        out["matrix"] = matrix
        if x_labels:
            out["x_labels"] = x_labels
        if y_labels:
            out["y_labels"] = y_labels
        for k in ("x_axis_title", "y_axis_title"):
            if k not in out and props.get(k):
                out[k] = truncate(str(props[k]), 100)
        return out

    def fallback_to(self) -> str:
        return "table"


def _coerce_matrix(rows: list[Any]) -> list[list[float | None]]:
    out: list[list[float | None]] = []
    for row in rows:
        if not isinstance(row, (list, tuple)):
            continue
        out.append([coerce_number(c) for c in row])
    return out


def _rows_to_matrix(rows: list[Any], x_labels: list[str] | None,
                    y_labels: list[str] | None
                    ) -> tuple[list[list[float | None]], list[str], list[str]]:
    """Pivot list of {x,y,value|z} dicts into matrix + axis labels."""
    xs: list[str] = list(x_labels) if x_labels else []
    ys: list[str] = list(y_labels) if y_labels else []
    cells: dict[tuple[str, str], float | None] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        x = r.get("x")
        y = r.get("y")
        v = r.get("value") if "value" in r else r.get("z")
        if x is None or y is None:
            continue
        sx, sy = str(x), str(y)
        if sx not in xs:
            xs.append(sx)
        if sy not in ys:
            ys.append(sy)
        cells[(sx, sy)] = coerce_number(v)
    matrix = [[cells.get((x, y)) for x in xs] for y in ys]
    return matrix, xs, ys
