"""Tests for per-widget merge strategies in ``report_skill.merge``.

These cover every entry in ``merge.STRATEGY`` plus the public
``merge_block_content`` entry point. All tests are deterministic, in-memory,
and zero-IO.
"""
from __future__ import annotations

import pytest

from report_skill import merge


# --------------------------------------------------------------------------- #
# milestone — append + dedupe by (date, label) + sort by date
# --------------------------------------------------------------------------- #
def test_milestone_appends_and_dedupes_by_date_and_label():
    existing = {"items": [
        {"date": "2026-01-01", "label": "kickoff"},
        {"date": "2026-02-01", "label": "alpha"},
    ]}
    new = {"items": [
        {"date": "2026-01-01", "label": "kickoff"},  # exact dup
        {"date": "2026-03-01", "label": "beta"},
    ]}
    out = merge.merge_block_content("milestone", existing, new)
    labels = [(i["date"], i["label"]) for i in out["items"]]
    assert labels == [
        ("2026-01-01", "kickoff"),
        ("2026-02-01", "alpha"),
        ("2026-03-01", "beta"),
    ]


def test_milestone_sorts_out_of_order_new_items_by_date():
    existing = {"items": [{"date": "2026-06-01", "label": "release"}]}
    new = {"items": [
        {"date": "2026-03-01", "label": "kickoff"},
        {"date": "2026-04-15", "label": "review"},
        {"date": "2026-01-10", "label": "plan"},
    ]}
    out = merge.merge_block_content("milestone", existing, new)
    dates = [i["date"] for i in out["items"]]
    assert dates == sorted(dates)
    assert dates == ["2026-01-10", "2026-03-01", "2026-04-15", "2026-06-01"]


def test_milestone_same_date_different_label_kept_separately():
    existing = {"items": [{"date": "2026-01-01", "label": "A"}]}
    new = {"items": [{"date": "2026-01-01", "label": "B"}]}
    out = merge.merge_block_content("milestone", existing, new)
    assert len(out["items"]) == 2


# --------------------------------------------------------------------------- #
# bulleted_list — dedupe by exact string
# --------------------------------------------------------------------------- #
def test_bulleted_list_dedupes_exact_strings():
    existing = {"items": ["a", "b"]}
    new = {"items": ["b", "c", "a", "d"]}
    out = merge.merge_block_content("bulleted_list", existing, new)
    assert out["items"] == ["a", "b", "c", "d"]


def test_bulleted_list_case_sensitive_dedupe():
    existing = {"items": ["Hello"]}
    new = {"items": ["hello", "HELLO"]}
    out = merge.merge_block_content("bulleted_list", existing, new)
    assert out["items"] == ["Hello", "hello", "HELLO"]


# --------------------------------------------------------------------------- #
# flowchart — dedupe by label
# --------------------------------------------------------------------------- #
def test_flowchart_dedupes_by_label():
    existing = {"items": [{"label": "start"}, {"label": "process"}]}
    new = {"items": [{"label": "process"}, {"label": "end"}]}
    out = merge.merge_block_content("flowchart", existing, new)
    assert [i["label"] for i in out["items"]] == ["start", "process", "end"]


# --------------------------------------------------------------------------- #
# table / chart / scatter / scatter3d — pure append, length doubles
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("wtype", ["table", "chart", "scatter", "scatter3d"])
def test_row_widget_pure_append_no_dedup(wtype):
    existing = {"rows": [{"a": 1}, {"a": 2}]}
    new = {"rows": [{"a": 1}, {"a": 2}]}  # identical rows
    out = merge.merge_block_content(wtype, existing, new)
    assert len(out["rows"]) == 4  # doubled, no dedup
    assert out["rows"] == [{"a": 1}, {"a": 2}, {"a": 1}, {"a": 2}]


# --------------------------------------------------------------------------- #
# pie/waffle/treemap/tree/mind_map/packing — dedupe by label, LAST WINS
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("wtype", ["pie", "waffle", "treemap", "tree", "mind_map", "packing"])
def test_label_keyed_widget_last_wins_dedup(wtype):
    existing = {"rows": [{"label": "A", "value": 10}, {"label": "B", "value": 20}]}
    new = {"rows": [{"label": "A", "value": 99}, {"label": "C", "value": 30}]}
    out = merge.merge_block_content(wtype, existing, new)
    by_label = {r["label"]: r["value"] for r in out["rows"]}
    # A's value should be the NEW 99, not the old 10
    assert by_label == {"A": 99, "B": 20, "C": 30}


