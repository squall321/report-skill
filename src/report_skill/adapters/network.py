"""Network adapter — nodes + edges.

Accepts: dict with `nodes`/`edges`, adjacency dict (id → list of neighbour ids),
list of edge dicts (nodes are derived), or pre-shaped passthrough.
"""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import coerce_number, truncate

_PASSTHROUGH = (
    "caption", "caption_skip_autofill", "directed", "layout", "node_shape",
    "show_labels", "show_edge_labels", "color_by_group", "node_size_by_value",
    "node_size_min", "node_size_max", "link_distance", "charge_strength",
)
_NODE_KEYS = ("id", "label", "group", "value", "color", "x", "y", "fixed")
_EDGE_KEYS = ("source", "target", "weight", "label", "directed", "color")


class NetworkAdapter(WidgetAdapter):
    type = "network"

    def normalize(self, raw: Any, props: dict) -> dict:
        out: dict = {}
        nodes: list[dict] = []
        edges: list[dict] = []

        if isinstance(raw, dict):
            for k in _PASSTHROUGH:
                if k in raw:
                    out[k] = raw[k]
            has_nodes = isinstance(raw.get("nodes"), list)
            has_edges = isinstance(raw.get("edges"), list)
            if has_nodes or has_edges:
                if has_nodes:
                    nodes = [_coerce_node(n) for n in raw["nodes"]]
                if has_edges:
                    edges = [_coerce_edge(e) for e in raw["edges"]]
            else:
                # adjacency: { "A": ["B", "C"], ... }
                for src, neighbours in raw.items():
                    if src in _PASSTHROUGH:
                        continue
                    if isinstance(neighbours, (list, tuple)):
                        for nb in neighbours:
                            edges.append({"source": str(src), "target": str(nb)})
        elif isinstance(raw, list):
            # list of edge dicts
            edges = [_coerce_edge(e) for e in raw if isinstance(e, dict)]
        else:
            raise NormalizeError(f"network: unsupported input {type(raw).__name__}")

        nodes = [n for n in nodes if n and n.get("id")]
        edges = [e for e in edges if e and e.get("source") and e.get("target")]

        # Derive missing nodes from edge endpoints
        seen_ids = {n["id"] for n in nodes}
        for e in edges:
            for ep in (e["source"], e["target"]):
                if ep not in seen_ids:
                    nodes.append({"id": ep})
                    seen_ids.add(ep)

        if not nodes:
            raise NormalizeError("network: no nodes after normalization")
        out["nodes"] = nodes
        if edges:
            out["edges"] = edges
        return out

    def fallback_to(self) -> str:
        return "table"


def _coerce_node(raw: Any) -> dict:
    if not isinstance(raw, dict):
        if isinstance(raw, str):
            return {"id": truncate(raw, 200)}
        return {}
    node: dict = {}
    nid = raw.get("id") or raw.get("label")
    if nid is None:
        return {}
    node["id"] = truncate(str(nid), 200)
    for k in ("label", "group", "color"):
        if raw.get(k) is not None:
            node[k] = truncate(str(raw[k]), 200 if k != "color" else 64)
    if raw.get("value") is not None:
        n = coerce_number(raw["value"])
        if n is not None:
            node["value"] = n
    for k in ("x", "y"):
        if raw.get(k) is not None:
            n = coerce_number(raw[k])
            if n is not None:
                node[k] = n
    if isinstance(raw.get("fixed"), bool):
        node["fixed"] = raw["fixed"]
    return node


def _coerce_edge(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {}
    src = raw.get("source") or raw.get("from")
    tgt = raw.get("target") or raw.get("to")
    if src is None or tgt is None:
        return {}
    edge: dict = {"source": truncate(str(src), 200), "target": truncate(str(tgt), 200)}
    if raw.get("weight") is not None:
        n = coerce_number(raw["weight"])
        if n is not None:
            edge["weight"] = n
    if raw.get("label") is not None:
        edge["label"] = truncate(str(raw["label"]), 200)
    if isinstance(raw.get("directed"), bool):
        edge["directed"] = raw["directed"]
    if raw.get("color") is not None:
        edge["color"] = truncate(str(raw["color"]), 64)
    return edge
