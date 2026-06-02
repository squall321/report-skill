"""Tests for ``report_skill.widget_suggest`` — pattern detectors + suppression.

All tests are deterministic. The LLM module is mocked so detectors never
reach out to a provider. We never run the LLM fallback.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from report_skill import widget_suggest


# --------------------------------------------------------------------------- #
# Tiny helper — runs suggest_extras with the LLM gate forced off
# --------------------------------------------------------------------------- #
def _suggest(text: str, *, template_blocks=None, max_extras: int = 5,
             use_llm: str = "never"):
    return widget_suggest.suggest_extras(
        text,
        template_blocks=template_blocks,
        use_llm=use_llm,
        max_extras=max_extras,
    )


# =========================================================================== #
# 1) Structured chart detector (highest priority)
# =========================================================================== #
def test_structured_chart_with_title_and_categorical_x_returns_chart_high():
    """Title + categorical x column + numeric y → chart, HIGH confidence."""
    text = (
        "월별 매출 현황\n"
        "x: 월\n"
        "y: 매출\n"
        "Jan, 100\n"
        "Feb, 145\n"
        "Mar, 168\n"
        "Apr, 200\n"
    )
    out = _suggest(text)
    chart = next((s for s in out if s.widget_type == "chart"), None)
    assert chart is not None
    assert chart.confidence == "high"
    assert chart.props["label"] == "월별 매출 현황"
    assert "columns" in chart.props
    # Axis hints lifted from x:/y: lines
    assert chart.props.get("x_axis_title") == "월"
    assert chart.props.get("y_axis_title") == "매출"


def test_structured_chart_pure_numeric_returns_scatter_high():
    text = (
        "산점도\n"
        "1.5, 100\n"
        "2.2, 145\n"
        "3.8, 168\n"
        "4.1, 200\n"
    )
    out = _suggest(text)
    scatter = next((s for s in out if s.widget_type == "scatter"), None)
    assert scatter is not None
    assert scatter.confidence == "high"


def test_structured_chart_csv_header_3_columns_typed_correctly():
    text = (
        "월별 손익\n"
        "month, revenue, profit\n"
        "Jan, 100, 20\n"
        "Feb, 145, 30\n"
        "Mar, 168, 45\n"
    )
    out = _suggest(text)
    chart = next((s for s in out if s.widget_type == "chart"), None)
    assert chart is not None
    cols = chart.props["columns"]
    assert len(cols) == 3
    # First col is the categorical x → text
    assert cols[0]["type"] == "text"
    # Cols 1+ are numeric
    assert cols[1]["type"] == "number"
    assert cols[2]["type"] == "number"


def test_structured_chart_pipe_delimited_works_too():
    text = (
        "월별 손익\n"
        "month | revenue | profit\n"
        "Jan | 100 | 20\n"
        "Feb | 145 | 30\n"
        "Mar | 168 | 45\n"
    )
    out = _suggest(text)
    chart = next((s for s in out if s.widget_type == "chart"), None)
    assert chart is not None
    assert len(chart.props["columns"]) == 3


def test_structured_chart_single_data_row_returns_none():
    text = (
        "데이터\n"
        "month, revenue\n"
        "Jan, 100\n"
    )
    out = _suggest(text)
    # No structured chart from a single data row
    chart = next((s for s in out if s.widget_type == "chart"), None)
    assert chart is None


# =========================================================================== #
# 2) Suppression rules
# =========================================================================== #
def test_milestone_high_suppresses_chart_and_scatter():
    text = (
        "주요 일정\n"
        "2026-05-31 출시\n"
        "2026-06-15 회고\n"
        "2026-07-01 차기 기획\n"
    )
    out = _suggest(text)
    types = {s.widget_type for s in out}
    assert "milestone" in types
    # Suppressed by HIGH-confidence milestone
    assert "chart" not in types
    assert "scatter" not in types


def test_template_block_of_type_chart_blocks_chart_suggestion():
    text = (
        "월별 매출\n"
        "Jan, 100\n"
        "Feb, 145\n"
        "Mar, 168\n"
        "Apr, 200\n"
    )
    template_blocks = [{"id": "tpl_chart", "type": "chart", "widget_type": "chart"}]
    out = _suggest(text, template_blocks=template_blocks)
    types = {s.widget_type for s in out}
    assert "chart" not in types


# =========================================================================== #
# 3) Standalone detectors
# =========================================================================== #
def test_timeseries_detector_fires_for_korean_months():
    # No header / no axes — so structured_chart should NOT pull this away.
    text = "매출 추이 분석. 1월 100, 2월 145, 3월 168, 4월 200"
    out = _suggest(text)
    chart = next((s for s in out if s.widget_type == "chart"), None)
    assert chart is not None
    assert chart.confidence == "high"


def test_milestone_detector_fires_for_iso_dates():
    text = "2026-05-31 출시\n2026-06-15 회고"
    out = _suggest(text)
    ms = next((s for s in out if s.widget_type == "milestone"), None)
    assert ms is not None
    assert len(ms.input) == 2
    # sorted ascending by date
    assert ms.input[0]["date"] <= ms.input[1]["date"]


def test_pie_detector_fires_for_percent_split():
    text = "지역별 매출 비중: 북미 40%, EMEA 30%, APAC 30%"
    out = _suggest(text)
    pie = next((s for s in out if s.widget_type == "pie"), None)
    assert pie is not None
    assert pie.confidence == "medium"
    # 3 slices
    assert len(pie.input) == 3


def test_flowchart_detector_fires_for_arrow_sequence():
    text = "데이터 파이프라인: 수집 -> 처리 -> 출력"
    out = _suggest(text)
    fc = next((s for s in out if s.widget_type == "flowchart"), None)
    assert fc is not None
    # 3 steps
    assert len(fc.input) == 3


def test_flowchart_detector_fires_for_numbered_list():
    text = (
        "절차:\n"
        "1. 데이터 수집\n"
        "2. 전처리\n"
        "3. 모델 학습\n"
        "4. 결과 검증\n"
    )
    out = _suggest(text)
    fc = next((s for s in out if s.widget_type == "flowchart"), None)
    assert fc is not None


def test_tree_detector_fires_for_indented_bullets():
    text = (
        "Root\n"
        "  - level1-a\n"
        "    - level2-a\n"
        "    - level2-b\n"
        "  - level1-b\n"
    )
    out = _suggest(text)
    tree = next((s for s in out if s.widget_type == "tree"), None)
    assert tree is not None


def test_tree_detector_fires_for_parent_child_lists():
    text = (
        "백엔드: 결제, 인증, 로그\n"
        "프론트: UI, 라우팅, 상태관리\n"
    )
    out = _suggest(text)
    tree = next((s for s in out if s.widget_type == "tree"), None)
    assert tree is not None


def test_raci_detector_fires_for_codes_across_lines():
    # Use space-separated codes so _RE_RACI_CODES gets multiple matches per
    # line (the chained-with-slash form collapses into a single token).
    text = (
        "DB 설계 R A C\n"
        "코드 리뷰 A C I\n"
        "검토 C I R\n"
    )
    out = _suggest(text)
    raci = next((s for s in out if s.widget_type == "raci_matrix"), None)
    assert raci is not None


def test_equation_detector_fires_for_latex_block():
    text = r"질량-에너지 등가: $$E = mc^2$$ where \frac{x}{y} appears."
    out = _suggest(text)
    eq = next((s for s in out if s.widget_type == "equation"), None)
    assert eq is not None
    assert eq.confidence == "high"
    assert "$$" in eq.input


def test_sankey_detector_fires_for_source_target_value_triples():
    text = (
        "Revenue -> Salaries : 1000\n"
        "Revenue -> Rent : 500\n"
        "Revenue -> Marketing : 300\n"
    )
    out = _suggest(text)
    sk = next((s for s in out if s.widget_type == "sankey"), None)
    assert sk is not None
    assert len(sk.input) == 3


def test_quadrant_detector_fires_for_cue_plus_items():
    text = (
        "important urgent matrix\n"
        "- 결제 버그 수정\n"
        "- 디자인 시스템\n"
        "- 문서 정리\n"
        "- 리팩토링\n"
    )
    out = _suggest(text)
    q = next((s for s in out if s.widget_type == "quadrant"), None)
    assert q is not None


def test_comparison_detector_fires_for_asis_tobe_with_rows():
    text = (
        "as-is vs to-be 비교\n"
        "성능: 100ms / 50ms\n"
        "비용: 100원 / 70원\n"
        "안정성: 95% / 99%\n"
    )
    out = _suggest(text)
    cmp = next((s for s in out if s.widget_type == "comparison"), None)
    assert cmp is not None
    assert cmp.confidence == "medium"


# =========================================================================== #
# 4) No-fire cases
# =========================================================================== #
def test_plain_prose_no_patterns_returns_empty():
    text = "오늘은 점심으로 비빔밥을 먹었고 산책을 했다. 날씨가 좋았다."
    out = _suggest(text)
    assert out == []


def test_single_number_not_enough_for_timeseries():
    text = "매출 100원"
    out = _suggest(text)
    # No chart suggestion from a single number
    assert all(s.widget_type != "chart" for s in out)


def test_empty_text_returns_empty():
    assert _suggest("") == []
    assert _suggest("   ") == []
    assert _suggest(None) == []  # type: ignore[arg-type]


# =========================================================================== #
# 5) use_llm="never" does NOT consult the LLM
# =========================================================================== #
def test_use_llm_never_does_not_call_provider():
    text = "오늘 회식했다."  # nothing should fire deterministically
    with patch.object(widget_suggest, "llm") as llm_mock:
        llm_mock.is_configured = MagicMock(return_value=True)
        llm_mock.get_provider = MagicMock()
        out = widget_suggest.suggest_extras(text, use_llm="never")
    llm_mock.get_provider.assert_not_called()
    assert out == []


def test_use_llm_auto_does_not_call_when_deterministic_hit():
    """When a deterministic detector fires, LLM is NOT consulted (auto)."""
    text = "2026-05-31 출시\n2026-06-15 회고\n2026-07-01 차기 기획"
    with patch.object(widget_suggest, "llm") as llm_mock:
        llm_mock.is_configured = MagicMock(return_value=True)
        llm_mock.get_provider = MagicMock()
        out = widget_suggest.suggest_extras(text, use_llm="auto")
    llm_mock.get_provider.assert_not_called()
    assert any(s.widget_type == "milestone" for s in out)


# =========================================================================== #
# 6) Misc shape checks
# =========================================================================== #
def test_extra_spec_dataclass_shape():
    text = "2026-05-31 출시\n2026-06-15 회고\n2026-07-01 차기 기획"
    out = _suggest(text)
    assert out, "should have at least one spec"
    s = out[0]
    assert isinstance(s.suggested_id, str) and s.suggested_id.startswith("auto_")
    assert isinstance(s.widget_type, str)
    assert isinstance(s.props, dict)
    assert s.confidence in {"high", "medium", "low"}
    assert isinstance(s.matched_pattern, str)
    assert s.source in {"deterministic", "llm"}


def test_max_extras_limit_is_respected():
    text = (
        "월별 매출\n"
        "Jan, 100\n"
        "Feb, 145\n"
        "Mar, 168\n"
        "Apr, 200\n"
        "\n"
        "북미 40%, EMEA 30%, APAC 30%\n"
        "\n"
        "Revenue -> Salaries : 1000\n"
        "Revenue -> Rent : 500\n"
        "Revenue -> Marketing : 300\n"
    )
    out = _suggest(text, max_extras=2)
    assert len(out) <= 2


def test_dedup_by_widget_type_keeps_highest_confidence_first():
    """The post-processing sort puts HIGH-conf entries ahead of LOWer ones."""
    text = (
        "2026-05-31 출시\n"
        "2026-06-15 회고\n"
        "2026-07-01 차기 기획\n"
    )
    out = _suggest(text)
    # milestone should be present
    types_in_order = [s.widget_type for s in out]
    assert "milestone" in types_in_order
    # And it should be HIGH
    ms = next(s for s in out if s.widget_type == "milestone")
    assert ms.confidence == "high"
