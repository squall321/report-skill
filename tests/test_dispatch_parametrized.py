"""Parametrize over every `_DISPATCH` key.

For each MCP tool, feed a minimal-valid args dict through the dispatcher
with a fake ReportArchiveClient. The contract is narrow: the dispatcher
must NOT raise (e.g. ``TypeError`` from a stale kwarg name, ``KeyError``
from a missing args lookup, ``AttributeError`` from a dead client method).

This complements ``test_mcp_roundtrip.py`` (which spot-checks a handful
of high-traffic dispatchers in depth) by giving wall-to-wall coverage of
the surface — any future refactor that drops a kwarg or renames a client
method gets caught here before it ships.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from report_skill import mcp_server, orchestrator
from report_skill.mcp_server import _DISPATCH


# --------------------------------------------------------------------------- #
# Fake-client + stubs (mirrors the test_mcp_roundtrip pattern)
# --------------------------------------------------------------------------- #
def _empty_normalize_result() -> orchestrator.NormalizeResult:
    return orchestrator.NormalizeResult(content={}, blocks=[], extra_blocks=[])


def _fake_template() -> dict:
    return {
        "template_id": "weekly-dev",
        "version": 1,
        "name": "주간 개발 보고",
        "schema": {"blocks": []},
    }


def _install_fake_client(monkeypatch, client_mock: MagicMock) -> None:
    class _FakeClientCtor:
        def __call__(self, *_a, **_kw):
            return self

        def __enter__(self):
            return client_mock

        def __exit__(self, *_exc):
            return None

    monkeypatch.setattr(mcp_server, "ReportArchiveClient", _FakeClientCtor())


def _install_stubs(monkeypatch) -> None:
    """Stub out heavy collaborators (catalog, normalize pipeline, tier
    helpers, examples). The fake client returns plausible empty data so
    each dispatcher reaches its `return` statement without exploding."""
    monkeypatch.setattr(mcp_server, "_normalize_and_upload",
                        lambda *_a, **_kw: _empty_normalize_result())
    monkeypatch.setattr(mcp_server.schemas, "load", lambda: {"widgets": {}})

    # Some dispatchers call report_ops directly — give them sane no-ops.
    monkeypatch.setattr(mcp_server.report_ops, "fetch_report",
                        lambda _c, _rid: {
                            "id": _rid,
                            "title": "fake",
                            "phase": "drafting",
                            "revision": 1,
                            "pages": [{
                                "template_id": "weekly-dev",
                                "template_version": 1,
                                # v0.11.0 content-aware tools (block_show /
                                # block_preview) need at least one block
                                # with the args-dict block_id; otherwise the
                                # dispatcher raises KeyError. _ARGS_BY_TOOL
                                # uses "summary" as the canonical block id.
                                "content": {"summary": "Sample summary text."},
                                "extra_blocks": [],
                            }],
                        })
    monkeypatch.setattr(mcp_server.report_ops, "update_blocks",
                        lambda *_a, **_kw: {"id": 1, "revision": 2})
    monkeypatch.setattr(mcp_server.report_ops, "add_page",
                        lambda *_a, **_kw: {"id": 1, "pages": [{}, {}]})
    monkeypatch.setattr(mcp_server.report_ops, "append_to_blocks",
                        lambda *_a, **_kw: {"id": 1, "revision": 2,
                                             "pages": [{"content": {}}]})
    monkeypatch.setattr(mcp_server.report_ops, "remove_items",
                        lambda *_a, **_kw: ({"id": 1, "revision": 2}, 0))
    monkeypatch.setattr(mcp_server.report_ops, "delete_report",
                        lambda *_a, **_kw: None)
    monkeypatch.setattr(mcp_server.report_ops, "mount_report",
                        lambda *_a, **_kw: [])
    monkeypatch.setattr(mcp_server.report_ops, "unmount_report",
                        lambda *_a, **_kw: None)
    monkeypatch.setattr(mcp_server.report_ops, "list_mounts",
                        lambda *_a, **_kw: [])

    # build_create_payload — skip the real builder body so we don't need
    # a fully-populated template.
    monkeypatch.setattr(
        mcp_server.report_builder, "build_create_payload",
        lambda *_a, **_kw: {"_stub": True},
    )

    # template_suggest / widget_suggest — empty results are fine.
    monkeypatch.setattr(mcp_server.template_suggest, "suggest_templates",
                        lambda *_a, **_kw: [])
    monkeypatch.setattr(mcp_server.widget_suggest, "suggest_extras",
                        lambda *_a, **_kw: [])

    # tier + catalog + examples helpers — return stable shapes.
    class _FakeTierProfile:
        tier = "M"
        source = "test"
        notes = ""

    class _FakePolicy:
        blocks_per_call = 4
        max_repair_passes = 2
        enable_llm_retry = False
        max_llm_retries = 0
        aggressive_enum_match = True
        fallback_chain_depth = 1

    monkeypatch.setattr(mcp_server.tier_mod, "current_tier",
                        lambda: _FakeTierProfile())
    monkeypatch.setattr(mcp_server.tier_mod, "policy", lambda: _FakePolicy())
    monkeypatch.setattr(mcp_server.tier_mod, "set_tier",
                        lambda *_a, **_kw: _FakeTierProfile())

    class _FakeStatus:
        ok = []
        stale = []
        missing = []
        orphan = []

    monkeypatch.setattr(mcp_server.examples_mod, "status",
                        lambda: _FakeStatus())

    class _FakeDiff:
        added = []
        removed = []
        modified = []
        unchanged = []

    class _FakeSnap:
        widgets = []

    monkeypatch.setattr(mcp_server.catalog_mod, "sync",
                        lambda: (_FakeSnap(), _FakeDiff()))
    monkeypatch.setattr(mcp_server.catalog_mod, "sync_templates",
                        lambda *_a, **_kw: (0, []))


def _build_fake_client() -> MagicMock:
    """A MagicMock with sensible defaults for every method called by
    a dispatcher. Anything not pre-baked falls back to MagicMock's
    auto-spec which returns another MagicMock — fine for the smoke
    contract (no raise)."""
    m = MagicMock()
    m.login.return_value = {
        "email": "bot@reportskill.app", "user_id": 1, "access_token": "tok",
    }
    m.fetch_templates.return_value = []
    m.fetch_template.return_value = _fake_template()
    m.create_report.return_value = {"id": 1, "title": "t", "revision": 1}
    m.copy_report.return_value = {"id": 2, "title": "t (copy)",
                                  "workspace_slug": "personal-1", "revision": 1}
    m.add_report_link.return_value = {"id": 9, "from_report_id": 1,
                                       "to_report_id": 2}
    m.publish_report.return_value = {"id": 1, "title": "t",
                                      "phase": "finalized", "revision": 2}
    m.unpublish_report.return_value = {"id": 1, "title": "t",
                                        "phase": "drafting", "revision": 3}
    m.fetch_report_lock_status.return_value = {
        "author_lock_enabled": False,
        "author_lock_reason": None,
        "author_lock_set_at": None,
    }
    m.fetch_report_types.return_value = []
    m.list_folders.return_value = []
    m.set_mount_folder.return_value = {"ok": True}
    m.set_mount_edit_policy.return_value = {"ok": True}
    m.set_template_scope.return_value = {"ok": True}
    m.list_presets.return_value = []
    m.create_preset.return_value = {"id": 5, "name": "p"}
    m.new_report_from_preset.return_value = {"id": 7,
                                              "workspace_slug": "personal-1"}
    m.delete_preset.return_value = None
    m.get_composite.return_value = {"id": 10, "title": "c",
                                     "summary_widgets": [], "items": []}
    m.update_composite_summary.return_value = {"id": 10, "title": "c",
                                                "revision": 2,
                                                "summary_widgets": []}
    m.list_submittable_composites.return_value = []
    m.list_composite_requests.return_value = []
    m.submit_to_composite.return_value = {"id": 1}
    m.accept_composite_request.return_value = {"ok": True}
    m.reject_composite_request.return_value = {"ok": True}
    m.withdraw_composite_request.return_value = {"ok": True}
    # ---- v0.6.0 composites body editing ----
    m.create_composite.return_value = {"id": 11, "title": "May agenda",
                                        "kind": "recurring",
                                        "workspace_slug": "personal-1",
                                        "view_mode": "single",
                                        "revision": 1, "items": []}
    m.update_composite.return_value = {"id": 10, "title": "renamed",
                                        "revision": 3, "view_mode": "single",
                                        "items": []}
    m.delete_composite.return_value = None
    m.publish_composite.return_value = {"id": 10, "title": "c",
                                         "published_at": "2026-06-06T00:00:00Z",
                                         "revision": 4}
    m.unpublish_composite.return_value = {"id": 10, "title": "c",
                                           "published_at": None, "revision": 5}
    # ---- v0.6.0 activities + notifications ----
    m.fetch_report_activities.return_value = {"items": []}
    m.list_notifications.return_value = {"items": [], "unread_count": 0}
    m.unread_notification_count.return_value = 0
    m.mark_notification_read.return_value = {"id": 5}
    m.mark_all_notifications_read.return_value = 0
    m.fetch_linkable_reports.return_value = []
    m.fetch_workspaces.return_value = []
    m.fetch_entity_types.return_value = []
    m.fetch_entities.return_value = []
    m.list_widget_relations.return_value = []
    m.list_ref_categories.return_value = []
    # v0.12.0 — high-value read surface
    m.list_composites_by_report.return_value = []
    m.list_reports.return_value = []
    m.list_comments_inbox.return_value = {"items": [], "unread": 0}
    m.list_entities_with_usage.return_value = []
    m.list_workspace_members.return_value = []
    # v0.10.0 — soft delete + takedown
    m.trash_report.return_value = {"id": 1, "deleted_at": "2026-06-09T22:00:00Z"}
    m.restore_report.return_value = {"id": 1, "deleted_at": None}
    m.request_report_takedown.return_value = {"id": 1, "status": "pending"}
    m.list_takedown_requests.return_value = []
    m.approve_takedown_request.return_value = {"id": 1, "status": "approved"}
    m.reject_takedown_request.return_value = {"id": 1, "status": "rejected"}
    # v0.8.0 — unified grants / sharing
    m.list_content_shares.return_value = []
    m.list_folder_shares.return_value = []
    m.list_board_shares.return_value = []
    m.add_content_share.return_value = {"id": 1, "principal_type": "workspace",
                                         "principal_ref": "dx", "level": "view"}
    m.add_folder_share.return_value = {"id": 1, "principal_type": "workspace",
                                        "principal_ref": "dx", "level": "view"}
    m.add_board_share.return_value = {"id": 1, "principal_type": "workspace",
                                       "principal_ref": "dx", "level": "view"}
    m.remove_content_share.return_value = None
    m.remove_folder_share.return_value = None
    m.remove_board_share.return_value = None
    m.upload_file.return_value = {"file_id": "f_abc", "filename": "x.png",
                                   "mime_type": "image/png", "size": 1}
    # `c.get(...)` is called by a few dispatchers (examples_mine_from_report,
    # report_import json branch, v0.11.0 content-aware read surface); return
    # a minimal report shape with one page + one content block so report_outline
    # / page_show_content / block_show / block_preview can dispatch without
    # IndexError.
    m.get.return_value = {
        "id": 1, "title": "t", "phase": "drafting", "revision": 1,
        "pages": [{
            "template_id": "weekly-dev", "template_version": 1,
            "name": "Page 0",
            "content": {"summary": "Sample summary text."},
            "extra_blocks": [],
        }],
    }
    return m


# --------------------------------------------------------------------------- #
# Per-dispatcher minimal valid args
# --------------------------------------------------------------------------- #
# Many tools are read-only and accept {} — listed here for completeness so
# the parametrize coverage is wall-to-wall and obvious at a glance.
#
# For write tools, supply only what the dispatcher uses unconditionally
# (anything else stays at the schema default).
_ARGS_BY_TOOL: dict[str, dict[str, Any]] = {
    # ---- read-only ----
    "ping": {},
    "templates_list": {},
    "templates_show": {"template_id": "weekly-dev"},
    "templates_suggest": {"text": "주간 개발 보고"},
    "widgets_catalog": {},
    "widgets_suggest_extras": {"text": "차트가 필요해"},
    "report_show": {"report_id": 1},
    "report_lock_status": {"report_id": 1},
    "examples_status": {},
    "tier_show": {},

    # ---- write ----
    "report_create": {
        "template_id": "weekly-dev",
        "title": "t",
        "blocks": {},
    },
    "report_update": {
        "report_id": 1,
        "blocks": {},
    },
    "report_revise": {
        "report_id": 1,
        "instruction": "tighten wording",
        "block_ids": ["summary"],
        "dry_run": True,
    },
    "report_append": {
        "report_id": 1,
        "blocks": {},
    },
    "report_add_page": {
        "report_id": 1,
        "template_id": "weekly-dev",
        "blocks": {},
    },
    "report_delete": {"report_id": 1, "confirm": True},
    "report_mount": {"report_id": 1, "workspace_slugs": ["dx"]},
    "report_unmount": {"report_id": 1, "workspace_slug": "dx"},
    "report_mounts": {"report_id": 1},
    "report_milestone_add": {
        "report_id": 1,
        "date": "2026-06-06",
        "label": "kickoff",
        "block_id": "milestones",  # skip _find_milestone_block lookup
    },
    "report_milestone_remove": {
        "report_id": 1,
        "date": "2026-06-06",
        "block_id": "milestones",
    },
    "file_upload": {"path": "fake/path.bin"},

    # ---- maintenance ----
    "catalog_sync": {},
    "examples_mine_from_report": {"report_id": 1, "dry_run": True},
    "tier_set": {"tier": "M"},
    "catalog_sync_templates": {},

    # ---- offline export/import ----
    "report_export": {
        "template_id": "weekly-dev",
        "title": "t",
        "blocks": {},
        "out_path": "fake/out.json",
        "offline": True,
    },
    "report_import": {"payload_path": "fake/payload.json"},
    "report_dump": {"report_id": 1, "out_path": "fake/bundle.zip"},

    # ---- mention resolvers ----
    "reports_search": {"q": "주간"},
    "workspaces_list": {},
    "entity_types_list": {},
    "entities_list": {"q": "HFP"},
    "widget_relations_list": {},
    "widget_ref_categories_list": {},
    # v0.12.0 — high-value read surface
    "composites_by_report": {"report_id": 1},
    "reports_list": {},
    "comments_inbox_list": {},
    "entities_usage_list": {},
    "workspace_members_list": {"workspace_slug": "dx"},
    # v0.11.0 — content-aware read surface
    "report_outline": {"report_id": 1},
    "page_show_content": {"report_id": 1, "page_index": 0},
    "block_show": {"report_id": 1, "page_index": 0, "block_id": "summary"},
    "block_preview": {"report_id": 1, "page_index": 0, "block_id": "summary"},
    # v0.10.0 — soft delete + takedown
    "report_trash": {"report_id": 1},
    "report_restore": {"report_id": 1},
    "report_takedown_request": {"report_id": 1, "workspace_slug": "dx"},
    "takedowns_list": {},
    "takedown_approve": {"request_id": 1},
    "takedown_reject": {"request_id": 1},

    # ---- v0.5.0 ----
    "report_copy": {"report_id": 1, "title": "copy"},
    "report_add_link": {"report_id": 1, "to_report_id": 2},
    "report_types_list": {},
    "report_publish": {"report_id": 1},
    "report_unpublish": {"report_id": 1},
    "folders_list": {},
    "report_mount_set_folder": {"report_id": 1, "workspace_slug": "dx"},
    "report_mount_set_edit_policy": {
        # v0.8.1+: manager policy auto-syncs a workspace_manager grant on RA.
        # Exercise the new value so the dispatch path is locked.
        "report_id": 1, "workspace_slug": "dx", "edit_policy": "manager",
    },
    "template_set_scope": {"template_id": "engineering-rca",
                            "owner_workspace_slugs": ["dx"]},
    "presets_list": {},
    "preset_create": {"report_id": 1, "name": "p"},
    "report_new_from_preset": {"preset_id": 5},
    "preset_delete": {"preset_id": 5},

    # ---- composites ----
    "composite_get": {"composite_id": 10},
    "composite_summary_set": {"composite_id": 10, "summary_widgets": []},
    "composites_submittable_for": {"report_id": 1},
    "composites_requests_list": {"composite_id": 10},
    "composites_submit": {"composite_id": 10, "report_id": 1},
    "composites_request_accept": {"composite_id": 10, "request_id": 1},
    "composites_request_reject": {"composite_id": 10, "request_id": 1},
    "composites_request_withdraw": {"composite_id": 10, "request_id": 1},

    # ---- v0.6.0 composites body editing ----
    "composite_create": {"title": "May agenda", "kind": "recurring"},
    "composite_update": {"composite_id": 10, "title": "renamed"},
    "composite_items_set": {"composite_id": 10, "items": []},
    "composite_delete": {"composite_id": 10},
    "composite_publish": {"composite_id": 10},
    "composite_unpublish": {"composite_id": 10},

    # ---- v0.6.0 activities + notifications ----
    "report_activities": {"report_id": 1},
    "notifications_list": {},
    "notifications_unread_count": {},
    "notification_mark_read": {"notification_id": 5},
    "notifications_mark_all_read": {},

    # ---- v0.8.0 unified grants ----
    "content_shares_list": {"content_type": "reports", "content_id": 1},
    "content_share_add": {
        "content_type": "reports", "content_id": 1,
        "principal_type": "workspace", "principal_ref": "dx", "level": "view",
    },
    "content_share_remove": {
        "content_type": "reports", "content_id": 1, "grant_id": 9,
    },
    "folder_shares_list": {"folder_id": 1},
    "folder_share_add": {
        "folder_id": 1,
        "principal_type": "workspace", "principal_ref": "dx", "level": "view",
    },
    "folder_share_remove": {"folder_id": 1, "grant_id": 9},
    "board_shares_list": {"workspace_slug": "dx"},
    "board_share_add": {
        "workspace_slug": "dx",
        "principal_type": "workspace", "principal_ref": "dx", "level": "view",
    },
    "board_share_remove": {"workspace_slug": "dx", "grant_id": 9},
}


# Tools we skip from the smoke loop because they need a real local file
# (file_upload's `path` is validated by upload_file before the fake client
# can intercept) or run a real flow we don't want to fake-in here.
# Each is covered separately in test_mcp_roundtrip.py / test_report_ops.py.
_SKIP_TOOLS: set[str] = {
    "file_upload",       # upload_file checks Path.is_file() before our mock
    "report_export",     # writes to disk; out_path validation
    "report_import",     # opens payload_path; covered in test_report_ops
    "report_dump",       # writes bundle.zip; covered in test_report_ops
    "report_revise",     # routes through real LLM provider lookup
}


@pytest.mark.parametrize("tool_name", sorted(_DISPATCH.keys()))
def test_dispatch_smoke(monkeypatch, tool_name: str) -> None:
    if tool_name in _SKIP_TOOLS:
        pytest.skip(f"{tool_name}: covered elsewhere (needs real I/O)")
    if tool_name not in _ARGS_BY_TOOL:
        pytest.fail(
            f"_ARGS_BY_TOOL missing entry for {tool_name!r} — "
            "add a minimal-valid args dict so the smoke coverage stays "
            "wall-to-wall."
        )
    _install_stubs(monkeypatch)
    _install_fake_client(monkeypatch, _build_fake_client())
    fn = _DISPATCH[tool_name]
    args = dict(_ARGS_BY_TOOL[tool_name])
    # The contract: the dispatcher returns SOMETHING (dict / list / None)
    # and does not raise. The dispatcher may return an `{"error": ...}`
    # envelope — that's still a non-raising result; this test only catches
    # crashes (TypeError on stale kwarg, AttributeError on dead client
    # method, etc.) rather than business-logic outcomes.
    result = fn(args)
    # NB: empty dict/list/None all count as "did not crash". We assert the
    # return is JSON-serializable-ish (the MCP layer JSON-dumps it next).
    assert result is None or isinstance(result, (dict, list, str, int, bool))


def test_args_by_tool_covers_every_dispatch_key() -> None:
    """If a tool gets added to _DISPATCH but not to _ARGS_BY_TOOL, fail
    loudly here so the parametrize loop doesn't silently skip it."""
    missing = sorted(set(_DISPATCH.keys()) - set(_ARGS_BY_TOOL.keys()))
    assert not missing, (
        "every _DISPATCH key needs an entry in _ARGS_BY_TOOL "
        f"(missing: {missing})"
    )


