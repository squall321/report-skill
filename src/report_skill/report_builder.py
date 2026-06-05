"""Build a ReportCreate payload from template + per-block normalized content."""
from __future__ import annotations

from datetime import date
from typing import Optional

# Legacy status → new phase. Backend rename:
#   status enum (draft / in_progress / completed)
#   → phase enum (drafting / reviewing / finalized)
# Drafts saved before the migration still carry the old labels — accept both.
_LEGACY_STATUS_TO_PHASE = {
    "draft": "drafting",
    "in_progress": "reviewing",
    "completed": "finalized",
}


def normalize_phase(value: Optional[str]) -> Optional[str]:
    """Map legacy status values to the new phase enum; pass new ones through."""
    if value is None:
        return None
    return _LEGACY_STATUS_TO_PHASE.get(value, value)


def compute_blocks_order(
    template: dict,
    content: dict,
    extras: list,
    *,
    explicit: Optional[list] = None,
) -> list:
    """Compute a page's `blocks_order` per the CR-2 + CR-8 rules.

    Returns `explicit` verbatim if provided. Otherwise auto-computes:
        heading extras (input order)
        + filled template blocks (template's natural order)
        + non-heading extras (input order)

    CR-2: filled template blocks only — empty ones stay out so the backend
    hides them, preventing the blank-box render.
    CR-8: heading extras float to the top so titles/dividers land above
    the body. Relative order within each group is preserved from input.

    Used by `build_create_payload_multi` (create flow) AND by edit-flow
    code paths (CR-11): `report_ops.add_page` and `report_ops.update_blocks`.
    """
    if explicit is not None:
        return list(explicit)
    schema_blocks = (template.get("schema") or {}).get("blocks") or []
    template_order = [b["id"] for b in schema_blocks
                      if isinstance(b, dict) and b.get("id") in content]
    heading_extras_order = [e["id"] for e in extras
                            if isinstance(e, dict) and e.get("id")
                            and e.get("type") == "heading"]
    non_heading_extras_order = [e["id"] for e in extras
                                if isinstance(e, dict) and e.get("id")
                                and e.get("type") != "heading"]
    return heading_extras_order + template_order + non_heading_extras_order


def merge_blocks_order(existing: list, add_ids: list) -> list:
    """Append `add_ids` to `existing` blocks_order, skipping ids already present.

    Preserves the user's existing block ordering entirely; new ids land at
    the end in input order. Used by `report_ops.update_blocks` (CR-11) when
    the caller adds new extras to a page that already has a settled order.

    Callers that want a more sophisticated insertion (e.g. heading above
    body on update) can pass an explicit `blocks_order` in their draft
    instead — that path bypasses this merge.
    """
    out = list(existing or [])
    seen = {bid for bid in out if bid}
    for bid in add_ids or []:
        if bid and bid not in seen:
            out.append(bid)
            seen.add(bid)
    return out


def build_create_payload(
    template: dict,
    content: dict,
    *,
    title: str,
    report_date: Optional[str] = None,
    phase: Optional[str] = None,
    lifecycle: Optional[str] = None,
    status: Optional[str] = None,   # legacy alias, kept for backward compat
    tags: Optional[list[str]] = None,
    page_name: str = "Main",
    extra_blocks: Optional[list[dict]] = None,
    # ---- v0.5.0 related-info + page settings (all optional) ----
    collab_workspace_slugs: Optional[list[str]] = None,
    entity_ids: Optional[list[int]] = None,
    report_type_id: Optional[int] = None,
    page_width_px: Optional[int] = None,
    page_gap_px: Optional[int] = None,
    page_blend_blocks: Optional[bool] = None,
    page_slide_guide: Optional[bool] = None,
    page_slide_ratio: Optional[str] = None,
    page_slide_ratio_custom_w: Optional[int] = None,
    page_slide_ratio_custom_h: Optional[int] = None,
    page_rich_text_prefix_d0: Optional[str] = None,
    page_rich_text_prefix_d1: Optional[str] = None,
    page_rich_text_prefix_d2: Optional[str] = None,
) -> dict:
    """ReportCreate POST body for a single-page report.

    v0.5.0 adds 13 optional top-level fields that pass through to the
    server-side ReportCreate schema (related-info tagging + page settings).
    None means leave unset. For `collab_workspace_slugs` and `entity_ids`,
    an empty list is forwarded verbatim — the backend treats `[]` as
    explicit-clear.
    """
    return build_create_payload_multi(
        pages=[{
            "template": template,
            "content": content,
            "name": page_name,
            "extra_blocks": extra_blocks or [],
        }],
        title=title,
        report_date=report_date,
        phase=phase,
        lifecycle=lifecycle,
        status=status,
        tags=tags,
        collab_workspace_slugs=collab_workspace_slugs,
        entity_ids=entity_ids,
        report_type_id=report_type_id,
        page_width_px=page_width_px,
        page_gap_px=page_gap_px,
        page_blend_blocks=page_blend_blocks,
        page_slide_guide=page_slide_guide,
        page_slide_ratio=page_slide_ratio,
        page_slide_ratio_custom_w=page_slide_ratio_custom_w,
        page_slide_ratio_custom_h=page_slide_ratio_custom_h,
        page_rich_text_prefix_d0=page_rich_text_prefix_d0,
        page_rich_text_prefix_d1=page_rich_text_prefix_d1,
        page_rich_text_prefix_d2=page_rich_text_prefix_d2,
    )


