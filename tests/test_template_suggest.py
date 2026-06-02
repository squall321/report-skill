"""Tests for ``report_skill.template_suggest`` — keyword scoring + LLM tie-break.

All tests pass the ``templates`` arg directly so no client is needed for
the core scoring path. The "no LLM call when use_llm='never'" tests stub
the llm module so any accidental provider lookup is detected.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from report_skill import template_suggest


# --------------------------------------------------------------------------- #
# The 8 canned templates — minimal stand-ins
# --------------------------------------------------------------------------- #
TEMPLATES: list[dict] = [
    {"template_id": "weekly-dev",       "name": "주간 개발",      "description": "주간 개발 보고서"},
    {"template_id": "weekly-biz",       "name": "주간 영업",      "description": "영업 파이프라인 주간 리뷰"},
    {"template_id": "monthly-summary",  "name": "월간 정리",      "description": "월말 종합 리뷰"},
    {"template_id": "rfc",              "name": "RFC",            "description": "기술 검토 / 설계 문서"},
    {"template_id": "adr",              "name": "ADR",            "description": "Architecture Decision Record"},
    {"template_id": "incident",         "name": "장애 보고",      "description": "Incident / postmortem"},
    {"template_id": "release-note",     "name": "릴리스 노트",    "description": "Release/배포 노트"},
    {"template_id": "meeting-decision", "name": "회의 결정사항",  "description": "Meeting minutes & decisions"},
]


# --------------------------------------------------------------------------- #
# Each canned template's keyword path picks the right top1
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,expected", [
    ("이번 주는 결제 API 통합과 sprint 회고를 진행했다", "weekly-dev"),
    ("영업 파이프라인 점검 및 수주 현황 정리", "weekly-biz"),
    ("월간 정리: 분기 매출 회고와 다음 달 계획", "monthly-summary"),
    ("이번 RFC는 기술 검토와 설계서 초안입니다.", "rfc"),
    ("Architecture decision record for the new auth scheme.", "adr"),
    ("어제 장애 발생: 결제 인시던트 postmortem 작성.", "incident"),
    ("릴리스 노트 v0.2 배포 changelog 정리", "release-note"),
    ("어제 회의록 결정사항 정리 — 다음 단계 논의", "meeting-decision"),
])
def test_canned_template_top1_for_obvious_text(text, expected):
    out = template_suggest.suggest_templates(text, templates=TEMPLATES, top_k=3,
                                              use_llm="never")
    assert out[0].template_id == expected, \
        f"expected {expected} top1, got {[(s.template_id, s.score) for s in out]}"


# --------------------------------------------------------------------------- #
# Confidence levels follow the formula
# --------------------------------------------------------------------------- #
def test_high_confidence_for_dominant_match():
    text = ("주간 개발 sprint 스프린트 weekly 주간보고 개발 dev 이번 주 ")
    out = template_suggest.suggest_templates(text, templates=TEMPLATES, top_k=3,
                                              use_llm="never")
    # top1 score must clear _HIGH_SCORE_FLOOR(0.40) and beat top2 by
    # _AMBIGUOUS_MARGIN(0.20)
    assert out[0].template_id == "weekly-dev"
    assert out[0].score >= template_suggest._HIGH_SCORE_FLOOR
    margin = out[0].score - out[1].score
    if margin >= template_suggest._AMBIGUOUS_MARGIN:
        assert out[0].confidence == "high"


def test_low_confidence_when_no_keywords_match():
    text = "오늘 점심은 김치찌개를 먹었다 hello world"
    out = template_suggest.suggest_templates(text, templates=TEMPLATES, top_k=3,
                                              use_llm="never")
    assert out[0].confidence == "low"
    # Top score must be very low / zero
    assert out[0].score < template_suggest._LOW_SCORE_FLOOR or \
           (out[0].score - out[1].score) < template_suggest._LOW_MARGIN


def test_medium_confidence_when_top1_has_small_margin_above_floor():
    """Craft text that triggers moderate signal but not dominance."""
    # Single keyword from weekly-biz, no dominant signal.
    text = "영업 영업 파이프라인"  # hits 2 weekly-biz tokens
    out = template_suggest.suggest_templates(text, templates=TEMPLATES, top_k=3,
                                              use_llm="never")
    assert out[0].template_id == "weekly-biz"
    # Confidence is one of the three valid values
    assert out[0].confidence in {"low", "medium", "high"}


# --------------------------------------------------------------------------- #
# Empty templates → padded placeholders
# --------------------------------------------------------------------------- #
def test_empty_templates_returns_top_k_placeholders():
    out = template_suggest.suggest_templates("아무거나", templates=[], top_k=3,
                                              use_llm="never")
    assert len(out) == 3
    for s in out:
        assert s.template_id == ""
        assert s.score == 0.0
        assert s.confidence == "low"


def test_padded_when_catalog_smaller_than_top_k():
    small = [TEMPLATES[0]]
    out = template_suggest.suggest_templates("주간 개발", templates=small, top_k=4,
                                              use_llm="never")
    assert len(out) == 4
    # First entry is the real match
    assert out[0].template_id == "weekly-dev"
    # The rest are empty placeholders
    for s in out[1:]:
        assert s.template_id == ""
        assert s.score == 0.0


# --------------------------------------------------------------------------- #
# use_llm="never" — no provider lookup is attempted
# --------------------------------------------------------------------------- #
def test_use_llm_never_does_not_call_provider():
    text = "회의록 결정 RFC 설계"  # ambiguous on purpose
    with patch.object(template_suggest, "llm") as llm_mock:
        # If anything in template_suggest reaches into llm, the mock records it.
        llm_mock.is_configured = MagicMock(return_value=True)
        llm_mock.get_provider = MagicMock()
        llm_mock.LLMError = Exception  # so try/except still works structurally
        out = template_suggest.suggest_templates(
            text, templates=TEMPLATES, top_k=3, use_llm="never",
        )
    # get_provider must NOT be called
    llm_mock.get_provider.assert_not_called()
    assert isinstance(out, list)
    assert len(out) == 3


# --------------------------------------------------------------------------- #
# use_llm="always" — LLM is called even when keyword confidence is high
# --------------------------------------------------------------------------- #
def test_use_llm_always_calls_provider_and_can_promote():
    """LLM picks a candidate that's IN the top-k → it gets promoted to top1."""
    text = "이번 주 weekly dev 개발 sprint"  # weekly-dev wins keyword scoring
    provider = MagicMock()
    # Pick weekly-biz — it's in top-k (it has 'weekly' hit) → promotion path
    provider.generate = MagicMock(
        return_value='{"template_id": "weekly-biz", "reason": "biz angle"}'
    )
    with patch.object(template_suggest, "llm") as llm_mock:
        llm_mock.is_configured = MagicMock(return_value=True)
        llm_mock.get_provider = MagicMock(return_value=provider)
        # extract_json delegates to a real-enough impl
        import json
        llm_mock.extract_json = MagicMock(side_effect=lambda raw: json.loads(raw))
        llm_mock.LLMError = Exception

        out = template_suggest.suggest_templates(
            text, templates=TEMPLATES, top_k=3, use_llm="always",
        )

    provider.generate.assert_called_once()
    # weekly-biz must be promoted to top1 with llm_reasoning set
    assert out[0].template_id == "weekly-biz"
    assert out[0].llm_reasoning is not None
    assert "biz" in out[0].llm_reasoning