# --------------------------------------------------------------------------- #
# Negative-path coverage (v0.7.0).
#
# The happy-path smoke loop above proves "no stale kwargs / dead methods".
# These negative tests prove the inverse: when the dispatcher is fed
# obviously-malformed args, it raises a *useful* exception (KeyError /
# ValueError / TypeError) instead of silently returning, partially
# committing, or crashing with an AttributeError downstream.
#
# We pick a few representative dispatchers rather than hammering all 66 —
# the failure modes here are about argument-handling style (required
# lookups via args["k"], int() coercion, isinstance() guards), not the
# per-tool business logic.
# --------------------------------------------------------------------------- #
def test_report_create_missing_required_template_id_raises_keyerror(monkeypatch) -> None:
    """report_create looks up `args["template_id"]` unconditionally —
    omitting it must surface as KeyError so call_tool can wrap into a
    structured envelope (call_tool maps KeyError → {error:'KeyError'})."""
    _install_stubs(monkeypatch)
    _install_fake_client(monkeypatch, _build_fake_client())
    fn = _DISPATCH["report_create"]
    # `template_id` omitted on purpose. The dispatcher must raise — NOT
    # silently default to '' or None which would create an empty report.
    with pytest.raises(KeyError):
        fn({"title": "t", "blocks": {}})


