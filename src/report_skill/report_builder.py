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

        # CR-2 fix — derive blocks_order so empty template blocks stay
        # hidden in the rendered report. Without this the backend defaults
        # to showing every template block (including the ones the user
        # didn't fill), which surfaces blank boxes and pushes extras out
        # of the user's intended order.
        # Honor an explicit per-page override if the caller passed one;
        # otherwise auto-compute from (template blocks the user filled in
        # template natural order) + (extras in input order).
        if p.get("blocks_order") is not None:
            blocks_order = list(p["blocks_order"])
        else:
            schema_blocks = (tpl.get("schema") or {}).get("blocks") or []
            template_order = [b["id"] for b in schema_blocks
                              if isinstance(b, dict) and b.get("id") in content]
            # CR-8 — heading extras float to the top of the page by default
            # so titles/section dividers introduced as extras land above the
            # filled template body instead of getting appended at the bottom.
            # Relative order among headings (and among non-headings) is
            # preserved from the input extras list.
            heading_extras_order = [e["id"] for e in extras
                                    if isinstance(e, dict) and e.get("id")
                                    and e.get("type") == "heading"]
            non_heading_extras_order = [e["id"] for e in extras
                                        if isinstance(e, dict) and e.get("id")
                                        and e.get("type") != "heading"]
            blocks_order = heading_extras_order + template_order + non_heading_extras_order

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
