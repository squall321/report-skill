"""Contour adapter — iso-value 2D surface. Supports matrix or scatter rows mode."""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import coerce_number, truncate

_PASSTHROUGH = (
    "caption", "caption_skip_autofill", "colorscale", "reverse_scale",
    "z_min", "z_max", "x_axis_title", "y_axis_title",
    "ncontours", "contours_coloring", "show_lines", "show_labels", "connect_gaps",
    # v0.9.1 — RA defcb74 caption color tokens
    "caption_color", "caption_html",
)


class ContourAdapter(WidgetAdapter):
    type = "contour"

    def normalize(self, raw: Any, props: dict) -> dict:
        out: dict = {}
        mode: str | None = None
        matrix: list[list[float | None]] | None = None
        rows_xyz: list[dict] | None = None
        x_labels: list[str] | None = None
        y_labels: list[str] | None = None

        if isinstance(raw, dict):
            for k in _PASSTHROUGH:
                if k in raw:
                    out[k] = raw[k]
            if isinstance(raw.get("x_labels"), list):
                x_labels = [str(v) for v in raw["x_labels"]]
            if isinstance(raw.get("y_labels"), list):
                y_labels = [str(v) for v in raw["y_labels"]]
            explicit_mode = raw.get("mode")
            if isinstance(raw.get("matrix"), list):
                matrix = _coerce_matrix(raw["matrix"])
                mode = "matrix"
            elif isinstance(raw.get("rows"), list):
                rows_xyz = _coerce_xyz_rows(raw["rows"])
                mode = explicit_mode if explicit_mode in ("matrix", "rows") else "rows"
            else:
                raise NormalizeError("contour: dict input needs 'matrix' or 'rows'")
        elif isinstance(raw, list):
            if raw and isinstance(raw[0], dict):
                rows_xyz = _coerce_xyz_rows(raw)
                mode = "rows"
            elif raw and isinstance(raw[0], (list, tuple)):
                matrix = _coerce_matrix(raw)
                mode = "matrix"
            else:
                raise NormalizeError("contour: list must contain rows or dicts")
        else:
            raise NormalizeError(f"contour: unsupported input type {type(raw).__name__}")

        if mode == "matrix":
            if not matrix or not any(matrix):
                raise NormalizeError("contour: empty matrix")
            out["mode"] = "matrix"
            out["matrix"] = matrix
            if x_labels:
                out["x_labels"] = x_labels
            if y_labels:
                out["y_labels"] = y_labels
        else:
            if not rows_xyz:
                raise NormalizeError("contour: no usable {x,y,z} rows")
            out["mode"] = "rows"
            out["rows"] = rows_xyz

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


def _coerce_xyz_rows(rows: list[Any]) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        x = coerce_number(r.get("x"))
        y = coerce_number(r.get("y"))
        z = coerce_number(r.get("z") if "z" in r else r.get("value"))
        if x is None and y is None and z is None:
            continue
        out.append({"x": x, "y": y, "z": z})
    return out
