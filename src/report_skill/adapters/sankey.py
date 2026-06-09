"""Sankey adapter — flows between nodes (source/target/value).

Accepts: list of flow dicts, adjacency dict (source → {target: value}), or
pre-shaped dict with `nodes`/`links`.
"""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import coerce_number, truncate

_PASSTHROUGH = (
    "caption", "caption_skip_autofill", "arrangement", "node_pad",
    "node_thickness", "unit",
    # v0.9.1 — RA defcb74 caption color tokens
    "caption_color", "caption_html",
)


class SankeyAdapter(WidgetAdapter):
    type = "sankey"

    def normalize(self, raw: Any, props: dict) -> dict:
        out: dict = {}
        nodes: list[dict] = []
        links: list[dict] = []

        if isinstance(raw, dict):
            for k in _PASSTHROUGH:
                if k in raw:
                    out[k] = raw[k]
            has_links = isinstance(raw.get("links"), list) or isinstance(raw.get("flows"), list)
            has_nodes = isinstance(raw.get("nodes"), list)
            if has_links or has_nodes:
                if has_nodes:
                    nodes = [_coerce_node(n) for n in raw["nodes"]]
                src = raw.get("links") if isinstance(raw.get("links"), list) else raw.get("flows", [])
                links = [_coerce_link(f) for f in src]
            else:
                # adjacency: { "Revenue": { "Salaries": 1000, ... }, ... }
                for src, targets in raw.items():
                    if src in _PASSTHROUGH:
                        continue
                    if isinstance(targets, dict):
                        for tgt, val in targets.items():
                            links.append(_coerce_link({"source": src, "target": tgt, "value": val}))
        elif isinstance(raw, list):
            links = [_coerce_link(f) for f in raw if isinstance(f, dict)]
        else:
            raise NormalizeError(f"sankey: unsupported input {type(raw).__name__}")

        links = [l for l in links if l and l.get("source") and l.get("target")]
        if not links:
            raise NormalizeError("sankey: no links after normalization")

        # Derive missing nodes from link endpoints
        seen = {n["label"] for n in nodes if n.get("label")}
        for l in links:
            for ep in (l["source"], l["target"]):
                if ep not in seen:
                    nodes.append({"label": ep})
                    seen.add(ep)

        out["nodes"] = nodes
        out["links"] = links
        return out

    def fallback_to(self) -> str:
        return "table"


def _coerce_node(raw: Any) -> dict:
    if isinstance(raw, str):
        return {"label": truncate(raw, 200)}
    if not isinstance(raw, dict):
        return {}
    lbl = raw.get("label") or raw.get("id")
    if lbl is None:
        return {}
    node: dict = {"label": truncate(str(lbl), 200)}
    if raw.get("color") is not None:
        node["color"] = truncate(str(raw["color"]), 32)
    return node


def _coerce_link(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {}
    src = raw.get("source") or raw.get("from")
    tgt = raw.get("target") or raw.get("to")
    if src is None or tgt is None:
        return {}
    link: dict = {"source": truncate(str(src), 200), "target": truncate(str(tgt), 200)}
    v = raw.get("value") if "value" in raw else raw.get("weight")
    if v is not None:
        n = coerce_number(v)
        link["value"] = n if (n is None or n >= 0) else 0.0
    if raw.get("color") is not None:
        link["color"] = truncate(str(raw["color"]), 32)
    return link
