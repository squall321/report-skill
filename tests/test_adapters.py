"""End-to-end adapter probes — one parametrize entry per registered widget.

For every widget in `report_skill.adapters.ADAPTERS`:
  * Feed a realistic, minimal "natural" input through ``adapter.normalize()``.
  * Assert the result is a dict.
  * Validate it against the cached content_schema with Draft7Validator.

The cases are intentionally close to what a human / LLM would type — the
adapter is supposed to absorb the messiness. Pre-shaped passthrough inputs
are reserved for the few media adapters whose schema requires a file_id.
"""
from __future__ import annotations

from typing import Any

import pytest
from jsonschema import Draft7Validator

from report_skill import schemas
from report_skill.adapters import ADAPTERS
from report_skill.adapters.base import NormalizeError
from report_skill.adapters.attachment import AttachmentAdapter
from report_skill.adapters.bulleted_list import BulletedListAdapter
from report_skill.adapters.image import ImageAdapter
from report_skill.adapters.key_value import KeyValueAdapter
from report_skill.adapters.table import TableAdapter


# --------------------------------------------------------------------------- #
# Per-adapter natural-input probes
# --------------------------------------------------------------------------- #
#
# Format: (widget_type, raw_input, props)
#   `props` matches what `schemas.resolved_props(block, snapshot)` would
#   normally produce; for adapters that don't care it's `{}`.
#
ADAPTER_CASES: list[tuple[str, Any, dict]] = [
    # text family
    ("heading", "분기 회고", {}),
    ("rich_text", "이번 주에는 결제 API를 통합했다.", {}),
    ("bulleted_list", ["A 항목", "B 항목", "C 항목"], {}),
    (
        "key_value",
        {"team": "백엔드", "sprint": "Sprint-23", "lead": "박국진"},
        {},
    ),
    (
        "table",
        [
            {"issue": "결제 지연", "severity": "높음", "owner": "김철수"},
            {"issue": "캐시 저하", "severity": "중간", "owner": "이영희"},
        ],
        {
            "columns": [
                {"key": "issue", "label": "이슈", "type": "text"},
                {
                    "key": "severity",
                    "label": "심각도",
                    "type": "select",
                    "options": ["낮음", "보통", "높음"],
                },
                {"key": "owner", "label": "담당자", "type": "text"},
            ]
        },
    ),
    (
        "comparison",
        {
            "비용": {"as_is": "월 200만원", "to_be": "월 80만원"},
            "위험도": {"as_is": "높음", "to_be": "낮음"},
        },
        {"cases": [{"key": "as_is", "label": "AS-IS"},
                   {"key": "to_be", "label": "TO-BE"}]},
    ),
    # chart family
    (
        "chart",
        [{"month": "1월", "revenue": 120},
         {"month": "2월", "revenue": 145},
         {"month": "3월", "revenue": 168}],
        {
            "label": "월별 매출",
            "chart_type": "line",
            "x_column_key": "month",
            "columns": [
                {"key": "month", "label": "월", "type": "text"},
                {"key": "revenue", "label": "매출", "type": "number"},
            ],
        },
    ),
    (
        "scatter",
        [{"x": 1.0, "y": 2.5}, {"x": 2.0, "y": 3.7}, {"x": 3.0, "y": 1.9}],
        {},
    ),
    (
        "scatter3d",
        [{"x": 1, "y": 2, "z": 3},
         {"x": 2, "y": 3, "z": 5},
         {"x": 3, "y": 4, "z": 4}],
        {"label": "3D points"},
    ),
    # matrix / distribution
    (
        "heatmap",
        [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]],
        {},
    ),
    (
        "contour",
        [[0.1, 0.2, 0.3], [0.2, 0.4, 0.6], [0.3, 0.6, 0.9]],
        {},
    ),
    (
        "radar",
        [
            {"name": "자체 구축", "values": {"비용": 9, "리스크": 3, "확장성": 7}},
            {"name": "Stripe", "values": {"비용": 4, "리스크": 8, "확장성": 9}},
        ],
        {},
    ),
    (
        "density",
        {"실험군A": [1.0, 1.5, 2.2, 2.8, 3.1], "실험군B": [2.0, 2.5, 3.0, 3.5]},
        {},
    ),
    (
        "box",
        {"A": [1, 2, 3, 4, 5], "B": [2, 3, 4, 5, 6, 7]},
        {},
    ),
    # proportion
    ("pie", {"높음": 3, "중간": 7, "낮음": 12}, {}),
    ("waffle", {"완료": 60, "진행중": 30, "대기": 10}, {}),
    (
        "progress_bar",
        [
            {"label": "기능 A", "value": 80, "max": 100},
            {"label": "기능 B", "value": 30, "max": 100},
        ],
        {},
    ),
    (
        "treemap",
        {"Backend": {"API": 12, "DB": 5}, "Frontend": {"UI": 7}},
        {},
    ),
    (
        "packing",
        [{"label": "A", "value": 30}, {"label": "B", "value": 50},
         {"label": "C", "value": 20}],
        {},
    ),
    # graph / hierarchy
    (
        "tree",
        {"Payment": {"Gateway": {"Stripe": {}, "Internal": {}},
                     "Outbox": {"Publisher": {}, "Consumer": {}}}},
        {},
    ),
    (
        "network",
        [
            {"source": "A", "target": "B"},
            {"source": "B", "target": "C"},
            {"source": "A", "target": "C"},
        ],
        {},
    ),
    (
        "mind_map",
        {"중심 주제": {"가지 1": ["잎 1", "잎 2"], "가지 2": ["잎 3"]}},
        {},
    ),
    (
        "sankey",
        [
            {"source": "고객", "target": "게이트웨이", "value": 1000},
            {"source": "게이트웨이", "target": "정산", "value": 970},
            {"source": "정산", "target": "운영비", "value": 400},
        ],
        {},
    ),
    # layout / diagram
    (
        "milestone",
        [
            {"date": "2026-05-29", "label": "캐시 최적화", "status": "pending"},
            {"date": "2026-05-31", "label": "결제 API", "status": "pending"},
        ],
        {},
    ),
    (
        "flowchart",
        ["수신", "검증", "처리", "응답"],
        {},
    ),
    (
        "raci_matrix",
        {
            "API 설계": {"PM": "C", "Dev": "R", "QA": "I", "Lead": "A"},
            "단위 테스트": {"PM": "I", "Dev": "R", "QA": "C", "Lead": "A"},
        },
        {},
    ),
    (
        "quadrant",
        [
            {"label": "결제 모듈", "x": 0.85, "y": 0.70},
            {"label": "캐시 최적화", "x": 0.45, "y": 0.30},
        ],
        {},
    ),
    (
        "equation",
        "E = mc^2",
        {},
    ),
    # html_embed: schema requires file_id OR caption-only mode
    (
        "html_embed",
        {"file_id": "fileabc", "filename": "report.html"},
        {},
    ),
    # media — pre-shaped with file_id; URLs/paths are rejected on purpose
    (
        "image",
        {"files": [{"file_id": "img-1"}], "caption": "Diagram"},
        {},
    ),
    (
        "video",
        {"files": [{"file_id": "vid-1"}], "caption": "Demo"},
        {},
    ),
    (
        "attachment",
        {"files": [{"file_id": "att-1", "filename": "spec.pdf"}]},
        {},
    ),
    (
        "cad_3d",
        {"file_id": "cad-1", "caption": "기구 어셈블리"},
        {},
    ),
]


