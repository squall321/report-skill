"""Regression suite for the output patterns produced by weak local 8B models.

Each fixture file under ``tests/fixtures/weak_llm/`` records a real-world
ugly input shape (TitleCase keys, markdown bullets in a string, off-enum
values, extra columns, nested dicts as values, ...) plus the expected
"should_normalize" behaviour. We feed each through the matching adapter and
assert the result either validates against the cached content_schema or
fails loudly with ``NormalizeError`` — depending on what the fixture declares.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from report_skill import schemas
from report_skill.adapters import ADAPTERS
from report_skill.adapters.base import NormalizeError

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "weak_llm"


def _load_fixtures():
    files = sorted(FIXTURES_DIR.glob("*.json"))
    out = []
    for f in files:
        with f.open(encoding="utf-8") as fh:
            data = json.load(fh)
        out.append((f.stem, data))
    return out


FIXTURES = _load_fixtures()


def test_fixture_directory_populated():
    """Sanity check — if someone moves the fixtures we want a loud failure."""
    assert FIXTURES, f"No weak-LLM fixtures found under {FIXTURES_DIR}"
    # Make sure each fixture has the required shape.
    for name, data in FIXTURES:
        for k in ("widget_type", "props", "raw_input", "should_normalize", "note"):
            assert k in data, f"{name}: missing required field '{k}'"


@pytest.mark.parametrize(
    "name, fixture",
    FIXTURES,
    ids=[name for name, _ in FIXTURES],
)
def test_weak_llm_fixture(name, fixture, snapshot):
    widget_type = fixture["widget_type"]
    raw = fixture["raw_input"]
    props = fixture["props"]
    should_normalize = fixture["should_normalize"]

    adapter = ADAPTERS.get(widget_type)
    assert adapter is not None, f"{name}: unknown widget_type {widget_type!r}"
    schema = schemas.content_schema(snapshot, widget_type)
    assert schema is not None, f"{name}: no content_schema for {widget_type}"

    if not should_normalize:
        with pytest.raises(NormalizeError):
            adapter.normalize(raw, props)
        return

    # Should normalize and validate.
    out = adapter.normalize(raw, props)
    assert isinstance(out, dict), f"{name}: normalize() must return dict"

    errors = sorted(Draft7Validator(schema).iter_errors(out),
                    key=lambda e: list(e.absolute_path))
    if errors:
        msgs = "\n".join(f"  - {list(e.absolute_path)}: {e.message}" for e in errors)
        pytest.fail(
            f"{name} (widget={widget_type}) normalized but failed schema:\n"
            f"{msgs}\n\noutput={out!r}\n\nnote={fixture['note']!r}"
        )


# --------------------------------------------------------------------------- #
# Targeted post-conditions for the more interesting fixtures
# --------------------------------------------------------------------------- #
def _fixture(stem: str) -> dict:
    with (FIXTURES_DIR / f"{stem}.json").open(encoding="utf-8") as fh:
        return json.load(fh)


def test_extra_columns_are_dropped():
    """Stray LLM-invented columns should disappear from the row dicts."""
    fx = _fixture("table_with_extra_columns")
    adapter = ADAPTERS["table"]
    out = adapter.normalize(fx["raw_input"], fx["props"])
    allowed = {"issue", "severity", "owner"}
    for row in out["rows"]:
        assert set(row.keys()) <= allowed, f"stray cols leaked: {set(row.keys()) - allowed}"


def test_uppercase_keys_slugified():
    """TitleCase / SCREAMING_SNAKE keys should be slugified to match the
    `^[a-z][a-z0-9_]*$` pattern enforced by the content schema."""
    fx = _fixture("key_value_with_uppercase_keys")
    out = ADAPTERS["key_value"].normalize(fx["raw_input"], fx["props"])
    assert "team" in out
    assert "sprint_number" in out
    assert "lead_name" in out
    assert "release_date" in out
    # No remaining uppercase or non-slug keys.
    for k in out.keys():
        assert k.islower() and "-" not in k and " " not in k


def test_markdown_bullets_split_into_items():
    fx = _fixture("bulleted_list_with_markdown_bullets")
    out = ADAPTERS["bulleted_list"].normalize(fx["raw_input"], fx["props"])
    items = out["items"]
    assert len(items) == 5
    # No bullet prefix should remain on any line.
    for it in items:
        assert not it.startswith(("-", "*", "•"))
        assert not (len(it) >= 2 and it[0].isdigit() and it[1] in ".)")


def test_off_enum_falls_back_to_middle():
    fx = _fixture("select_with_off_enum_value")
    out = ADAPTERS["table"].normalize(fx["raw_input"], fx["props"])
    # 'critical' has no near match → middle option '보통'.
    assert out["rows"][0]["severity"] == "보통"
    # '중간' has Levenshtein-2 match to '보통' (middle); nearest_enum picks it.
    assert out["rows"][1]["severity"] == "보통"


def test_nested_dict_value_stringified():
    fx = _fixture("key_value_with_nested_dict_values")
    out = ADAPTERS["key_value"].normalize(fx["raw_input"], fx["props"])
    # Schema accepts only scalar (str/num/bool/null) or arrays thereof.
    # The nested dict for `team` must have been stringified.
    assert isinstance(out["team"], str)
    assert "name" in out["team"]
    # List of strings passes through untouched.
    assert out["members"] == ["박국진", "이영희", "김철수"]


def test_number_column_handles_mixed_types():
    fx = _fixture("table_with_text_in_number_column")
    out = ADAPTERS["table"].normalize(fx["raw_input"], fx["props"])
    revenues = [r["revenue"] for r in out["rows"]]
    # '120' → 120.0, '1,450' → 1450.0, 168 → 168.0
    assert revenues[0] == 120.0
    assert revenues[1] == 1450.0
    assert revenues[2] == 168.0
    # Unparseable falls back to raw string.
    assert revenues[3] == "약 152 정도"
