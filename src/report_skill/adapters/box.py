"""Box plot adapter — flattens multi-series 1D data into {group, value} rows."""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import coerce_number, truncate

_PASSTHROUGH = (
    "caption", "caption_skip_autofill", "unit", "x_axis_title", "y_axis_title",
    "orientation", "y_min", "y_max", "box_points", "box_mean", "jitter",
)


class BoxAdapter(WidgetAdapter):
    type = "box"

    def normalize(self, raw: Any, props: dict) -> dict:
        out: dict = {}
        rows: list[dict] = []

        if isinstance(raw, dict):
            for k in _PASSTHROUGH:
                if k in raw:
                    out[k] = raw[k]
            if isinstance(raw.get("rows"), list):
                rows = _coerce_flat_rows(raw["rows"])
            elif isinstance(raw.get("groups"), list):
                rows = _from_series(raw["groups"])
            else:
                mapping = {k: v for k, v in raw.items()
                           if k not in _PASSTHROUGH and k not in ("rows", "groups")}
                rows = _from_mapping(mapping)
        elif isinstance(raw, list):
            if raw and isinstance(raw[0], dict) and ("group" in raw[0] and "value" in raw[0]):
                rows = _coerce_flat_rows(raw)
            elif raw and isinstance(raw[0], dict):
                rows = _from_series(raw)
            else:
                raise NormalizeError("box: list items must be dicts")
        else:
            raise NormalizeError(f"box: unsupported input type {type(raw).__name__}")

        if not rows:
            raise NormalizeError("box: no usable rows")
        out["rows"] = rows
        for k in ("x_axis_title", "y_axis_title"):
            if k not in out and props.get(k):
                out[k] = truncate(str(props[k]), 100)
        return out

    def fallback_to(self) -> str:
        return "table"


def _coerce_flat_rows(items: list[Any]) -> list[dict]:
    out: list[dict] = []
    for r in items:
        if not isinstance(r, dict):
            continue
        g = r.get("group") or r.get("name") or r.get("label")
        if g is None:
            continue
        v = coerce_number(r.get("value"))
        out.append({"group": str(g)[:200], "value": v})
    return out


def _from_series(items: list[Any]) -> list[dict]:
    out: list[dict] = []
    for i, s in enumerate(items):
        if not isinstance(s, dict):
            continue
        name = s.get("name") or s.get("label") or s.get("group") or f"Group {i+1}"
        vals = s.get("values")
        if not isinstance(vals, (list, tuple)):
            continue
        gname = str(name)[:200]
        for v in vals:
            n = coerce_number(v)
            if n is not None:
                out.append({"group": gname, "value": n})
    return out


def _from_mapping(mapping: dict) -> list[dict]:
    out: list[dict] = []
    for name, seq in mapping.items():
        if not isinstance(seq, (list, tuple)):
            continue
        gname = str(name)[:200]
        for v in seq:
            n = coerce_number(v)
            if n is not None:
                out.append({"group": gname, "value": n})
    return out
