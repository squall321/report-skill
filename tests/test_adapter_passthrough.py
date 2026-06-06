"""White-list lock — for each of the 14 widgets in v0.5.2 audit B1-B16,
exercise the adapter's `normalize()` with a content-schema-shaped dict
and assert each declared passthrough field actually survives the round
trip.

Catches the v0.5.0-class regression where an adapter silently dropped
an optional content field (caption / caption_skip_autofill / colorscale /
…), producing schema-valid but information-losing output.

Source of truth: per-widget content_schema in
d:/ReportArchive/backend/app/widgets/registry.py — the cases below mirror
those shapes verbatim.
"""
from __future__ import annotations

from typing import Any

import pytest

from report_skill.adapters import ADAPTERS


# --------------------------------------------------------------------------- #
# Per-widget probe: (widget_type, raw input, props, expected_surviving_keys)
# --------------------------------------------------------------------------- #
#
# Each `raw` is shaped so the adapter takes the dict-passthrough branch
# (which is the path users hit when they hand-author a content dict that
# already matches the widget content_schema). `expected_keys` is the
# subset of `_PASSTHROUGH` the test guarantees are preserved verbatim.
#
# NB: a few adapters compute the kept value differently (truncation,
# coercion, etc.) so we only assert PRESENCE — that the key did not get
# silently dropped — not exact-equality of every nested value.
PASSTHROUGH_CASES: list[tuple[str, Any, dict, list[str]]] = [
    # ---- B1 pie: chart_type/hole/colorscale/reverse_scale/text_info/
    #              text_position/sort/show_legend/unit/caption*
    (
        "pie",
        {
            "rows": [{"label": "A", "value": 30}, {"label": "B", "value": 70}],
            "caption": "분기별 비중",
            "caption_skip_autofill": True,
            "unit": "%",
            "chart_type": "donut",
            "hole": 0.5,
            "colorscale": "Viridis",
            "reverse_scale": False,
            "text_info": "label+percent",
            "text_position": "outside",
            "sort": True,
            "show_legend": True,
        },
        {},
        ["caption", "caption_skip_autofill", "unit", "chart_type",
         "hole", "colorscale", "reverse_scale", "text_info",
         "text_position", "sort", "show_legend"],
    ),
    # ---- B2 treemap: branchvalues/text_info/colorscale/...
    (
        "treemap",
        {
            "rows": [{"label": "A", "parent": "root", "value": 10}],
            "caption": "분해도",
            "caption_skip_autofill": True,
            "unit": "건",
            "colorscale": "Plasma",
            "reverse_scale": True,
            "text_info": "label+value+percent_parent",
            "branchvalues": "total",
        },
        {},
        ["caption", "caption_skip_autofill", "unit", "colorscale",
         "reverse_scale", "text_info", "branchvalues"],
    ),
    # ---- B3 packing: padding + colorscale + text_info + caption*
    (
        "packing",
        {
            "rows": [{"label": "A", "value": 10}, {"label": "B", "value": 5}],
            "caption": "충진도",
            "caption_skip_autofill": False,
            "unit": "MB",
            "colorscale": "Cividis",
            "reverse_scale": False,
            "text_info": "label+value+percent",
            "padding": 8,
        },
        {},
        ["caption", "caption_skip_autofill", "unit", "colorscale",
         "reverse_scale", "text_info", "padding"],
    ),
    # ---- B4 waffle: cols/grid_rows/shape/fill_direction/show_legend/
    #                 show_value_per_cell
    (
        "waffle",
        {
            "rows": [{"label": "A", "value": 30}, {"label": "B", "value": 70}],
            "caption": "비율 와플",
            "caption_skip_autofill": True,
            "unit": "%",
            "cols": 10,
            "grid_rows": 10,
            "shape": "circle",
            "fill_direction": "column",
            "show_legend": True,
            "show_value_per_cell": False,
        },
        {},
        ["caption", "caption_skip_autofill", "unit", "cols", "grid_rows",
         "shape", "fill_direction", "show_legend", "show_value_per_cell"],
    ),
    # ---- B5 heading: text_style + margin_bottom_px (NO `tag` per RA schema)
    (
        "heading",
        {
            "text": "1) 진행 사항",
            "level": 2,
            "text_style": {"bold": True, "color": "#222"},
            "margin_bottom_px": 12,
        },
        {},
        ["text_style", "margin_bottom_px"],
    ),
    # ---- B6 progress_bar: default_max + unit (per-item value/max/label
    #                       lives inside items[] so we just check the
    #                       widget-level passthrough survives)
    (
        "progress_bar",
        {
            "items": [
                {"label": "API 통합", "value": 60, "max": 100,
                 "status": "in_progress"},
                {"label": "QA", "value": 0, "max": 100, "status": "pending"},
            ],
            "caption": "스프린트 진행률",
            "caption_skip_autofill": False,
            "default_max": 100,
            "unit": "%",
        },
        {"default_max": 100},
        ["caption", "caption_skip_autofill", "default_max", "unit"],
    ),
    # ---- B7 bulleted_list: dict-input branch preserves caption +
    #                        caption_skip_autofill
    (
        "bulleted_list",
        {
            "items": ["A", "B", "C"],
            "caption": "체크리스트",
            "caption_skip_autofill": True,
        },
        {},
        ["caption", "caption_skip_autofill"],
    ),
    # ---- B8 image: caption_skip_autofill + aspect_ratio + max_count +
    #                annotations + note (existing)
    (
        "image",
        {
            "files": [{"file_id": "f_abc", "caption": "도식", "alt": "diagram"}],
            "caption": "메인 사진",
            "caption_skip_autofill": True,
            "aspect_ratio": "16:9",
            "max_count": 5,
            "annotations": [
                {"id": "a1", "type": "rect", "x": 0.1, "y": 0.2,
                 "w": 0.3, "h": 0.4},
            ],
            "note": "왼쪽 패널 참고",
        },
        {},
        ["caption_skip_autofill", "aspect_ratio", "max_count",
         "annotations", "note"],
    ),
    # ---- B9 chart: x_min/x_max/y_min/y_max + annotations + caption*
    (
        "chart",
        {
            "rows": [{"month": "1월", "revenue": 120},
                     {"month": "2월", "revenue": 145}],
            "caption": "월별 매출",
            "caption_skip_autofill": False,
            "chart_type": "line",
            "x_axis_title": "월",
            "y_axis_title": "매출(억)",
            "x_min": 0, "x_max": 12,
            "y_min": 0, "y_max": 200,
            "annotations": [{"id": "a1", "type": "callout"}],
        },
        {},
        ["caption", "caption_skip_autofill", "x_axis_title", "y_axis_title",
         "x_min", "x_max", "y_min", "y_max", "annotations"],
    ),
    # ---- B10 scatter: mode + axes + annotations + caption*
    (
        "scatter",
        {
            "rows": [{"x": 1, "y": 2}, {"x": 3, "y": 4}],
            "caption": "산점도",
            "caption_skip_autofill": True,
            "mode": "scatter",
            "x_axis_title": "x", "y_axis_title": "y",
            "x_min": 0, "x_max": 10, "y_min": 0, "y_max": 10,
            "annotations": [{"id": "a1", "type": "callout"}],
        },
        {},
        ["caption", "caption_skip_autofill", "mode",
         "x_axis_title", "y_axis_title",
         "x_min", "x_max", "y_min", "y_max", "annotations"],
    ),
    # ---- B11 scatter3d: PRESERVE user series with kind=surface +
    #                     color_key; passthrough caption/colorscale/axes
    (
        "scatter3d",
        {
            "rows": [{"x": 1, "y": 2, "z": 3}, {"x": 4, "y": 5, "z": 6}],
            "series": [
                {
                    "label": "surface_a",
                    "kind": "surface",
                    "x_key": "x", "y_key": "y", "z_key": "z",
                    "color_key": "z",
                    "color": "#FF0000",
                },
            ],
            "caption": "3D 분포",
            "caption_skip_autofill": True,
            "colorscale": "Plasma",
            "x_axis_title": "x", "y_axis_title": "y", "z_axis_title": "z",
        },
        {},
        ["caption", "caption_skip_autofill", "colorscale",
         "x_axis_title", "y_axis_title", "z_axis_title"],
    ),
    # ---- B12 cad_3d: file_id + view_state + annotations + caption*
    (
        "cad_3d",
        {
            "file_id": "f_model_step",
            "loaded_filename": "part.stp",
            "view_state": {"position": [1.0, 2.0, 3.0],
                           "target": [0.0, 0.0, 0.0],
                           "zoom": 1.0,
                           "show_grid": True, "show_axes": True,
                           "sidebar_open": False},
            "hidden_parts": ["bolt_1"],
            "wireframe_parts": ["housing"],
            "annotations": [
                {"id": "d1", "type": "distance_3d",
                 "p1": {"x": 0.0, "y": 0.0, "z": 0.0},
                 "p2": {"x": 1.0, "y": 0.0, "z": 0.0},
                 "label": "1m", "color": "#FF8800"},
            ],
            "caption": "조립도",
            "caption_skip_autofill": True,
        },
        {},
        ["caption_skip_autofill", "view_state", "hidden_parts",
         "wireframe_parts", "annotations"],
    ),
    # ---- B13 comparison: caption + caption_skip_autofill + cases +
    #                      horizontal_scroll + max_cases + image_max_height_px
    (
        "comparison",
        {
            "rows": [
                {"key": "cost", "label": "비용", "kind": "text",
                 "values": {"as_is": "월 200", "to_be": "월 80"}},
            ],
            "caption": "AS-IS vs TO-BE",
            "caption_skip_autofill": True,
            "cases": [{"key": "as_is", "label": "AS-IS"},
                      {"key": "to_be", "label": "TO-BE"}],
            "horizontal_scroll": True,
            "max_cases": 4,
            "image_max_height_px": 320,
        },
        {"cases": [{"key": "as_is", "label": "AS-IS"},
                   {"key": "to_be", "label": "TO-BE"}]},
        ["caption", "caption_skip_autofill", "cases",
         "horizontal_scroll", "max_cases", "image_max_height_px"],
    ),
    # ---- B14 table: caption + caption_skip_autofill + columns
    #                 (per-report override)
    (
        "table",
        {
            "rows": [{"issue": "느린 응답", "owner": "김"}],
            "caption": "이슈 표",
            "caption_skip_autofill": False,
            "columns": [
                {"key": "issue", "label": "이슈", "type": "text"},
                {"key": "owner", "label": "담당자", "type": "text"},
            ],
        },
        {
            "columns": [
                {"key": "issue", "label": "이슈", "type": "text"},
                {"key": "owner", "label": "담당자", "type": "text"},
            ],
        },
        ["caption", "caption_skip_autofill", "columns"],
    ),
    # ---- B15 rich_text dict-branch: caption + caption_skip_autofill
    (
        "rich_text",
        {
            "markdown": "본문 단락",
            "caption": "RT 캡션",
            "caption_skip_autofill": True,
        },
        {},
        ["caption", "caption_skip_autofill"],
    ),
    # ---- B16 html_embed: bundle_id + entry_path + display + title +
    #                      description + cover_file_id + caption_skip_autofill
    (
        "html_embed",
        {
            "file_id": "f_embed",
            "bundle_id": "bundle_x",
            "entry_path": "index.html",
            "display": "card",
            "title": "데모",
            "description": "인터랙티브 데모",
            "cover_file_id": "f_cover",
            "caption_skip_autofill": True,
        },
        {},
        ["bundle_id", "entry_path", "display", "title",
         "description", "cover_file_id", "caption_skip_autofill"],
    ),
]


