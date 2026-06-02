"""Tests for the deterministic repair helpers in `report_skill.repair`.

These run no LLM / network and are the cheapest layer of the suite; every
adapter ultimately leans on one of these helpers, so anything broken here
cascades into adapter failures.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from report_skill import repair


# --------------------------------------------------------------------------- #
# to_slug
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text, expected",
    [
        ("  Hello World  ", "hello_world"),
        ("Hello-World", "hello_world"),
        ("123abc", "x_123abc"),         # starts with digit → x_ prefix
        ("한글-test", "test"),           # non-ASCII stripped
        ("ALREADYok", "alreadyok"),
        ("multi   spaces", "multi_spaces"),
        ("kebab-case-thing", "kebab_case_thing"),
        ("__leading", "leading"),
        ("trailing__", "trailing"),
        ("only한글", "only"),            # non-ASCII dropped, ASCII core kept
        ("", ""),
        ("A" * 100, "a" * 64),           # capped at 64
    ],
    ids=[
        "spaces_lowercased",
        "hyphen_to_underscore",
        "digit_first_gets_x_prefix",
        "non_ascii_stripped",
        "already_alpha",
        "multi_spaces_collapse",
        "kebab_to_snake",
        "leading_underscore_trimmed",
        "trailing_underscore_trimmed",
        "all_non_ascii_returns_empty",
        "empty_in_empty_out",
        "capped_at_64_chars",
    ],
)
def test_to_slug(text, expected):
    assert repair.to_slug(text) == expected


# --------------------------------------------------------------------------- #
# nearest_enum
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value, options, expected",
    [
        ("중간", ["낮음", "보통", "높음"], "보통"),     # middle tie-break
        ("높음", ["낮음", "보통", "높음"], "높음"),     # exact
        ("HIGH", ["high", "low"], "high"),              # case-insensitive
        ("hi", ["high", "low"], "high"),                # substring
        ("Critical", ["high", "mid", "low"], None),     # no match
        ("", ["a", "b"], None),                          # empty value
        (None, ["a", "b"], None),                        # none value
    ],
    ids=[
        "korean_middle_tie_break",
        "exact_match",
        "case_insensitive",
        "substring_match",
        "no_match_returns_none",
        "empty_value_returns_none",
        "none_value_returns_none",
    ],
)
def test_nearest_enum(value, options, expected):
    assert repair.nearest_enum(value, options) == expected


# --------------------------------------------------------------------------- #
# force_enum
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value, options, expected",
    [
        ("medium", ["A", "B", "C"], "B"),               # fallback to middle
        ("높음", ["낮음", "보통", "높음"], "높음"),
        ("중간", ["낮음", "보통", "높음"], "보통"),
        ("???", ["x", "y", "z", "w"], "z"),              # len//2 = 2 → 'z'
        ("anything", [], None),                          # no options ⇒ None
    ],
    ids=[
        "no_match_falls_to_middle",
        "exact_kept",
        "near_match_picks_middle_by_tie_break",
        "four_options_middle_index_two",
        "empty_options_returns_none",
    ],
)
def test_force_enum(value, options, expected):
    assert repair.force_enum(value, options) == expected


# --------------------------------------------------------------------------- #
# coerce_number
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value, expected",
    [
        ("1,234.5", 1234.5),
        ("42", 42.0),
        ("  -3.14 ", -3.14),
        ("abc", None),
        ("", None),
        (None, None),
        # bool is deliberately NOT coerced to a number — use coerce_bool for that.
        # Treating True/False as 1/0 would mis-handle yes/no inputs in number columns.
        (True, None),
        (False, None),
        (7, 7.0),
        (2.5, 2.5),
    ],
    ids=[
        "comma_thousands",
        "plain_int_string",
        "negative_float_with_spaces",
        "non_numeric_string",
        "empty_string",
        "none",
        "bool_true_rejected",
        "bool_false_rejected",
        "int_in_int_out_float",
        "float_passthrough",
    ],
)
def test_coerce_number(value, expected):
    assert repair.coerce_number(value) == expected


# --------------------------------------------------------------------------- #
# coerce_integer
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value, expected",
    [
        ("42.7", 43),       # rounds to nearest
        ("-3", -3),
        ("0", 0),
        ("1,000", 1000),
        ("abc", None),
        (2.4, 2),
        (2.6, 3),
    ],
    ids=[
        "rounds_up",
        "negative_int_string",
        "zero",
        "comma_thousands",
        "non_numeric_returns_none",
        "float_rounds_down",
        "float_rounds_up",
    ],
)
def test_coerce_integer(value, expected):
    assert repair.coerce_integer(value) == expected


# --------------------------------------------------------------------------- #
# coerce_iso_date
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026년 5월 31일", "2026-05-31"),
        ("2026/5/1", "2026-05-01"),
        ("2026-05-31", "2026-05-31"),
        ("2026.5.1", "2026-05-01"),
        ("20260501", "2026-05-01"),
        (date(2026, 5, 26), "2026-05-26"),
        (datetime(2026, 5, 26, 13, 45), "2026-05-26"),
        ("not a date", None),
        ("", None),
        (None, None),
        ("2026-13-40", None),    # invalid month/day
    ],
    ids=[
        "korean_hangeul",
        "slash_separator",
        "iso_passthrough",
        "dot_separator",
        "compact_yyyymmdd",
        "date_object",
        "datetime_object",
        "garbage_string",
        "empty_string",
        "none_input",
        "invalid_calendar_date",
    ],
)
def test_coerce_iso_date(value, expected):
    assert repair.coerce_iso_date(value) == expected


# --------------------------------------------------------------------------- #
# coerce_bool
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value, expected",
    [
        ("예", True),
        ("아니오", False),
        ("네", True),
        ("거짓", False),
        ("참", True),
        ("yes", True),
        ("NO", False),
        ("true", True),
        ("0", False),
        ("1", True),
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("maybe", None),
        (None, None),
    ],
    ids=[
        "korean_yes",
        "korean_no",
        "korean_ne",
        "korean_false",
        "korean_true_cham",
        "english_yes",
        "english_no_uppercase",
        "lowercase_true",
        "string_zero_is_false",
        "string_one_is_true",
        "bool_true",
        "bool_false",
        "int_one_true",
        "int_zero_false",
        "ambiguous_returns_none",
        "none_returns_none",
    ],
)
def test_coerce_bool(value, expected):
    assert repair.coerce_bool(value) == expected


# --------------------------------------------------------------------------- #
# truncate
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value, max_len, expected",
    [
        ("hello", 10, "hello"),       # no change
        ("hello world", 5, "hell…"),  # 4 chars + ellipsis
        ("abcdef", 6, "abcdef"),      # exactly fits
        ("abcdef", 3, "ab…"),
    ],
    ids=[
        "shorter_than_max",
        "longer_gets_ellipsis",
        "exact_fit",
        "short_max_with_ellipsis",
    ],
)
def test_truncate(value, max_len, expected):
    assert repair.truncate(value, max_len) == expected


# --------------------------------------------------------------------------- #
# strip_bullet
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "line, expected",
    [
        ("- item one", "item one"),
        ("* asterisk bullet", "asterisk bullet"),
        ("• unicode bullet", "unicode bullet"),
        ("1) thing", "thing"),
        ("2. numbered period", "numbered period"),
        ("plain text", "plain text"),
        ("   - indented", "indented"),
        ("", ""),
        ("  ", ""),
    ],
    ids=[
        "dash_bullet",
        "asterisk_bullet",
        "unicode_bullet",
        "numbered_paren",
        "numbered_period",
        "no_bullet_passthrough",
        "indented_dash",
        "empty",
        "whitespace_only",
    ],
)
def test_strip_bullet(line, expected):
    assert repair.strip_bullet(line) == expected
