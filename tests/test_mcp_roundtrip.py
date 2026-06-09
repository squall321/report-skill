"""MCP-layer roundtrip tests — closes the verify gap from v0.5.0.

The v0.5.0 verify phase passed pytest but missed five regressions where the
MCP schema, dispatcher and underlying client were out of sync. These tests
exercise the `_DISPATCH` callable directly with a fully-stubbed
ReportArchiveClient so that future builder / MCP-schema asymmetries surface
as test failures rather than runtime errors at the MCP boundary.

Every test patches:
  * ``report_skill.mcp_server.ReportArchiveClient`` so no network is used
  * ``report_skill.mcp_server.schemas.load`` so no widget catalog is needed
  * Whichever downstream callable carries the kwargs of interest
    (``report_builder.build_create_payload`` for create,
    ``report_ops.update_blocks`` for update, ...) so the test can assert
    the exact pass-through.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from report_skill import mcp_server, orchestrator
from report_skill.mcp_server import _DISPATCH


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _empty_normalize_result() -> orchestrator.NormalizeResult:
    """A NormalizeResult with no blocks — bypasses widget-snapshot logic."""
    return orchestrator.NormalizeResult(content={}, blocks=[], extra_blocks=[])


def _fake_template() -> dict:
    """Minimal template dict accepted by the builder."""
    return {
        "template_id": "weekly-dev",
        "version": 1,
        "name": "주간 개발 보고",
        "schema": {"blocks": []},
    }


def _install_fake_client(monkeypatch, client_mock: MagicMock) -> None:
    """Replace ``mcp_server.ReportArchiveClient`` with a class that, when
    called as ``ReportArchiveClient()`` in a ``with`` block, yields the
    provided MagicMock — matching the dispatcher's context-manager usage.
    """
    class _FakeClientCtor:
        def __call__(self, *_a, **_kw):
            return self

        def __enter__(self):
            return client_mock

        def __exit__(self, *_exc):
            return None

    monkeypatch.setattr(mcp_server, "ReportArchiveClient", _FakeClientCtor())


def _install_stub_normalize(monkeypatch) -> None:
    """Skip the real normalize+upload pipeline (no widget catalog needed)."""
    monkeypatch.setattr(
        mcp_server, "_normalize_and_upload",
        lambda *_a, **_kw: _empty_normalize_result(),
    )
    monkeypatch.setattr(mcp_server.schemas, "load", lambda: {})


# The 13 fields the v0.5.0 ReportCreate / ReportUpdate schemas accept but
# the MCP layer was silently dropping. Each test below asserts every entry
# appears verbatim in the captured downstream call.
THIRTEEN_FIELDS = {
    # related-info (3)
    "collab_workspace_slugs": ["dx", "platform"],
    "entity_ids": [101, 202],
    "report_type_id": 7,
    # page-level (10)
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
}


# --------------------------------------------------------------------------- #
# 1. report_create — 13 fields propagate to build_create_payload
# --------------------------------------------------------------------------- #
def test_report_create_dispatch_propagates_13_fields(monkeypatch):
    _install_stub_normalize(monkeypatch)

    client_mock = MagicMock()
    client_mock.fetch_template.return_value = _fake_template()
    client_mock.create_report.return_value = {
        "id": 1, "title": "t", "revision": 1,
    }
    _install_fake_client(monkeypatch, client_mock)

    captured: dict[str, Any] = {}

    def fake_build(template, content, **kwargs):
        captured.update(kwargs)
        return {"_stub": True}

    monkeypatch.setattr(
        mcp_server.report_builder, "build_create_payload", fake_build,
    )

    args = {
        "template_id": "weekly-dev",
        "title": "v0.5.1 roundtrip",
        "blocks": {},
        **THIRTEEN_FIELDS,
    }
    _DISPATCH["report_create"](args)

    for key, expected in THIRTEEN_FIELDS.items():
        assert key in captured, f"build_create_payload missing kwarg {key!r}"
        assert captured[key] == expected, (
            f"build_create_payload kwarg {key!r}: "
            f"expected {expected!r}, got {captured[key]!r}"
        )


# --------------------------------------------------------------------------- #
# 2. report_update — 13 fields propagate to report_ops.update_blocks
# --------------------------------------------------------------------------- #
def test_report_update_dispatch_propagates_13_fields(monkeypatch):
    _install_stub_normalize(monkeypatch)

    fake_existing = {
        "id": 42,
        "revision": 1,
        "pages": [{
            "template_id": "weekly-dev",
            "template_version": 1,
            "content": {},
            "extra_blocks": [],
        }],
    }

    client_mock = MagicMock()
    client_mock.fetch_template.return_value = _fake_template()
    _install_fake_client(monkeypatch, client_mock)

    monkeypatch.setattr(
        mcp_server.report_ops, "fetch_report",
        lambda _c, _rid: fake_existing,
    )

    captured: dict[str, Any] = {}

    def fake_update_blocks(_client, _report_id, **kwargs):
        captured.update(kwargs)
        return {"id": 42, "revision": 2}

    monkeypatch.setattr(
        mcp_server.report_ops, "update_blocks", fake_update_blocks,
    )

    args = {
        "report_id": 42,
        "blocks": {},
        **THIRTEEN_FIELDS,
    }
    _DISPATCH["report_update"](args)

    for key, expected in THIRTEEN_FIELDS.items():
        assert key in captured, f"update_blocks missing kwarg {key!r}"
        assert captured[key] == expected, (
            f"update_blocks kwarg {key!r}: "
            f"expected {expected!r}, got {captured[key]!r}"
        )


# --------------------------------------------------------------------------- #
# 3. template_set_scope — accepts a string slug (no more int() crash)
# --------------------------------------------------------------------------- #
def test_template_set_scope_accepts_string_slug(monkeypatch):
    client_mock = MagicMock()
    client_mock.set_template_scope.return_value = {"ok": True}
    _install_fake_client(monkeypatch, client_mock)

    # Should NOT raise ValueError on int("engineering-rca").
    _DISPATCH["template_set_scope"]({
        "template_id": "engineering-rca",
        "owner_workspace_slugs": ["dx"],
    })

    # The client was called with the slug verbatim — not coerced through int().
    client_mock.set_template_scope.assert_called_once()
    call = client_mock.set_template_scope.call_args
    assert call.args[0] == "engineering-rca"
    assert call.kwargs.get("owner_workspace_slugs") == ["dx"]


# --------------------------------------------------------------------------- #
# 4. composites_requests_list — status_filter kwarg accepted (no TypeError)
# --------------------------------------------------------------------------- #
def test_list_composite_requests_accepts_status_filter(monkeypatch):
    client_mock = MagicMock()
    client_mock.list_composite_requests.return_value = []
    _install_fake_client(monkeypatch, client_mock)

    _DISPATCH["composites_requests_list"]({
        "composite_id": 1,
        "status_filter": "pending",
    })

    client_mock.list_composite_requests.assert_called_once()
    call = client_mock.list_composite_requests.call_args
    assert call.args[0] == 1
    assert call.kwargs.get("status_filter") == "pending"


# --------------------------------------------------------------------------- #
# 5. report_add_link — direction kwarg propagates
# --------------------------------------------------------------------------- #
def test_add_report_link_accepts_direction(monkeypatch):
    client_mock = MagicMock()
    client_mock.add_report_link.return_value = {"id": 9}
    _install_fake_client(monkeypatch, client_mock)

    _DISPATCH["report_add_link"]({
        "report_id": 1,
        "to_report_id": 2,
        "direction": "incoming",
    })

    client_mock.add_report_link.assert_called_once()
    call = client_mock.add_report_link.call_args
    assert call.args[0] == 1
    assert call.kwargs.get("to_report_id") == 2
    assert call.kwargs.get("direction") == "incoming"


# --------------------------------------------------------------------------- #
# 6. _DISPATCH size — locks the surface so an accidental rename / drop fails
# --------------------------------------------------------------------------- #
def test_dispatch_count_is_91():
    # v0.5.0 shipped 53 tools; v0.5.1 adds `report_lock_status` → 54;
    # v0.6.0 adds 6 composites body editing + 1 activities + 4 notifications → 65;
    # v0.7.0 adds `widget_relations_list` → 66;
    # v0.8.0 adds 9 unified-grants tools → 75;
    # v0.9.0 adds `widget_ref_categories_list` (RA 074233d) → 76;
    # v0.10.0 adds 2 soft-delete + 4 takedown-queue tools → 82;
    # v0.11.0 adds 4 content-aware read tools → 86;
    # v0.12.0 adds 5 high-value read tools (composites_by_report, reports_list,
    # comments_inbox_list, entities_usage_list, workspace_members_list) → 91.
    assert len(_DISPATCH) == 91, sorted(_DISPATCH)


# --------------------------------------------------------------------------- #
# 7. report_lock_status — new tool wires through to fetch_report_lock_status
# --------------------------------------------------------------------------- #
def test_report_lock_status_dispatch(monkeypatch):
    client_mock = MagicMock()
    client_mock.fetch_report_lock_status.return_value = {
        "author_lock_enabled": True,
        "author_lock_reason": "검토 중",
        "author_lock_set_at": "2026-06-06T00:00:00Z",
    }
    _install_fake_client(monkeypatch, client_mock)

    out = _DISPATCH["report_lock_status"]({"report_id": 1})

    client_mock.fetch_report_lock_status.assert_called_once_with(1)
    assert out["author_lock_enabled"] is True
    assert out["author_lock_reason"] == "검토 중"


# --------------------------------------------------------------------------- #
# v0.6.0 — composites body editing dispatchers (6 tools)
# --------------------------------------------------------------------------- #
def test_composite_create_dispatch(monkeypatch):
    client_mock = MagicMock()
    client_mock.create_composite.return_value = {
        "id": 99, "title": "May agenda", "kind": "recurring",
        "workspace_slug": "personal-1", "view_mode": "single",
        "revision": 1, "items": [],
    }
    _install_fake_client(monkeypatch, client_mock)

    out = _DISPATCH["composite_create"]({
        "title": "May agenda",
        "kind": "recurring",
        "view_mode": "single",
        "period_date": "2026-05-01",
        "description": "5월 종합",
        "items": [{"ref_report_id": 1, "note": ""}],
    })

    client_mock.create_composite.assert_called_once()
    kwargs = client_mock.create_composite.call_args.kwargs
    assert kwargs["title"] == "May agenda"
    assert kwargs["kind"] == "recurring"
    assert kwargs["view_mode"] == "single"
    assert kwargs["period_date"] == "2026-05-01"
    assert kwargs["description"] == "5월 종합"
    assert kwargs["items"] == [{"ref_report_id": 1, "note": ""}]
    assert out["id"] == 99


def test_composite_update_dispatch(monkeypatch):
    client_mock = MagicMock()
    client_mock.update_composite.return_value = {
        "id": 10, "title": "renamed", "revision": 7,
        "view_mode": "two_col", "items": [],
    }
    _install_fake_client(monkeypatch, client_mock)

    out = _DISPATCH["composite_update"]({
        "composite_id": 10,
        "title": "renamed",
        "view_mode": "two_col",
        "description": "updated",
        "expected_revision": 6,
    })

    client_mock.update_composite.assert_called_once()
    call = client_mock.update_composite.call_args
    assert call.args[0] == 10
    kwargs = call.kwargs
    assert kwargs["title"] == "renamed"
    assert kwargs["view_mode"] == "two_col"
    assert kwargs["description"] == "updated"
    assert kwargs["expected_revision"] == 6
    # tri-state — fields not supplied must NOT appear
    assert "period_date" not in kwargs
    assert "group_name" not in kwargs
    assert out["revision"] == 7


def test_composite_update_clears_period_date_when_explicit_null(monkeypatch):
    """Tri-state semantics — `period_date: null` reaches the body (clear)
    while omitting the key leaves it alone."""
    client_mock = MagicMock()
    client_mock.update_composite.return_value = {"id": 10, "revision": 8}
    _install_fake_client(monkeypatch, client_mock)

    _DISPATCH["composite_update"]({
        "composite_id": 10,
        "period_date": None,   # explicit null
    })

    kwargs = client_mock.update_composite.call_args.kwargs
    assert "period_date" in kwargs, (
        "period_date=None must reach the client kwargs so the server clears it"
    )
    assert kwargs["period_date"] is None


def test_composite_items_set_dispatch(monkeypatch):
    client_mock = MagicMock()
    client_mock.update_composite.return_value = {
        "id": 10, "title": "c", "revision": 9, "items": [{"id": 1}, {"id": 2}],
    }
    _install_fake_client(monkeypatch, client_mock)

    items = [
        {"ref_report_id": 100, "note": "first"},
        {"ref_composite_id": 50, "note": "nested", "display_column": 2},
    ]
    out = _DISPATCH["composite_items_set"]({
        "composite_id": 10,
        "items": items,
        "expected_revision": 8,
    })

    kwargs = client_mock.update_composite.call_args.kwargs
    assert kwargs["items"] == items
    assert kwargs["expected_revision"] == 8
    assert out["item_count"] == 2


def test_composite_delete_dispatch(monkeypatch):
    client_mock = MagicMock()
    client_mock.delete_composite.return_value = None
    _install_fake_client(monkeypatch, client_mock)

    out = _DISPATCH["composite_delete"]({"composite_id": 10})

    client_mock.delete_composite.assert_called_once_with(10)
    assert out == {"deleted": True, "id": 10}


def test_composite_publish_dispatch(monkeypatch):
    client_mock = MagicMock()
    client_mock.publish_composite.return_value = {
        "id": 10, "title": "c", "published_at": "2026-06-06T00:00:00Z",
        "revision": 11,
    }
    _install_fake_client(monkeypatch, client_mock)

    out = _DISPATCH["composite_publish"]({"composite_id": 10})

    client_mock.publish_composite.assert_called_once_with(10)
    assert out["published_at"] == "2026-06-06T00:00:00Z"


def test_composite_unpublish_dispatch(monkeypatch):
    client_mock = MagicMock()
    client_mock.unpublish_composite.return_value = {
        "id": 10, "title": "c", "published_at": None, "revision": 12,
    }
    _install_fake_client(monkeypatch, client_mock)

    out = _DISPATCH["composite_unpublish"]({"composite_id": 10})

    client_mock.unpublish_composite.assert_called_once_with(10)
    assert out["published_at"] is None


# --------------------------------------------------------------------------- #
# v0.6.0 — activities + notifications dispatchers (5 tools)
# --------------------------------------------------------------------------- #
def test_report_activities_dispatch(monkeypatch):
    client_mock = MagicMock()
    client_mock.fetch_report_activities.return_value = {
        "items": [
            {"id": 3, "type": "publish", "created_at": "2026-06-06T01:00:00Z"},
            {"id": 2, "type": "locked", "created_at": "2026-06-06T00:30:00Z"},
            {"id": 1, "type": "created", "created_at": "2026-06-06T00:00:00Z"},
        ],
    }
    _install_fake_client(monkeypatch, client_mock)

    out = _DISPATCH["report_activities"]({
        "report_id": 42, "limit": 20,
    })

    client_mock.fetch_report_activities.assert_called_once()
    call = client_mock.fetch_report_activities.call_args
    assert call.args[0] == 42
    assert call.kwargs["limit"] == 20
    assert call.kwargs["before_id"] is None
    assert out["count"] == 3
    assert len(out["items"]) == 3


def test_report_activities_pagination_cursor(monkeypatch):
    client_mock = MagicMock()
    client_mock.fetch_report_activities.return_value = {"items": []}
    _install_fake_client(monkeypatch, client_mock)

    _DISPATCH["report_activities"]({
        "report_id": 1, "limit": 50, "before_id": 100,
    })
    kwargs = client_mock.fetch_report_activities.call_args.kwargs
    assert kwargs["before_id"] == 100
    assert kwargs["limit"] == 50


def test_notifications_list_dispatch(monkeypatch):
    client_mock = MagicMock()
    client_mock.list_notifications.return_value = {
        "items": [{"id": 1, "type": "report_published"}],
        "unread_count": 7,
    }
    _install_fake_client(monkeypatch, client_mock)

    out = _DISPATCH["notifications_list"]({
        "unread_only": True, "limit": 25,
    })

    client_mock.list_notifications.assert_called_once()
    kwargs = client_mock.list_notifications.call_args.kwargs
    assert kwargs["unread_only"] is True
    assert kwargs["limit"] == 25
    assert kwargs["before_id"] is None
    assert out["count"] == 1
    assert out["unread_count"] == 7


def test_notifications_unread_count_dispatch(monkeypatch):
    client_mock = MagicMock()
    client_mock.unread_notification_count.return_value = 12
    _install_fake_client(monkeypatch, client_mock)

    out = _DISPATCH["notifications_unread_count"]({})

    client_mock.unread_notification_count.assert_called_once_with()
    assert out == {"unread_count": 12}


def test_notification_mark_read_dispatch(monkeypatch):
    client_mock = MagicMock()
    client_mock.mark_notification_read.return_value = {"id": 99}
    _install_fake_client(monkeypatch, client_mock)

    out = _DISPATCH["notification_mark_read"]({"notification_id": 99})

    client_mock.mark_notification_read.assert_called_once_with(99)
    assert out["id"] == 99
    assert out["marked_read"] is True


def test_notifications_mark_all_read_dispatch(monkeypatch):
    client_mock = MagicMock()
    client_mock.mark_all_notifications_read.return_value = 42
    _install_fake_client(monkeypatch, client_mock)

    out = _DISPATCH["notifications_mark_all_read"]({})

    client_mock.mark_all_notifications_read.assert_called_once_with()
    assert out == {"marked_read": 42}
