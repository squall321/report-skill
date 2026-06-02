"""Block-by-block pipeline tests for ``orchestrator.normalize_report``.

Uses the in-memory ``weekly_dev_template`` fixture from conftest — no
network, no backend, no LLM.
"""
from __future__ import annotations

import copy

import pytest

from report_skill import orchestrator


# Reusable clean draft body (matches the fixture's block ids).
def _clean_blocks() -> dict:
    return {
        "meta": {"team": "백엔드", "sprint": "Sprint-23", "lead": "박국진"},
        "summary": "이번 주는 API 통합과 PostgreSQL 마이그레이션을 진행했다.",
        "progress": [
            "REST API 5개 추가",
            "PostgreSQL v2 마이그레이션 완료",
            "단위 테스트 커버리지 85% 도달",
        ],
        "issues": [
            {"issue": "결제 API 연동 지연", "severity": "높음",
             "owner": "김철수", "due": "2026-05-31"},
            {"issue": "캐시 성능 저하", "severity": "보통",
             "owner": "이영희", "due": "2026-05-29"},
        ],
        "next_week": ["결제 API 통합", "캐시 최적화"],
    }


def test_clean_input_all_ok(snapshot, weekly_dev_template):
    blocks = _clean_blocks()
    result = orchestrator.normalize_report(weekly_dev_template, blocks, snapshot)

    # Every block should be ok or repaired (no failures, no skipped).
    assert result.failed_count == 0, [b.short() for b in result.blocks if b.status == "failed"]
    assert result.skipped_count == 0
    assert result.ok_count == len(weekly_dev_template["schema"]["blocks"])

    # Content keys match the block ids defined in the template.
    expected_ids = {b["id"] for b in weekly_dev_template["schema"]["blocks"]}
    assert set(result.content.keys()) == expected_ids


def test_missing_block_skipped(snapshot, weekly_dev_template):
    blocks = _clean_blocks()
    blocks.pop("next_week")    # drop one expected block

    result = orchestrator.normalize_report(weekly_dev_template, blocks, snapshot)

    statuses = {b.block_id: b.status for b in result.blocks}
    assert statuses["next_week"] == "skipped"
    assert "next_week" not in result.content
    # Other blocks should still succeed.
    assert statuses["meta"] in ("ok", "repaired")
    assert statuses["summary"] in ("ok", "repaired")


def test_severity_repair(snapshot, weekly_dev_template):
    """A row with severity='중간' must normalize to '보통' (force_enum tie-break
    to the middle option in ['낮음','보통','높음'])."""
    blocks = _clean_blocks()
    blocks["issues"] = [
        {"issue": "성능 저하", "severity": "중간",  # off-enum
         "owner": "이영희", "due": "2026-05-29"},
    ]
    result = orchestrator.normalize_report(weekly_dev_template, blocks, snapshot)

    issues_report = next(b for b in result.blocks if b.block_id == "issues")
    assert issues_report.status in ("ok", "repaired"), issues_report.short()

    issues_content = result.content["issues"]
    assert issues_content["rows"][0]["severity"] == "보통"


def test_unsupported_widget(snapshot):
    template = {
        "template_id": "synthetic",
        "schema": {
            "blocks": [
                {"id": "thing", "type": "nonexistent_widget", "props": {}},
            ]
        },
    }
    result = orchestrator.normalize_report(template, {"thing": "anything"}, snapshot)

    statuses = {b.block_id: b.status for b in result.blocks}
    assert statuses["thing"] == "unsupported"
    assert "thing" not in result.content


def test_extra_blocks_pipeline(snapshot, weekly_dev_template):
    """Extras flow through the same normalize pipeline and surface on
    NormalizeResult.extra_blocks; their content key is keyed by id."""
    blocks = _clean_blocks()
    extras_in = [{
        "id": "revenue_trend",
        "type": "chart",
        "props": {
            "label": "월별 매출",
            "chart_type": "line",
            "x_column_key": "month",
            "columns": [
                {"key": "month", "label": "월", "type": "text"},
                {"key": "revenue", "label": "매출", "type": "number"},
            ],
        },
        "input": [
            {"month": "1월", "revenue": 120},
            {"month": "2월", "revenue": 145},
            {"month": "3월", "revenue": 168},
        ],
    }]

    result = orchestrator.normalize_report(
        weekly_dev_template, blocks, snapshot, extra_blocks_input=extras_in,
    )

    # Extra block must appear in extras list AND be present in content keyed by id.
    extra_ids = {e["id"] for e in result.extra_blocks}
    assert "revenue_trend" in extra_ids
    assert "revenue_trend" in result.content

    # The extra block report should be marked ok/repaired with "(extra)" detail.
    extra_report = next(b for b in result.blocks if b.block_id == "revenue_trend")
    assert extra_report.status in ("ok", "repaired"), extra_report.short()
    assert "(extra)" in (extra_report.detail or "")


def test_extra_block_missing_id_or_type_fails(snapshot, weekly_dev_template):
    """Malformed extras (no id/type/input) are reported as failed, not crash."""
    extras_in = [
        {"type": "rich_text", "input": "missing id"},
        {"id": "no_type", "input": "missing type"},
        {"id": "no_input", "type": "rich_text"},
    ]
    result = orchestrator.normalize_report(
        weekly_dev_template, _clean_blocks(), snapshot, extra_blocks_input=extras_in,
    )
    statuses = [b.status for b in result.blocks if b.block_id in {"?", "no_type", "no_input"}]
    assert all(s == "failed" for s in statuses)


def test_clean_input_does_not_mutate_inputs(snapshot, weekly_dev_template):
    """Sanity: pipeline must not mutate the caller's blocks or template."""
    blocks = _clean_blocks()
    blocks_snapshot = copy.deepcopy(blocks)
    template_snapshot = copy.deepcopy(weekly_dev_template)

    orchestrator.normalize_report(weekly_dev_template, blocks, snapshot)

    assert blocks == blocks_snapshot, "draft_blocks was mutated"
    assert weekly_dev_template == template_snapshot, "template was mutated"