def _ids(cases):
    return [c[0] for c in cases]


def test_adapter_coverage_matches_registry():
    """The parametrize table must cover every wired adapter — otherwise a
    newly added widget would slip in without an end-to-end probe."""
    covered = {c[0] for c in ADAPTER_CASES}
    wired = set(ADAPTERS.keys())
    missing = wired - covered
    extra = covered - wired
    assert not missing, f"adapter cases missing for: {sorted(missing)}"
    assert not extra, f"adapter cases reference unknown widgets: {sorted(extra)}"


@pytest.mark.parametrize("widget_type, raw, props", ADAPTER_CASES, ids=_ids(ADAPTER_CASES))
def test_adapter_natural_input_validates(widget_type, raw, props, snapshot):
    adapter = ADAPTERS[widget_type]
    out = adapter.normalize(raw, props)
    assert isinstance(out, dict), f"{widget_type}: normalize() must return dict"

    schema = schemas.content_schema(snapshot, widget_type)
    assert schema is not None, f"{widget_type}: no content_schema in snapshot"
    errors = sorted(Draft7Validator(schema).iter_errors(out),
                    key=lambda e: list(e.absolute_path))
    if errors:
        msgs = "\n".join(f"  - {list(e.absolute_path)}: {e.message}" for e in errors)
        pytest.fail(f"{widget_type} normalized output failed schema:\n{msgs}\n\noutput={out!r}")


# --------------------------------------------------------------------------- #
# Targeted error paths
# --------------------------------------------------------------------------- #
class TestAdapterErrorPaths:
    """Adapters must refuse clearly-bad input loudly (with NormalizeError),
    rather than silently produce invalid content."""

    def test_bulleted_list_rejects_none(self):
        with pytest.raises(NormalizeError):
            BulletedListAdapter().normalize(None, {})

    def test_bulleted_list_rejects_int(self):
        with pytest.raises(NormalizeError):
            BulletedListAdapter().normalize(42, {})

    def test_table_rejects_dict_without_rows(self):
        with pytest.raises(NormalizeError):
            TableAdapter().normalize(
                {"random": "shape"},
                props={"columns": [{"key": "name", "label": "이름", "type": "text"}]},
            )

    def test_table_silently_filters_non_dict_entries(self):
        # List of mixed entries: only the valid dict survives. The adapter
        # should not raise — it silently drops the noise.
        result = TableAdapter().normalize(
            [{"name": "Alice"}, "garbage", 123, None, {"name": "Bob"}],
            props={"columns": [{"key": "name", "label": "이름", "type": "text"}]},
        )
        assert "rows" in result
        # Only the two valid dicts should make it through.
        assert len(result["rows"]) == 2
        assert result["rows"][0]["name"] == "Alice"
        assert result["rows"][1]["name"] == "Bob"

    def test_key_value_accepts_flat_dict(self):
        out = KeyValueAdapter().normalize({"team": "be", "size": 3}, {})
        assert out["team"] == "be"
        assert out["size"] == 3

    def test_key_value_accepts_items_shape(self):
        out = KeyValueAdapter().normalize(
            {"items": [{"key": "team", "label": "팀", "type": "text"}]},
            {},
        )
        assert isinstance(out["items"], list)
        assert out["items"][0]["key"] == "team"

    def test_image_rejects_url_without_file_id(self):
        # The adapter explicitly forbids URLs to nudge callers toward uploading
        # via POST /api/files first.
        with pytest.raises(NormalizeError):
            ImageAdapter().normalize({"url": "https://example.com/a.png"}, {})

    def test_image_file_dict_without_file_id_raises(self):
        with pytest.raises(NormalizeError):
            ImageAdapter().normalize(
                {"files": [{"caption": "no id here"}]},
                {},
            )

    def test_attachment_file_missing_filename_raises(self):
        # Schema requires filename on each file entry.
        with pytest.raises(NormalizeError):
            AttachmentAdapter().normalize(
                {"files": [{"file_id": "att-x"}]},
                {},
            )
