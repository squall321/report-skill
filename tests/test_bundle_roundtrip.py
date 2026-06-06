"""bundle.ready_for_recreate — round-trip lock for the v0.5.2 _TOP_KEEP /
_PAGE_KEEP expansions + the entities → entity_ids projection + the
finalized → drafting phase down-shift (C1 / C3 / C6).

Catches the v0.5.0-class regression where a new top-level or page-level
ReportRead field gets added on the backend (e.g. layout_overrides,
props_overrides, block_sections, closed_at) and bundle export silently
drops it — the imported report ends up with a different layout than
the source.
"""
from __future__ import annotations

from report_skill import bundle


def _fake_report_read() -> dict:
    """A ReportRead-shaped dict that exercises every kept field. Mirrors
    the backend schema in d:/ReportArchive/backend/app/modules/reports/
    schemas.py (ReportRead, ReportPage).
    """
    return {
        # ---- server-managed fields (must be stripped by ready_for_recreate) -- #
        "id": 42,
        "owner_id": 9,
        "owner_name": "박국진",
        "workspace_slug": "personal-9",
        "created_at": "2026-05-01T00:00:00Z",
        "updated_at": "2026-06-01T00:00:00Z",
        "revision": 7,
        "author_lock_enabled": False,
        # ---- _TOP_KEEP fields (must survive) -------------------------------- #
        "title": "분기 회고",
        "report_date": "2026-06-01",
        "tags": ["회고", "백엔드"],
        "phase": "finalized",  # → down-shifted to drafting (C6)
        "lifecycle": "single_shot",
        "template_id": "weekly-dev",
        "template_version": 1,
        "collab_workspace_slugs": ["dx", "platform"],
        "report_type_id": 3,
        "page_width_px": 1280,
        "page_gap_px": 24,
        "page_blend_blocks": True,
        "page_slide_guide": False,
        "page_slide_ratio": "16:9",
        "page_slide_ratio_custom_w": 1920,
        "page_slide_ratio_custom_h": 1080,
        "page_rich_text_prefix_d0": "1.",
        "page_rich_text_prefix_d1": "1)",
        "page_rich_text_prefix_d2": "-",
        "closed_at": "2026-06-30",  # v0.5.2 C3
        # ---- entities → entity_ids projection ------------------------------- #
        "entities": [
            {"id": 101, "value": "HFP-X1"},
            {"id": 202, "value": "현대모비스"},
            {"id": "non-int-id", "value": "drop me"},   # bad id → dropped
            "not-a-dict",                                # garbage → dropped
        ],
        # ---- pages (each must keep the v0.5.2 _PAGE_KEEP additions) -------- #
        "pages": [
            {
                # server-managed page fields (must be stripped)
                "id": 100,
                "report_id": 42,
                "page_index": 0,
                # _PAGE_KEEP — must survive
                "template_id": "weekly-dev",
                "template_version": 1,
                "name": "Main",
                "content": {"summary": {"items": [{"text": "x"}]}},
                "extra_blocks": [{"id": "extra1", "type": "heading"}],
                "blocks_order": ["extra1", "summary"],
                # v0.5.2 _PAGE_KEEP additions
                "layout_overrides": {
                    "summary": {"col_start": 1, "col_span": 6,
                                "row_start": 1, "row_span": 3},
                },
                "props_overrides": {
                    "summary": {"text_style": {"bold": True}},
                },
                "block_sections": {"summary": "intro", "extra1": None},
            },
        ],
    }


# --------------------------------------------------------------------------- #
# C1 + C3 — every kept top-level / page-level field survives
# --------------------------------------------------------------------------- #
def test_ready_for_recreate_preserves_all_top_keep_fields() -> None:
    src = _fake_report_read()
    out = bundle.ready_for_recreate(src)
    # All TOP_KEEP fields present (except `phase` which gets down-shifted —
    # still present, just with the new value).
    for key in bundle._TOP_KEEP:
        if key not in src:
            continue
        assert key in out, (
            f"ready_for_recreate dropped _TOP_KEEP field {key!r} — "
            "bundle export would silently lose this on round-trip."
        )


