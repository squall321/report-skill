"""Project-level pytest fixtures.

Provides:
  * ``snapshot`` (module-scoped) — the cached widget snapshot dict produced by
    `report-skill catalog sync`. All schema validation in the suite uses this.
  * ``validator_for(widget_type)`` — small helper that returns a
    ``Draft7Validator`` bound to the widget's ``content_schema``.
  * ``weekly_dev_template`` — a fake template dict mirroring what
    `/api/templates/weekly-dev` returns. Hand-rolled so the tests don't need
    network or the backend running.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

import pytest
from jsonschema import Draft7Validator

# Ensure the in-tree src/ is importable when running pytest from the repo root
# without a `pip install -e .`. Tests run against the same package the runtime
# uses; we just nudge sys.path so `import report_skill` resolves locally.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from report_skill import schemas  # noqa: E402  (after sys.path tweak)


# --------------------------------------------------------------------------- #
# Snapshot + validator helper
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def snapshot() -> dict:
    """Cached widget snapshot from `.skill-cache/widgets.json`."""
    return schemas.load()


@pytest.fixture
def validator_for(snapshot) -> Callable[[str], Draft7Validator]:
    """Return a Draft7Validator bound to the content_schema of `widget_type`."""

    def _make(widget_type: str) -> Draft7Validator:
        schema = schemas.content_schema(snapshot, widget_type)
        assert schema is not None, f"no content_schema for {widget_type!r}"
        return Draft7Validator(schema)

    return _make


# --------------------------------------------------------------------------- #
# Fake `weekly-dev` template fixture (mirrors /api/templates/weekly-dev shape)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def weekly_dev_template() -> dict:
    """Minimal weekly-dev-style template; the orchestrator only cares about
    `schema.blocks`, but the rest of the shell is included so the fixture is
    realistic enough to pass to a builder later."""
    return {
        "template_id": "weekly-dev",
        "name": "주간 개발 보고",
        "version": 1,
        "schema": {
            "blocks": [
                {
                    "id": "meta",
                    "type": "key_value",
                    "props": {
                        "label": "메타",
                        "items": [
                            {"key": "team", "label": "팀", "type": "text"},
                            {"key": "sprint", "label": "스프린트", "type": "text"},
                            {"key": "lead", "label": "리드", "type": "text"},
                        ],
                    },
                },
                {
                    "id": "summary",
                    "type": "rich_text",
                    "props": {"label": "요약"},
                },
                {
                    "id": "progress",
                    "type": "bulleted_list",
                    "props": {"label": "이번 주 한 일"},
                },
                {
                    "id": "issues",
                    "type": "table",
                    "props": {
                        "label": "이슈",
                        "columns": [
                            {"key": "issue", "label": "이슈", "type": "text"},
                            {
                                "key": "severity",
                                "label": "심각도",
                                "type": "select",
                                "options": ["낮음", "보통", "높음"],
                            },
                            {"key": "owner", "label": "담당자", "type": "text"},
                            {"key": "due", "label": "기한", "type": "date"},
                        ],
                    },
                },
                {
                    "id": "next_week",
                    "type": "bulleted_list",
                    "props": {"label": "다음 주 계획"},
                },
            ]
        },
    }
