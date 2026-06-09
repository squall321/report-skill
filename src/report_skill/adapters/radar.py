"""Radar adapter — multi-axis polar. Builds axis_labels + series + values matrix."""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import coerce_number, truncate

_PASSTHROUGH = (
    "caption", "caption_skip_autofill",
    "value_min", "value_max", "fill_opacity", "show_legend",
    # v0.9.1 — RA defcb74 caption color tokens
    "caption_color", "caption_html",
)


class RadarAdapter(WidgetAdapter):
    type = "radar"

    def normalize(self, raw: Any, props: dict) -> dict:
        out: dict = {}
        axis_labels: list[str] = []
        series: list[dict] = []
        values: list[list[float | None]] = []

        if isinstance(raw, dict):
            for k in _PASSTHROUGH:
                if k in raw:
                    out[k] = raw[k]
            if isinstance(raw.get("axis_labels"), list):
                axis_labels = [str(v) for v in raw["axis_labels"]]
            # Direct passthrough shape
            if isinstance(raw.get("series"), list) and isinstance(raw.get("values"), list):
                series = [_clean_series(s) for s in raw["series"] if isinstance(s, dict)]
                values = [[coerce_number(v) for v in row] for row in raw["values"]
                          if isinstance(row, (list, tuple))]
                if not axis_labels:
                    # synthesize
                    width = max((len(v) for v in values), default=0)
                    axis_labels = [f"Axis {i+1}" for i in range(width)]
                # Auto value_max from items if not given
                _maybe_value_max(raw, out)
            elif isinstance(raw.get("series"), list):
                series, values, axis_labels = _from_series_list(raw["series"], axis_labels)
            else:
                raise NormalizeError("radar: dict input needs 'series'")
        elif isinstance(raw, list):
            if not raw:
                raise NormalizeError("radar: empty list")
            if isinstance(raw[0], dict) and ("values" in raw[0] or "name" in raw[0]):
                series, values, axis_labels = _from_series_list(raw, axis_labels)
            elif isinstance(raw[0], dict) and "axis" in raw[0]:
                # Single-series axis dicts
                axis_labels = [str(r.get("axis", "")) for r in raw if isinstance(r, dict)]
                vals = [coerce_number(r.get("value")) for r in raw if isinstance(r, dict)]
                maxes = [coerce_number(r.get("max")) for r in raw if isinstance(r, dict)]
                series = [{"label": "Series 1"}]
                values = [vals]
                mx = [m for m in maxes if m is not None]
                if mx and "value_max" not in out:
                    out["value_max"] = max(mx)
            else:
                raise NormalizeError("radar: list items must be axis or series dicts")
        else:
            raise NormalizeError(f"radar: unsupported input type {type(raw).__name__}")

        if not series or not values:
            raise NormalizeError("radar: no series/values")
        # Pad/truncate each value row to axis count
        n = len(axis_labels)
        values = [(row + [None] * n)[:n] for row in values]

        out["axis_labels"] = axis_labels
        out["series"] = series
        out["values"] = values
        if "caption" in out:
            out["caption"] = truncate(str(out["caption"]), 200)
        return out

    def fallback_to(self) -> str:
        return "table"


def _clean_series(s: dict) -> dict:
    name = s.get("label") or s.get("name") or "Series"
    out: dict = {"label": str(name)[:200]}
    if isinstance(s.get("color"), str):
        out["color"] = s["color"][:64]
    return out


def _from_series_list(items: list[Any], axis_hint: list[str]
                      ) -> tuple[list[dict], list[list[float | None]], list[str]]:
    """Build series + parallel values matrix from list of {name, values:...}."""
    axes: list[str] = list(axis_hint)
    series: list[dict] = []
    raw_rows: list[Any] = []
    for s in items:
        if not isinstance(s, dict):
            continue
        series.append(_clean_series(s))
        raw_rows.append(s.get("values"))
        # If values is a dict, harvest axis names
        v = s.get("values")
        if isinstance(v, dict):
            for k in v.keys():
                if str(k) not in axes:
                    axes.append(str(k))

    values: list[list[float | None]] = []
    for row in raw_rows:
        if isinstance(row, dict):
            values.append([coerce_number(row.get(a)) for a in axes])
        elif isinstance(row, (list, tuple)):
            nums = [coerce_number(v) for v in row]
            values.append(nums)
            if not axes:
                axes = [f"Axis {i+1}" for i in range(len(nums))]
        else:
            values.append([])
    if not axes and values:
        axes = [f"Axis {i+1}" for i in range(len(values[0]))]
    return series, values, axes


def _maybe_value_max(raw: dict, out: dict) -> None:
    if "value_max" in out:
        return
    vm = coerce_number(raw.get("value_max"))
    if vm is not None:
        out["value_max"] = vm