def test_ready_for_recreate_preserves_all_page_keep_fields() -> None:
    src = _fake_report_read()
    out = bundle.ready_for_recreate(src)
    assert out.get("pages") and len(out["pages"]) == 1
    page_out = out["pages"][0]
    src_page = src["pages"][0]
    for key in bundle._PAGE_KEEP:
        if key not in src_page:
            continue
        assert key in page_out, (
            f"ready_for_recreate dropped _PAGE_KEEP field {key!r} — "
            "bundle export would silently lose this on round-trip."
        )
        # Value parity — the kept field must round-trip verbatim (no
        # silent coercion / re-shaping at the bundle layer).
        assert page_out[key] == src_page[key], (
            f"_PAGE_KEEP field {key!r} was mutated during ready_for_recreate "
            f"(src={src_page[key]!r}, out={page_out[key]!r})"
        )


# --------------------------------------------------------------------------- #
# C6 — phase=finalized down-shifts to drafting
# --------------------------------------------------------------------------- #
def test_phase_finalized_down_shifts_to_drafting() -> None:
    src = _fake_report_read()
    assert src["phase"] == "finalized"
    out = bundle.ready_for_recreate(src)
    assert out.get("phase") == "drafting", (
        "phase=finalized must down-shift to 'drafting' on bundle export — "
        "otherwise import lands edit-blocked. "
        f"Got phase={out.get('phase')!r}."
    )


def test_phase_drafting_passes_through() -> None:
    """Sanity — non-finalized phases must NOT be touched."""
    src = _fake_report_read()
    src["phase"] = "reviewing"
    out = bundle.ready_for_recreate(src)
    assert out.get("phase") == "reviewing", (
        f"phase={src['phase']!r} should pass through verbatim; got "
        f"{out.get('phase')!r}"
    )


# --------------------------------------------------------------------------- #
# entities → entity_ids projection
# --------------------------------------------------------------------------- #
def test_entities_project_to_entity_ids() -> None:
    src = _fake_report_read()
    out = bundle.ready_for_recreate(src)
    # Only the two int-id entries survive; the bad-id dict and the
    # not-a-dict garbage get dropped silently.
    assert out.get("entity_ids") == [101, 202], (
        f"entity projection wrong: {out.get('entity_ids')}"
    )
    # The original `entities` array must NOT be carried over — the write
    # contract uses entity_ids, not entities.
    assert "entities" not in out, (
        "ready_for_recreate leaked the read-side `entities` field — "
        "ReportCreate uses entity_ids and would reject `entities`."
    )


def test_entities_absent_yields_no_entity_ids_key() -> None:
    """When the source has no entities, the output must not invent an
    empty entity_ids list (server distinguishes [] = clear from absent)."""
    src = _fake_report_read()
    src.pop("entities", None)
    out = bundle.ready_for_recreate(src)
    assert "entity_ids" not in out


# --------------------------------------------------------------------------- #
# Server-managed strip — id / owner / revision / timestamps must not leak
# --------------------------------------------------------------------------- #
def test_ready_for_recreate_strips_server_managed_fields() -> None:
    src = _fake_report_read()
    out = bundle.ready_for_recreate(src)
    for forbidden in ("id", "owner_id", "owner_name", "workspace_slug",
                      "created_at", "updated_at", "revision",
                      "author_lock_enabled"):
        assert forbidden not in out, (
            f"ready_for_recreate leaked server-managed field {forbidden!r}; "
            "the ReportCreate POST would either reject it or use stale data."
        )
    # Page-level server-managed fields stripped too.
    page = out["pages"][0]
    for forbidden in ("id", "report_id", "page_index"):
        assert forbidden not in page, (
            f"ready_for_recreate leaked page-server field {forbidden!r}"
        )