def build_create_payload_multi(
    pages: list[dict],
    *,
    title: str,
    report_date: Optional[str] = None,
    phase: Optional[str] = None,
    lifecycle: Optional[str] = None,
    status: Optional[str] = None,   # legacy alias
    tags: Optional[list[str]] = None,
    # ---- v0.5.0 related-info + page settings (all optional) ----
    collab_workspace_slugs: Optional[list[str]] = None,
    entity_ids: Optional[list[int]] = None,
    report_type_id: Optional[int] = None,
    page_width_px: Optional[int] = None,
    page_gap_px: Optional[int] = None,
    page_blend_blocks: Optional[bool] = None,
    page_slide_guide: Optional[bool] = None,
    page_slide_ratio: Optional[str] = None,
    page_slide_ratio_custom_w: Optional[int] = None,
    page_slide_ratio_custom_h: Optional[int] = None,
    page_rich_text_prefix_d0: Optional[str] = None,
    page_rich_text_prefix_d1: Optional[str] = None,
    page_rich_text_prefix_d2: Optional[str] = None,
) -> dict:
    """ReportCreate POST body for a multi-page report.

    `pages` items: { template: <fetched template dict>, content: {block_id: ...},
                     name: str, extra_blocks?: [...] }
    The report's top-level template_id/version mirror pages[0] (backend requires this).

    v0.5.0 adds 13 optional top-level fields:
      - related-info tagging: `collab_workspace_slugs`, `entity_ids`, `report_type_id`
      - page settings: `page_width_px`, `page_gap_px`, `page_blend_blocks`,
        `page_slide_guide`, `page_slide_ratio`,
        `page_slide_ratio_custom_w`, `page_slide_ratio_custom_h`,
        `page_rich_text_prefix_d0/d1/d2`
    None means omit from payload. For `collab_workspace_slugs` and
    `entity_ids`, an empty list is forwarded verbatim — the backend treats
    `[]` as explicit-clear.
    """
    if not pages:
        raise ValueError("at least one page required")

    page_payloads: list[dict] = []
    for p in pages:
        tpl = p["template"]
        content = p["content"]
        extras = p.get("extra_blocks") or []

        # CR-2 + CR-8 — auto-compute blocks_order (see compute_blocks_order
        # docstring). Honors p["blocks_order"] when explicitly set.
        blocks_order = compute_blocks_order(
            tpl, content, extras, explicit=p.get("blocks_order"),
        )

        page_payloads.append({
            "template_id": tpl["template_id"],
            "template_version": tpl["version"],
            "name": p.get("name") or tpl.get("name") or "Page",
            "content": content,
            "blocks_order": blocks_order,
            **({"extra_blocks": extras} if extras else {}),
        })

    primary = pages[0]["template"]
    body: dict = {
        "template_id": primary["template_id"],
        "template_version": primary["version"],
        "title": title,
        "tags": tags or [],
        "pages": page_payloads,
    }
    # Map legacy status → phase. Backend rename: drafting / reviewing / finalized.
    resolved_phase = normalize_phase(phase) or normalize_phase(status)
    if resolved_phase is not None:
        body["phase"] = resolved_phase
    if lifecycle is not None:
        body["lifecycle"] = lifecycle
    body["report_date"] = report_date or date.today().isoformat()

    # ---- v0.5.0 optional top-level fields ----
    # Lists with empty-list-clears semantics: include whenever caller passed
    # any list (including []). None means leave unset.
    if collab_workspace_slugs is not None:
        body["collab_workspace_slugs"] = list(collab_workspace_slugs)
    if entity_ids is not None:
        body["entity_ids"] = list(entity_ids)
    # Scalar optionals: include only when not None.
    if report_type_id is not None:
        body["report_type_id"] = report_type_id
    if page_width_px is not None:
        body["page_width_px"] = page_width_px
    if page_gap_px is not None:
        body["page_gap_px"] = page_gap_px
    if page_blend_blocks is not None:
        body["page_blend_blocks"] = page_blend_blocks
    if page_slide_guide is not None:
        body["page_slide_guide"] = page_slide_guide
    if page_slide_ratio is not None:
        body["page_slide_ratio"] = page_slide_ratio
    if page_slide_ratio_custom_w is not None:
        body["page_slide_ratio_custom_w"] = page_slide_ratio_custom_w
    if page_slide_ratio_custom_h is not None:
        body["page_slide_ratio_custom_h"] = page_slide_ratio_custom_h
    if page_rich_text_prefix_d0 is not None:
        body["page_rich_text_prefix_d0"] = page_rich_text_prefix_d0
    if page_rich_text_prefix_d1 is not None:
        body["page_rich_text_prefix_d1"] = page_rich_text_prefix_d1
    if page_rich_text_prefix_d2 is not None:
        body["page_rich_text_prefix_d2"] = page_rich_text_prefix_d2

    return body