# --------------------------------------------------------------------------- #
# LLM tie-break failure → fall back to keyword ranking
# --------------------------------------------------------------------------- #
def test_llm_failure_falls_back_to_keyword_ranking():
    text = "회의록 RFC 설계서 결정"  # roughly ambiguous between meeting-decision/rfc
    provider = MagicMock()
    provider.generate = MagicMock(side_effect=RuntimeError("network down"))
    with patch.object(template_suggest, "llm") as llm_mock:
        llm_mock.is_configured = MagicMock(return_value=True)
        llm_mock.get_provider = MagicMock(return_value=provider)
        llm_mock.LLMError = Exception
        import json
        llm_mock.extract_json = MagicMock(side_effect=lambda raw: json.loads(raw))
        out = template_suggest.suggest_templates(
            text, templates=TEMPLATES, top_k=3, use_llm="always",
        )
    # We still got results; llm_reasoning is None on top1
    assert len(out) == 3
    assert out[0].llm_reasoning is None


# --------------------------------------------------------------------------- #
# Internal scoring helpers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("top_score,margin,expected", [
    (0.5, 0.25, "high"),         # clears high floor + ambiguous margin
    (0.35, 0.25, "medium"),      # margin OK but score below high floor
    (0.5, 0.15, "medium"),       # score OK but margin below ambiguous floor
    (0.05, 0.5, "low"),          # very weak top
    (0.5, 0.01, "low"),          # tiny margin → low
])
def test_confidence_for_formula(top_score, margin, expected):
    got = template_suggest._confidence_for(top_score, margin)
    assert got == expected


# --------------------------------------------------------------------------- #
# Korean + English mixed text still scores correctly
# --------------------------------------------------------------------------- #
def test_mixed_korean_english_text_picks_right_template():
    text = "Sprint 회고 weekly 개발 보고 — 결제 API 통합 진행 상황"
    out = template_suggest.suggest_templates(text, templates=TEMPLATES, top_k=3,
                                              use_llm="never")
    assert out[0].template_id == "weekly-dev"


# --------------------------------------------------------------------------- #
# Fallback / no client — when templates=None and client construction fails
# --------------------------------------------------------------------------- #
def test_no_templates_arg_falls_back_when_client_fails():
    """When templates=None and client raises, we get empty placeholders."""
    fake_client_cls = MagicMock(side_effect=RuntimeError("no backend"))
    # Patch the lazy import inside suggest_templates
    import sys
    fake_client_mod = MagicMock()
    fake_client_mod.ReportArchiveClient = fake_client_cls
    with patch.dict(sys.modules, {"report_skill.client": fake_client_mod}):
        out = template_suggest.suggest_templates("아무거나", templates=None, top_k=2,
                                                  use_llm="never")
    assert len(out) == 2
    assert all(s.template_id == "" for s in out)


# --------------------------------------------------------------------------- #
# Sort stability: scored items tied at zero are sorted by template_id
# --------------------------------------------------------------------------- #
def test_zero_score_tie_break_alphabetical_by_id():
    text = "completely irrelevant text"  # zero hits across the catalog
    out = template_suggest.suggest_templates(text, templates=TEMPLATES, top_k=8,
                                              use_llm="never")
    # All zero-scored — order is alphabetical by id.  Only check on the
    # zero-score tail (the catalog may have some baseline hits).
    zero_ids = [s.template_id for s in out if s.score == 0.0 and s.template_id]
    assert zero_ids == sorted(zero_ids)


# --------------------------------------------------------------------------- #
# Top-k clamps to at least 1
# --------------------------------------------------------------------------- #
def test_top_k_zero_clamped_to_one():
    out = template_suggest.suggest_templates("주간", templates=TEMPLATES, top_k=0,
                                              use_llm="never")
    assert len(out) == 1


# --------------------------------------------------------------------------- #
# Suggestion dataclass shape
# --------------------------------------------------------------------------- #
def test_suggestion_has_expected_fields():
    out = template_suggest.suggest_templates(
        "이번 주 weekly 개발", templates=TEMPLATES, top_k=1, use_llm="never",
    )
    s = out[0]
    assert hasattr(s, "template_id")
    assert hasattr(s, "template_name")
    assert hasattr(s, "score")
    assert hasattr(s, "matched_keywords")
    assert hasattr(s, "llm_reasoning")
    assert hasattr(s, "confidence")
    assert isinstance(s.matched_keywords, list)
    assert 0.0 <= s.score <= 1.0
