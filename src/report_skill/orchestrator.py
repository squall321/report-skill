"""Block-by-block normalize → validate → repair → fallback pipeline.

P1 scope: deterministic only (no LLM retries). Fallback now walks the
adapter chain (`fallback_to()` hops) up to `tier.policy().fallback_chain_depth`
and, when a fallback adapter produces valid content, the original typed
block is left empty (content key absent → template-level "missing field"
trivially passes) while a synthetic ``<id>__fb`` extra_block carrying the
simpler widget gets appended. The original BlockReport is marked
``fell_back`` with chain detail, and a second report is emitted for the
synthetic block.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from report_skill import schemas, tier, validate
from report_skill.adapters import ADAPTERS
from report_skill.adapters.base import NormalizeError, WidgetAdapter


@dataclass
class BlockReport:
    block_id: str
    widget_type: str
    status: str                     # ok | repaired | skipped | failed | unsupported | fell_back
    detail: Optional[str] = None
    content: Optional[dict] = None
    issues: list[validate.ValidationIssue] = field(default_factory=list)

    def short(self) -> str:
        suffix = f"  ({self.detail})" if self.detail else ""
        return f"[{self.status:>10}] {self.block_id} ({self.widget_type}){suffix}"


@dataclass
class NormalizeResult:
    content: dict                   # {block_id: content_dict} — only ok+repaired
    blocks: list[BlockReport]
    extra_blocks: list[dict] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for b in self.blocks if b.status in ("ok", "repaired"))

    @property
    def failed_count(self) -> int:
        return sum(1 for b in self.blocks if b.status == "failed")

    @property
    def skipped_count(self) -> int:
        return sum(1 for b in self.blocks if b.status == "skipped")


def normalize_report(template: dict, draft_blocks: dict[str, Any],
                     snapshot: dict,
                     extra_blocks_input: list[dict] | None = None) -> NormalizeResult:
    """Walk the template's blocks and normalize each from the input dict.

    `extra_blocks_input` is an optional list of ad-hoc block definitions:
        [{"id": "custom_1", "type": "rich_text", "props": {...}, "input": <anything-ish>}]
    These get normalized through the same pipeline and exposed on the
    result for the builder to inject into the page's extra_blocks array.
    """
    blocks_schema = template.get("schema", {}).get("blocks", [])
    out_content: dict = {}
    reports: list[BlockReport] = []
    extras: list[dict] = []

    depth = tier.policy().fallback_chain_depth

    for block in blocks_schema:
        bid = block["id"]
        wtype = block["type"]
        raw = draft_blocks.get(bid)
        if raw is None:
            reports.append(BlockReport(bid, wtype, "skipped", "no input provided"))
            continue
        result = normalize_block(bid, wtype, raw, block, snapshot)
        if result.status in ("ok", "repaired") and result.content is not None:
            out_content[bid] = result.content
            reports.append(result)
            continue
        # Failure path — try cross-widget fallback chain unless we are missing
        # an adapter entirely (nothing to chain from) or depth is disabled.
        if result.status == "failed" and depth > 0:
            fb_outcome = _try_fallback_chain(bid, wtype, raw, block, snapshot, depth)
            if fb_outcome is not None:
                chain_types, fb_type, fb_content, fb_props = fb_outcome
                arrow = " → ".join(chain_types)
                result.status = "fell_back"
                result.detail = f"chain: {arrow} (validation fix)"
                reports.append(result)

                fb_id = f"{bid}__fb"
                out_content[fb_id] = fb_content
                extras.append({
                    "id": fb_id,
                    "type": fb_type,
                    "props": fb_props,
                })
                reports.append(BlockReport(
                    fb_id, fb_type, "ok",
                    detail=f"synthetic fallback for {bid} ({wtype})",
                    content=fb_content,
                ))
                continue
        reports.append(result)

    for extra in extra_blocks_input or []:
        bid = extra.get("id")
        wtype = extra.get("type")
        raw = extra.get("input") or extra.get("content")
        if not bid or not wtype or raw is None:
            reports.append(BlockReport(bid or "?", wtype or "?", "failed",
                                       "extra block missing id/type/input"))
            continue
        synth_block = {"id": bid, "type": wtype, "props": extra.get("props", {})}
        result = normalize_block(bid, wtype, raw, synth_block, snapshot)
        result.detail = f"(extra) {result.detail or ''}".strip()
        reports.append(result)
        if result.status in ("ok", "repaired") and result.content is not None:
            out_content[bid] = result.content
            extras.append({
                "id": bid,
                "type": wtype,
                "props": extra.get("props", {}),
                **({"layout": extra["layout"]} if "layout" in extra else {}),
            })

    return NormalizeResult(content=out_content, blocks=reports, extra_blocks=extras)


def normalize_block(block_id: str, widget_type: str, raw: Any,
                    template_block: dict, snapshot: dict) -> BlockReport:
    adapter: Optional[WidgetAdapter] = ADAPTERS.get(widget_type)
    if adapter is None:
        return BlockReport(block_id, widget_type, "unsupported",
                           f"no adapter registered for widget type '{widget_type}'")

    props = schemas.resolved_props(template_block, snapshot)
    schema = schemas.content_schema(snapshot, widget_type)

    try:
        normalized = adapter.normalize(raw, props)
    except NormalizeError as e:
        return BlockReport(block_id, widget_type, "failed", f"normalize: {e}")

    issues = validate.validate(normalized, schema) if schema else []
    if not issues:
        return BlockReport(block_id, widget_type, "ok", content=normalized)

    # Iterative deterministic post-repair, bounded by tier policy.
    # Each pass strips unknown keys, coerces near-miss enums, etc; the
    # number of passes scales with how aggressive the tier wants to be
    # (S=1, M=2, W=3 — see tier.TierPolicy).
    max_passes = max(1, tier.policy().max_repair_passes)
    current = normalized
    repaired_any = False
    fix_count = 0
    for _ in range(max_passes):
        candidate = _post_repair(current, issues)
        if candidate is None:
            break
        repaired_any = True
        fix_count += len(issues)
        current = candidate
        issues = validate.validate(current, schema)
        if not issues:
            return BlockReport(
                block_id, widget_type, "repaired",
                detail=f"auto-fixed {fix_count} validation issue(s)",
                content=current,
            )

    if repaired_any:
        # Made progress but still issues — fall through to failure handling.
        normalized = current  # surface the partially-repaired content in issue context

    return BlockReport(block_id, widget_type, "failed",
                       detail=f"validation: {issues[0]}",
                       issues=issues)


def _try_fallback_chain(block_id: str, widget_type: str, raw: Any,
                        template_block: dict, snapshot: dict,
                        max_depth: int) -> Optional[tuple[list[str], str, dict, dict]]:
    """Walk `adapter.fallback_to()` hops trying each as a substitute for the
    failing block. Returns (chain_types, final_type, content, props) on the
    first success, or None when the chain bottoms out / depth runs out.

    The chain is recorded inclusive of the originating widget for nicer logs:
        ["table", "bulleted_list"]  →  detail: "chain: table → bulleted_list"
    """
    chain_types: list[str] = [widget_type]
    current = ADAPTERS.get(widget_type)
    if current is None:
        return None

    original_label = (template_block.get("props") or {}).get("label") or block_id

    visited: set[str] = {widget_type}
    hops = 0
    while hops < max_depth:
        nxt = current.fallback_to()
        if not nxt or nxt in visited:
            return None
        visited.add(nxt)
        fb_adapter = ADAPTERS.get(nxt)
        if fb_adapter is None:
            return None
        chain_types.append(nxt)

        fb_props = {"label": original_label}
        synth_block = {"id": f"{block_id}__fb", "type": nxt, "props": fb_props}
        attempt = normalize_block(f"{block_id}__fb", nxt, raw, synth_block, snapshot)
        if attempt.status in ("ok", "repaired") and attempt.content is not None:
            return chain_types, nxt, attempt.content, fb_props

        current = fb_adapter
        hops += 1
    return None


def _post_repair(content: dict, issues: list[validate.ValidationIssue]) -> Optional[dict]:
    """Generic post-normalize repairs. Returns a new dict or None if nothing applies.

    Currently handles:
      - additionalProperties: strip unknown top-level keys.
    """
    repaired = dict(content)
    changed = False

    for issue in issues:
        if issue.raw.validator == "additionalProperties":
            # err.path is the parent; err.message lists the bad keys.
            # Strip them at the top level if that's where the issue is.
            schema = issue.raw.schema
            allowed = set((schema.get("properties") or {}).keys())
            pattern_props = schema.get("patternProperties", {})
            import re as _re
            patterns = [_re.compile(p) for p in pattern_props.keys()]
            if not issue.raw.absolute_path:
                # top-level — safe to strip
                target = repaired
            else:
                target = _drill(repaired, issue.raw.absolute_path)
                if not isinstance(target, dict):
                    continue
            for k in list(target.keys()):
                if k in allowed:
                    continue
                if any(p.match(k) for p in patterns):
                    continue
                target.pop(k, None)
                changed = True

    return repaired if changed else None


def _drill(obj: Any, path) -> Any:
    cur = obj
    for p in path:
        if isinstance(p, int) and isinstance(cur, list) and 0 <= p < len(cur):
            cur = cur[p]
        elif isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur
