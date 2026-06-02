"""Per-widget content merge strategies — for incremental append updates.

`report update` REPLACES a block's content. For widgets where the natural
operation is "add another entry" (milestone events, table rows, bullet
items, chart points), users want APPEND semantics instead. This module
maps widget types to merge functions that take (existing, new) → merged.

Widgets without an obvious append meaning (heading, equation, html_embed,
single-file media) fall back to replace.

Used by:
  - `report_ops.append_to_blocks()` — the programmatic API
  - `report append` / `report milestone add` CLI commands
"""
from __future__ import annotations

from typing import Any, Callable, Optional

MergeFn = Callable[[dict, dict], dict]


# --------------------------------------------------------------------------- #
# Generic strategies
# --------------------------------------------------------------------------- #
def _replace(existing: dict, new: dict) -> dict:
    """Last-write-wins. Used for single-value widgets (heading, equation)."""
    return new


def _merge_milestone(existing: dict, new: dict) -> dict:
    """Append events; dedupe by (date, label); sort by date."""
    items: list[dict] = []
    seen: set[tuple] = set()
    for src in (existing.get("items") or [], new.get("items") or []):
        for it in src:
            if not isinstance(it, dict):
                continue
            key = (it.get("date"), it.get("label"))
            if key in seen:
                continue
            seen.add(key)
            items.append(it)
    items.sort(key=lambda i: i.get("date") or "")
    out = dict(existing)
    out["items"] = items
    return out


def _merge_bulleted_list(existing: dict, new: dict) -> dict:
    """Append items; dedupe by exact string match (case-sensitive)."""
    items = list(existing.get("items") or [])
    seen = set(items)
    for it in (new.get("items") or []):
        if it not in seen:
            items.append(it)
            seen.add(it)
    out = dict(existing)
    out["items"] = items
    return out


def _merge_rich_text(existing: dict, new: dict) -> dict:
    """Append new markdown as a separate paragraph below existing."""
    out = dict(existing)
    old = (existing.get("markdown") or "").rstrip()
    add = (new.get("markdown") or "").lstrip()
    if not add:
        return out
    out["markdown"] = f"{old}\n\n{add}" if old else add
    return out


def _make_row_appender(dedupe_key: Optional[str] = None) -> MergeFn:
    """Factory: append rows; optional dedup by a single field."""
    def _fn(existing: dict, new: dict) -> dict:
        rows = list(existing.get("rows") or [])
        if dedupe_key:
            seen = {r.get(dedupe_key) for r in rows if isinstance(r, dict)}
            for r in (new.get("rows") or []):
                if isinstance(r, dict):
                    if r.get(dedupe_key) in seen:
                        # last-wins: replace existing row with same key
                        rows = [x for x in rows if not (isinstance(x, dict) and x.get(dedupe_key) == r.get(dedupe_key))]
                    rows.append(r)
                    seen.add(r.get(dedupe_key))
        else:
            rows.extend(new.get("rows") or [])
        out = dict(existing)
        out["rows"] = rows
        return out
    return _fn


def _merge_key_value(existing: dict, new: dict) -> dict:
    """Two shapes: flat-dict (patternProperties keys) OR {items:[...]}.
    Heuristic: if new is items-shape, append items by `key`. Else merge dicts."""
    if "items" in new and isinstance(new["items"], list):
        items = list(existing.get("items") or [])
        seen = {it.get("key") for it in items if isinstance(it, dict)}
        for it in new["items"]:
            if isinstance(it, dict) and it.get("key") in seen:
                items = [x for x in items if not (isinstance(x, dict) and x.get("key") == it.get("key"))]
            items.append(it)
            if isinstance(it, dict):
                seen.add(it.get("key"))
        out = dict(existing)
        out["items"] = items
        return out
    # Flat-dict merge — new wins per key
    out = dict(existing)
    for k, v in new.items():
        if k in ("caption", "caption_skip_autofill", "items"):
            out[k] = v
        else:
            out[k] = v
    return out


def _merge_files(existing: dict, new: dict) -> dict:
    """Append uploaded files; dedupe by file_id."""
    files = list(existing.get("files") or [])
    seen = {f.get("file_id") for f in files if isinstance(f, dict)}
    for f in (new.get("files") or []):
        if isinstance(f, dict) and f.get("file_id") not in seen:
            files.append(f)
            seen.add(f.get("file_id"))
    out = dict(existing)
    if files:
        out["files"] = files
    if "caption" in new:
        out["caption"] = new["caption"]
    return out


def _merge_flowchart(existing: dict, new: dict) -> dict:
    """Append steps in order; dedupe by label."""
    items = list(existing.get("items") or [])
    seen = {it.get("label") for it in items if isinstance(it, dict)}
    for it in (new.get("items") or []):
        if isinstance(it, dict) and it.get("label") not in seen:
            items.append(it)
            seen.add(it.get("label"))
    out = dict(existing)
    out["items"] = items
    return out


