"""Density adapter — multi-series 1D KDE. Accepts {name: [..]} or list of dicts."""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import coerce_number, truncate

_PASSTHROUGH = (
    "caption", "caption_skip_autofill", "unit", "x_axis_title", "y_axis_title",
    "bandwidth_mode", "bandwidth", "samples", "x_min", "x_max",
    "fill", "show_dots", "dot_opacity",
)


class DensityAdapter(WidgetAdapter):
    type = "density"

    def normalize(self, raw: Any, props: dict) -> dict:
        out: dict = {}
        groups: list[dict] = []

        if isinstance(raw, dict):
            for k in _PASSTHROUGH:
                if k in raw:
                    out[k] = raw[k]
            if isinstance(raw.get("groups"), list):
                groups = _coerce_groups(raw["groups"])
            else:
                # Treat dict as {name: [values...]} mapping (skip known meta keys)
                mapping = {k: v for k, v in raw.items()
                           if k not in _PASSTHROUGH and k != "groups"}
                groups = _from_mapping(mapping)
        elif isinstance(raw, list):
            if raw and isinstance(raw[0], dict):
                groups = _coerce_groups(raw)
            else:
                raise NormalizeError("density: list items must be group dicts")
        else:
            raise NormalizeError(f"density: unsupported input type {type(raw).__name__}")

        if not groups:
            raise NormalizeError("density: no groups produced")
        out["groups"] = groups
        for k in ("x_axis_title", "y_axis_title"):
            if k not in out and props.get(k):
                out[k] = truncate(str(props[k]), 100)
        return out

    def fallback_to(self) -> str:
        return "table"


def _coerce_values(seq: Any) -> list[float | None]:
    if not isinstance(seq, (list, tuple)):
        return []
    out: list[float | None] = []
    for v in seq:
        n = coerce_number(v)
        if n is not None:
            out.append(n)
    return out


def _coerce_groups(items: list[Any]) -> list[dict]:
    out: list[dict] = []
    for i, s in enumerate(items):
        if not isinstance(s, dict):
            continue
        name = s.get("name") or s.get("label") or f"Group {i+1}"
        vals = _coerce_values(s.get("values"))
        if not vals:
            continue
        g: dict = {"name": str(name)[:200], "values": vals}
        if isinstance(s.get("color"), str):
            g["color"] = s["color"][:64]
        out.append(g)
    return out


def _from_mapping(mapping: dict) -> list[dict]:
    out: list[dict] = []
    for name, seq in mapping.items():
        vals = _coerce_values(seq)
        if vals:
            out.append({"name": str(name)[:200], "values": vals})
    return out