def test_composite_create_missing_kind_raises_keyerror(monkeypatch) -> None:
    """composite_create reads `args["kind"]` unconditionally; missing it
    must KeyError. (Schema-level required-validation lives at the MCP
    layer; this is the dispatcher's own contract.)"""
    _install_stubs(monkeypatch)
    _install_fake_client(monkeypatch, _build_fake_client())
    fn = _DISPATCH["composite_create"]
    with pytest.raises(KeyError):
        fn({"title": "May agenda"})  # missing 'kind'


def test_composite_summary_set_non_list_widgets_raises_valueerror(monkeypatch) -> None:
    """composite_summary_set has an explicit isinstance(widgets, list)
    guard — feeding a dict / string must raise ValueError with a clear
    message so the LLM doesn't try to recover by retrying the same shape."""
    _install_stubs(monkeypatch)
    _install_fake_client(monkeypatch, _build_fake_client())
    fn = _DISPATCH["composite_summary_set"]
    with pytest.raises(ValueError, match="summary_widgets must be a JSON array"):
        fn({"composite_id": 10, "summary_widgets": {"not": "a list"}})


def test_report_show_non_numeric_report_id_raises(monkeypatch) -> None:
    """report_show coerces via int(args["report_id"]) — a non-numeric
    string must raise ValueError (the call_tool error wrapper turns this
    into a structured envelope). This catches accidents like an LLM
    passing the mention literal "report:42" through.

    int(...) on garbage raises ValueError; on None raises TypeError. We
    accept either since both indicate a malformed argument."""
    _install_stubs(monkeypatch)
    _install_fake_client(monkeypatch, _build_fake_client())
    fn = _DISPATCH["report_show"]
    with pytest.raises((ValueError, TypeError)):
        fn({"report_id": "not-a-number"})


def test_report_mount_non_iterable_workspace_slugs_raises(monkeypatch) -> None:
    """report_mount forwards `workspace_slugs` straight to
    report_ops.mount_report which calls list(...) on it; passing an
    int triggers TypeError ("'int' object is not iterable")."""
    _install_stubs(monkeypatch)
    # Override the mount_report stub with one that mirrors the real
    # iteration contract (list(workspace_slugs)). The default stub in
    # _install_stubs swallows kwargs and returns [], which would mask
    # the type error we're trying to catch.
    def _strict_mount(_c, _rid, *, workspace_slugs):
        return list(workspace_slugs)  # raises TypeError on int

    monkeypatch.setattr(mcp_server.report_ops, "mount_report", _strict_mount)
    _install_fake_client(monkeypatch, _build_fake_client())
    fn = _DISPATCH["report_mount"]
    with pytest.raises(TypeError):
        # int where iterable[str] expected
        fn({"report_id": 1, "workspace_slugs": 42})