# --------------------------------------------------------------------------- #
# comparison — dedupe by `key`
# --------------------------------------------------------------------------- #
def test_comparison_dedupes_by_key():
    existing = {"rows": [{"key": "perf", "a": 1}, {"key": "cost", "a": 2}]}
    new = {"rows": [{"key": "perf", "a": 99}, {"key": "ux", "a": 3}]}
    out = merge.merge_block_content("comparison", existing, new)
    by_key = {r["key"]: r["a"] for r in out["rows"]}
    assert by_key == {"perf": 99, "cost": 2, "ux": 3}


# --------------------------------------------------------------------------- #
# quadrant — dedupe by `id`
# --------------------------------------------------------------------------- #
def test_quadrant_dedupes_by_id():
    existing = {"rows": [{"id": "i1", "name": "old"}, {"id": "i2", "name": "x"}]}
    new = {"rows": [{"id": "i1", "name": "new"}, {"id": "i3", "name": "z"}]}
    out = merge.merge_block_content("quadrant", existing, new)
    by_id = {r["id"]: r["name"] for r in out["rows"]}
    assert by_id == {"i1": "new", "i2": "x", "i3": "z"}


# --------------------------------------------------------------------------- #
# rich_text — append as new paragraph
# --------------------------------------------------------------------------- #
def test_rich_text_appends_paragraph_with_double_newline():
    existing = {"markdown": "First paragraph."}
    new = {"markdown": "Second paragraph."}
    out = merge.merge_block_content("rich_text", existing, new)
    assert out["markdown"] == "First paragraph.\n\nSecond paragraph."


def test_rich_text_empty_new_is_noop():
    existing = {"markdown": "hello"}
    new = {"markdown": ""}
    out = merge.merge_block_content("rich_text", existing, new)
    assert out["markdown"] == "hello"


def test_rich_text_empty_existing_takes_new():
    existing = {"markdown": ""}
    new = {"markdown": "fresh"}
    out = merge.merge_block_content("rich_text", existing, new)
    assert out["markdown"] == "fresh"


# --------------------------------------------------------------------------- #
# key_value — both shapes
# --------------------------------------------------------------------------- #
def test_key_value_items_mode_last_wins_by_key():
    existing = {"items": [
        {"key": "team", "value": "backend"},
        {"key": "lead", "value": "alice"},
    ]}
    new = {"items": [
        {"key": "team", "value": "infra"},          # overwrites
        {"key": "sprint", "value": "S-24"},         # new
    ]}
    out = merge.merge_block_content("key_value", existing, new)
    by_key = {it["key"]: it["value"] for it in out["items"]}
    assert by_key == {"team": "infra", "lead": "alice", "sprint": "S-24"}


def test_key_value_flat_dict_mode_new_wins_per_key():
    existing = {"team": "backend", "lead": "alice"}
    new = {"team": "infra", "sprint": "S-24"}
    out = merge.merge_block_content("key_value", existing, new)
    assert out["team"] == "infra"
    assert out["lead"] == "alice"
    assert out["sprint"] == "S-24"


# --------------------------------------------------------------------------- #
# sankey — nodes dedupe by label, links by (source, target)
# --------------------------------------------------------------------------- #
def test_sankey_dedupes_nodes_by_label_and_links_by_pair():
    existing = {
        "nodes": [{"label": "A"}, {"label": "B"}],
        "links": [{"source": "A", "target": "B", "value": 10}],
    }
    new = {
        "nodes": [{"label": "B"}, {"label": "C"}],
        "links": [
            {"source": "A", "target": "B", "value": 99},  # dup pair
            {"source": "B", "target": "C", "value": 5},
        ],
    }
    out = merge.merge_block_content("sankey", existing, new)
    assert [n["label"] for n in out["nodes"]] == ["A", "B", "C"]
    pairs = [(l["source"], l["target"]) for l in out["links"]]
    assert pairs == [("A", "B"), ("B", "C")]


# --------------------------------------------------------------------------- #
# network — nodes by id, edges by (source, target)
# --------------------------------------------------------------------------- #
def test_network_dedupes_nodes_by_id_and_edges_by_pair():
    existing = {
        "nodes": [{"id": "n1"}, {"id": "n2"}],
        "edges": [{"source": "n1", "target": "n2"}],
    }
    new = {
        "nodes": [{"id": "n2"}, {"id": "n3"}],
        "edges": [
            {"source": "n1", "target": "n2"},  # dup
            {"source": "n2", "target": "n3"},
        ],
    }
    out = merge.merge_block_content("network", existing, new)
    assert [n["id"] for n in out["nodes"]] == ["n1", "n2", "n3"]
    pairs = [(e["source"], e["target"]) for e in out["edges"]]
    assert pairs == [("n1", "n2"), ("n2", "n3")]