@pytest.mark.parametrize(
    "widget_type, raw, props, expected_keys",
    PASSTHROUGH_CASES,
    ids=[c[0] for c in PASSTHROUGH_CASES],
)
def test_adapter_preserves_passthrough_fields(
    widget_type: str, raw: Any, props: dict, expected_keys: list[str],
) -> None:
    adapter = ADAPTERS[widget_type]
    out = adapter.normalize(raw, props)
    assert isinstance(out, dict), (
        f"{widget_type}: adapter.normalize() returned {type(out).__name__}, "
        "expected dict"
    )
    missing = [k for k in expected_keys if k not in out]
    assert not missing, (
        f"{widget_type}: passthrough fields silently dropped by adapter: "
        f"{missing}. Got keys: {sorted(out.keys())}"
    )


# --------------------------------------------------------------------------- #
# Extra B11 lock — surface series with kind=surface must survive verbatim
# (this is the specific scatter3d regression the v0.5.2 audit calls out).
# --------------------------------------------------------------------------- #
def test_scatter3d_preserves_user_surface_series() -> None:
    adapter = ADAPTERS["scatter3d"]
    raw = {
        "rows": [{"x": 1, "y": 2, "z": 3}, {"x": 4, "y": 5, "z": 6}],
        "series": [
            {
                "label": "surface_a",
                "kind": "surface",
                "x_key": "x", "y_key": "y", "z_key": "z",
                "color_key": "z",
                "color": "#FF0000",
            },
        ],
    }
    out = adapter.normalize(raw, {})
    assert isinstance(out.get("series"), list) and len(out["series"]) == 1
    s = out["series"][0]
    assert s["kind"] == "surface", (
        f"scatter3d: user series kind dropped (got {s.get('kind')!r}; "
        f"expected 'surface'). Adapter must not synthesize over caller's series."
    )
    assert s.get("color_key") == "z", (
        f"scatter3d: user series color_key dropped (got {s.get('color_key')!r})"
    )
    assert s.get("color") == "#FF0000"


# --------------------------------------------------------------------------- #
# Extra B15 lock — rich_text list-branch must preserve per-item `relation`
# --------------------------------------------------------------------------- #
def test_rich_text_list_branch_preserves_relation() -> None:
    adapter = ADAPTERS["rich_text"]
    raw = [
        {"text": "관련 자료 참조", "depth": 0, "relation": "vendor_a"},
        {"text": "별도 인용", "depth": 1, "relation": "report_x"},
    ]
    out = adapter.normalize(raw, {})
    items = out.get("items") or []
    assert len(items) == 2, f"rich_text: expected 2 items, got {len(items)}"
    rels = [i.get("relation") for i in items]
    assert rels == ["vendor_a", "report_x"], (
        f"rich_text: per-item relation slug dropped (got {rels})"
    )