def _merge_raci(existing: dict, new: dict) -> dict:
    """Merge by activity label; deep-merge per-role assignments."""
    rows: list[dict] = []
    by_label: dict[str, dict] = {}
    for r in (existing.get("rows") or []):
        if isinstance(r, dict) and r.get("label"):
            row_copy = dict(r)
            row_copy["assignments"] = dict(r.get("assignments") or {})
            rows.append(row_copy)
            by_label[r["label"]] = row_copy
    for r in (new.get("rows") or []):
        if not isinstance(r, dict) or not r.get("label"):
            continue
        existing_row = by_label.get(r["label"])
        if existing_row is not None:
            existing_row.setdefault("assignments", {}).update(r.get("assignments") or {})
        else:
            row_copy = dict(r)
            row_copy["assignments"] = dict(r.get("assignments") or {})
            rows.append(row_copy)
            by_label[r["label"]] = row_copy
    out = dict(existing)
    out["rows"] = rows
    return out


def _merge_sankey(existing: dict, new: dict) -> dict:
    """Append nodes + links; dedupe nodes by label, links by (source,target)."""
    nodes = list(existing.get("nodes") or [])
    node_labels = {n.get("label") for n in nodes if isinstance(n, dict)}
    for n in (new.get("nodes") or []):
        if isinstance(n, dict) and n.get("label") and n["label"] not in node_labels:
            nodes.append(n)
            node_labels.add(n["label"])
    links = list(existing.get("links") or [])
    link_keys = {(l.get("source"), l.get("target")) for l in links if isinstance(l, dict)}
    for l in (new.get("links") or []):
        if isinstance(l, dict) and (l.get("source"), l.get("target")) not in link_keys:
            links.append(l)
            link_keys.add((l.get("source"), l.get("target")))
    out = dict(existing)
    out["nodes"] = nodes
    out["links"] = links
    return out


def _merge_network(existing: dict, new: dict) -> dict:
    """Same as sankey but nodes use `id` and edges use (source,target)."""
    nodes = list(existing.get("nodes") or [])
    node_ids = {n.get("id") for n in nodes if isinstance(n, dict)}
    for n in (new.get("nodes") or []):
        if isinstance(n, dict) and n.get("id") and n["id"] not in node_ids:
            nodes.append(n)
            node_ids.add(n["id"])
    edges = list(existing.get("edges") or [])
    edge_keys = {(e.get("source"), e.get("target")) for e in edges if isinstance(e, dict)}
    for e in (new.get("edges") or []):
        if isinstance(e, dict) and (e.get("source"), e.get("target")) not in edge_keys:
            edges.append(e)
            edge_keys.add((e.get("source"), e.get("target")))
    out = dict(existing)
    out["nodes"] = nodes
    out["edges"] = edges
    return out


def _merge_progress_bar(existing: dict, new: dict) -> dict:
    """Merge by label; last-wins for value/max updates."""
    items: list[dict] = []
    by_label: dict[str, int] = {}
    for it in (existing.get("items") or []):
        if isinstance(it, dict) and it.get("label"):
            by_label[it["label"]] = len(items)
            items.append(dict(it))
    for it in (new.get("items") or []):
        if not isinstance(it, dict) or not it.get("label"):
            continue
        if it["label"] in by_label:
            items[by_label[it["label"]]].update(it)
        else:
            by_label[it["label"]] = len(items)
            items.append(dict(it))
    out = dict(existing)
    out["items"] = items
    return out


# --------------------------------------------------------------------------- #
# Strategy table
# --------------------------------------------------------------------------- #
STRATEGY: dict[str, MergeFn] = {
    # event/time list widgets
    "milestone":     _merge_milestone,
    # bullet list widgets
    "bulleted_list": _merge_bulleted_list,
    "flowchart":     _merge_flowchart,
    # row-based widgets (no dedup key — pure append)
    "table":     _make_row_appender(None),
    "chart":     _make_row_appender(None),
    "scatter":   _make_row_appender(None),
    "scatter3d": _make_row_appender(None),
    # row-based with label-keyed dedup
    "pie":      _make_row_appender("label"),
    "waffle":   _make_row_appender("label"),
    "treemap":  _make_row_appender("label"),
    "packing":  _make_row_appender("label"),
    "tree":     _make_row_appender("label"),
    "mind_map": _make_row_appender("label"),
    "quadrant": _make_row_appender("id"),
    "comparison": _make_row_appender("key"),
    # text widgets
    "rich_text": _merge_rich_text,
    "key_value": _merge_key_value,
    # graph widgets
    "sankey":  _merge_sankey,
    "network": _merge_network,
    # progress widgets
    "progress_bar": _merge_progress_bar,
    # media widgets — append files
    "image":      _merge_files,
    "video":      _merge_files,
    "attachment": _merge_files,
    # widgets with no natural append — fall through to replace
    # (heading, equation, html_embed, cad_3d, radar, heatmap, contour, box, density)
}


def merge_block_content(widget_type: str, existing: dict | None, new: dict) -> dict:
    """Merge `new` content into `existing` for the given widget type.

    If `existing` is None / not a dict, returns `new` unchanged (first-write).
    Widgets without an explicit strategy fall through to replace.
    """
    if not isinstance(existing, dict):
        return new
    fn = STRATEGY.get(widget_type, _replace)
    return fn(existing, new)


def is_appendable(widget_type: str) -> bool:
    """True when the widget has a real append semantic (not pure replace)."""
    return widget_type in STRATEGY
