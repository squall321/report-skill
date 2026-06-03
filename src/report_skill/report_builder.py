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
) -> dict:
    """ReportCreate POST body for a single-page report."""
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
) -> dict:
    """ReportCreate POST body for a multi-page report.

    `pages` items: { template: <fetched template dict>, content: {block_id: ...},
                     name: str, extra_blocks?: [...] }
    The report's top-level template_id/version mirror pages[0] (backend requires this).
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
    return body