# --------------------------------------------------------------------------- #
# progress_bar — merge by label, last-wins for value/max
# --------------------------------------------------------------------------- #
def test_progress_bar_label_merge_last_wins_value_and_max():
    existing = {"items": [
        {"label": "step1", "value": 30, "max": 100},
        {"label": "step2", "value": 10, "max": 50},
    ]}
    new = {"items": [
        {"label": "step1", "value": 80, "max": 200},   # update
        {"label": "step3", "value": 5, "max": 25},     # new
    ]}
    out = merge.merge_block_content("progress_bar", existing, new)
    by_label = {it["label"]: it for it in out["items"]}
    assert by_label["step1"]["value"] == 80
    assert by_label["step1"]["max"] == 200
    assert by_label["step2"]["value"] == 10
    assert by_label["step3"]["value"] == 5
    assert len(out["items"]) == 3


# --------------------------------------------------------------------------- #
# image / video / attachment — files append + dedupe by file_id
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("wtype", ["image", "video", "attachment"])
def test_media_widget_dedupes_by_file_id(wtype):
    existing = {"files": [{"file_id": "f1", "name": "a.png"}]}
    new = {"files": [
        {"file_id": "f1", "name": "duplicate"},  # dup id
        {"file_id": "f2", "name": "b.png"},
    ]}
    out = merge.merge_block_content(wtype, existing, new)
    ids = [f["file_id"] for f in out["files"]]
    assert ids == ["f1", "f2"]
    # existing wins (not overwritten by the dup)
    assert out["files"][0]["name"] == "a.png"


def test_media_widget_propagates_caption():
    existing = {"files": [{"file_id": "f1"}]}
    new = {"files": [], "caption": "new caption"}
    out = merge.merge_block_content("image", existing, new)
    assert out["caption"] == "new caption"


# --------------------------------------------------------------------------- #
# Fall-through case — heading, equation, html_embed → pure replace
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("wtype", ["heading", "equation", "html_embed"])
def test_fallthrough_widget_pure_replace(wtype):
    existing = {"text": "old", "extra": "kept-in-existing"}
    new = {"text": "new"}
    out = merge.merge_block_content(wtype, existing, new)
    # Pure replace — existing dropped entirely, new returned
    assert out == {"text": "new"}
    assert "extra" not in out


# --------------------------------------------------------------------------- #
# Edge case — existing=None → returns new untouched
# --------------------------------------------------------------------------- #
def test_existing_none_returns_new_untouched():
    new = {"items": [{"date": "2026-01-01", "label": "x"}]}
    out = merge.merge_block_content("milestone", None, new)
    assert out is new  # returned unchanged (no merge)


def test_existing_non_dict_returns_new_untouched():
    new = {"foo": "bar"}
    out = merge.merge_block_content("table", "not-a-dict", new)
    assert out is new


# --------------------------------------------------------------------------- #
# Edge cases on empty / missing fields
# --------------------------------------------------------------------------- #
def test_milestone_skips_non_dict_items():
    existing = {"items": [{"date": "2026-01-01", "label": "ok"}, "garbage", 42]}
    new = {"items": [{"date": "2026-02-01", "label": "new"}]}
    out = merge.merge_block_content("milestone", existing, new)
    assert len(out["items"]) == 2  # garbage filtered, both real ones kept


def test_bulleted_list_handles_empty_new():
    existing = {"items": ["a", "b"]}
    new = {"items": []}
    out = merge.merge_block_content("bulleted_list", existing, new)
    assert out["items"] == ["a", "b"]


def test_sankey_skips_links_with_missing_source_or_target():
    existing = {"nodes": [{"label": "A"}], "links": []}
    new = {
        "nodes": [{"label": "B"}],
        "links": [{"source": "A", "target": "B", "value": 1}],
    }
    out = merge.merge_block_content("sankey", existing, new)
    assert len(out["links"]) == 1


# --------------------------------------------------------------------------- #
# is_appendable + STRATEGY surface
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("wtype", [
    "milestone", "bulleted_list", "flowchart",
    "table", "chart", "scatter", "scatter3d",
    "pie", "waffle", "treemap", "tree", "mind_map", "packing",
    "quadrant", "comparison",
    "rich_text", "key_value",
    "sankey", "network",
    "progress_bar",
    "image", "video", "attachment",
])
def test_is_appendable_true_for_strategy_entries(wtype):
    assert merge.is_appendable(wtype) is True


@pytest.mark.parametrize("wtype", ["heading", "equation", "html_embed", "unknown_widget"])
def test_is_appendable_false_for_fallthrough_widgets(wtype):
    assert merge.is_appendable(wtype) is False


def test_strategy_table_keys_are_all_unique_widgets():
    # Sanity: STRATEGY is a flat dict, no key collision
    assert len(merge.STRATEGY) == len(set(merge.STRATEGY.keys()))
