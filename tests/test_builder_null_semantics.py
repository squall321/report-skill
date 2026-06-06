"""report_builder null-semantics lock (v0.5.2 C7 + list-clears).

Three distinct semantics must be preserved:

1. ``collab_workspace_slugs=[]`` (and ``entity_ids=[]``) — empty-list-clears.
   The empty list MUST reach the body verbatim so the backend clears the
   existing tags. Passing ``None`` (default) MUST omit the key.

2. ``entity_ids=None`` — omit the key entirely so the server leaves it
   alone. (Same rule as collab_workspace_slugs above; both lists share
   "None=omit, []=include".)

3. ``report_type_id=None`` — v0.5.2 C7 introduces the ``_UNSET`` sentinel
   so the caller can pass ``None`` explicitly to clear the field
   server-side. Behavior:
       - omitted kwarg  → key absent from body  (server keeps current)
       - ``None``       → ``"report_type_id": None`` in body (server clears)
       - integer value  → that value in body
   Without the sentinel, ``None`` and "not supplied" are indistinguishable
   and the caller can never clear the tag.

``closed_at`` follows the same _UNSET pattern (C4) — also covered here.
"""
from __future__ import annotations

from datetime import date

from report_skill import report_builder


def _template() -> dict:
    return {
        "template_id": "weekly-dev",
        "version": 1,
        "name": "주간 개발 보고",
        "schema": {"blocks": []},
    }


# --------------------------------------------------------------------------- #
# 1) collab_workspace_slugs / entity_ids — empty list clears, None omits
# --------------------------------------------------------------------------- #
def test_empty_list_collab_workspace_slugs_reaches_body() -> None:
    body = report_builder.build_create_payload(
        _template(), content={},
        title="t",
        collab_workspace_slugs=[],
    )
    assert "collab_workspace_slugs" in body, (
        "empty collab_workspace_slugs=[] must reach the body so the "
        "backend clears the existing tags."
    )
    assert body["collab_workspace_slugs"] == [], (
        f"expected [] verbatim, got {body['collab_workspace_slugs']!r}"
    )


def test_none_collab_workspace_slugs_omitted_from_body() -> None:
    body = report_builder.build_create_payload(
        _template(), content={},
        title="t",
        collab_workspace_slugs=None,
    )
    assert "collab_workspace_slugs" not in body, (
        "collab_workspace_slugs=None must be OMITTED from the body so the "
        "server leaves existing tags alone."
    )


def test_empty_list_entity_ids_reaches_body() -> None:
    body = report_builder.build_create_payload(
        _template(), content={},
        title="t",
        entity_ids=[],
    )
    assert "entity_ids" in body and body["entity_ids"] == [], (
        f"entity_ids=[] must reach the body verbatim; got {body!r}"
    )


def test_none_entity_ids_omitted_from_body() -> None:
    body = report_builder.build_create_payload(
        _template(), content={},
        title="t",
        entity_ids=None,
    )
    assert "entity_ids" not in body, (
        "entity_ids=None must be OMITTED from the body (leave-unset)."
    )


def test_populated_entity_ids_reach_body() -> None:
    body = report_builder.build_create_payload(
        _template(), content={},
        title="t",
        entity_ids=[101, 202],
    )
    assert body.get("entity_ids") == [101, 202]


# --------------------------------------------------------------------------- #
# 2) report_type_id — _UNSET / None / value tri-state (C7)
# --------------------------------------------------------------------------- #
def test_report_type_id_omitted_when_kwarg_not_supplied() -> None:
    """Default kwarg = _UNSET sentinel → key absent from body."""
    body = report_builder.build_create_payload(
        _template(), content={},
        title="t",
        # report_type_id intentionally NOT passed
    )
    assert "report_type_id" not in body, (
        "report_type_id with no kwarg supplied must NOT appear in the body "
        "(server keeps the current value)."
    )


def test_report_type_id_none_reaches_body_as_null() -> None:
    """Explicit None → body has {"report_type_id": None} so server clears."""
    body = report_builder.build_create_payload(
        _template(), content={},
        title="t",
        report_type_id=None,
    )
    assert "report_type_id" in body, (
        "report_type_id=None must reach the body so the server clears the "
        "field — without the _UNSET sentinel the caller has no way to clear "
        "this tag."
    )
    assert body["report_type_id"] is None, (
        f"expected None in body, got {body['report_type_id']!r}"
    )


def test_report_type_id_int_reaches_body_as_int() -> None:
    body = report_builder.build_create_payload(
        _template(), content={},
        title="t",
        report_type_id=7,
    )
    assert body.get("report_type_id") == 7


# --------------------------------------------------------------------------- #
# 3) closed_at — same _UNSET tri-state (C4)
# --------------------------------------------------------------------------- #
def test_closed_at_omitted_when_kwarg_not_supplied() -> None:
    body = report_builder.build_create_payload(
        _template(), content={},
        title="t",
        # closed_at intentionally NOT passed
    )
    assert "closed_at" not in body, (
        "closed_at with no kwarg supplied must NOT appear in the body."
    )


def test_closed_at_none_reaches_body_as_null() -> None:
    body = report_builder.build_create_payload(
        _template(), content={},
        title="t",
        closed_at=None,
    )
    assert "closed_at" in body, (
        "closed_at=None must reach the body so the server clears the field."
    )
    assert body["closed_at"] is None


def test_closed_at_date_object_serializes_to_iso_string() -> None:
    body = report_builder.build_create_payload(
        _template(), content={},
        title="t",
        closed_at=date(2026, 6, 30),
    )
    assert body.get("closed_at") == "2026-06-30", (
        f"date objects must be serialized as ISO YYYY-MM-DD; "
        f"got {body.get('closed_at')!r}"
    )


def test_closed_at_string_passes_through() -> None:
    body = report_builder.build_create_payload(
        _template(), content={},
        title="t",
        closed_at="2026-06-30",
    )
    assert body.get("closed_at") == "2026-06-30"


# --------------------------------------------------------------------------- #
# 4) Sanity — build_create_payload_multi shares the same semantics
# --------------------------------------------------------------------------- #
def test_multi_builder_shares_unset_semantics() -> None:
    pages = [{
        "template": _template(),
        "content": {},
        "name": "Main",
    }]
    body = report_builder.build_create_payload_multi(
        pages,
        title="t",
        collab_workspace_slugs=[],
        entity_ids=None,
        report_type_id=None,
        closed_at=None,
    )
    # collab_workspace_slugs=[] → reaches body
    assert body.get("collab_workspace_slugs") == []
    # entity_ids=None → omitted
    assert "entity_ids" not in body
    # report_type_id=None → reaches body as null (C7)
    assert "report_type_id" in body and body["report_type_id"] is None
    # closed_at=None → reaches body as null (C4)
    assert "closed_at" in body and body["closed_at"] is None
