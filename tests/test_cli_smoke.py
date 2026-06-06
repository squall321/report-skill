"""CLI smoke — every v0.6.0 CLI command must parse + dispatch.

Uses ``typer.testing.CliRunner`` so we don't actually shell out. Each
test patches ``ReportArchiveClient`` at the module level so no network
is used; we only assert the CLI parsed the arguments correctly, called
the right client method with the right kwargs, and exited 0.

Covers:
  * report activities <id> [--limit N] [--before-id N]
  * composites create / update / items-set / delete / publish / unpublish
  * notifications list / unread-count / mark-read / mark-all-read

Each ``--help`` invocation also gets a smoke probe so help text rendering
regressions surface here rather than at user-facing CLI time.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from report_skill import cli as cli_mod


runner = CliRunner()


# --------------------------------------------------------------------------- #
# Shared fake-client helper (mirrors test_mcp_roundtrip pattern)
# --------------------------------------------------------------------------- #
def _install_fake_client(monkeypatch, client_mock: MagicMock) -> None:
    """Replace ``cli.ReportArchiveClient`` so the CLI's ``with`` block
    yields our mock instead of opening a real httpx session."""

    class _FakeCtor:
        def __call__(self, *_a, **_kw):
            return self

        def __enter__(self):
            return client_mock

        def __exit__(self, *_exc):
            return None

    monkeypatch.setattr(cli_mod, "ReportArchiveClient", _FakeCtor())


# --------------------------------------------------------------------------- #
# --help — every new command renders without error
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("argv", [
    ["report", "activities", "--help"],
    ["composites", "create", "--help"],
    ["composites", "update", "--help"],
    ["composites", "items-set", "--help"],
    ["composites", "delete", "--help"],
    ["composites", "publish", "--help"],
    ["composites", "unpublish", "--help"],
    ["notifications", "list", "--help"],
    ["notifications", "unread-count", "--help"],
    ["notifications", "mark-read", "--help"],
    ["notifications", "mark-all-read", "--help"],
])
def test_help_renders(argv):
    result = runner.invoke(cli_mod.app, argv)
    assert result.exit_code == 0, (
        f"{' '.join(argv)} exited {result.exit_code}: {result.output}"
    )
    assert "Usage:" in result.output


# --------------------------------------------------------------------------- #
# report activities
# --------------------------------------------------------------------------- #
def test_report_activities_invokes_client(monkeypatch):
    client_mock = MagicMock()
    client_mock.fetch_report_activities.return_value = {
        "items": [{"id": 1, "type": "publish"}],
    }
    _install_fake_client(monkeypatch, client_mock)

    result = runner.invoke(cli_mod.app, [
        "report", "activities", "42",
        "--limit", "10", "--before-id", "100",
    ])
    assert result.exit_code == 0, result.output
    client_mock.fetch_report_activities.assert_called_once()
    call = client_mock.fetch_report_activities.call_args
    assert call.args[0] == 42
    assert call.kwargs == {"limit": 10, "before_id": 100}


# --------------------------------------------------------------------------- #
# composites create / update / items-set
# --------------------------------------------------------------------------- #
def test_composites_create_invokes_client(monkeypatch):
    client_mock = MagicMock()
    client_mock.create_composite.return_value = {
        "id": 7, "title": "May agenda", "kind": "recurring",
    }
    _install_fake_client(monkeypatch, client_mock)

    result = runner.invoke(cli_mod.app, [
        "composites", "create",
        "--title", "May agenda",
        "--kind", "recurring",
        "--view-mode", "single",
        "--period-date", "2026-05-01",
        "--workspace", "personal-1",
    ])
    assert result.exit_code == 0, result.output
    client_mock.create_composite.assert_called_once()
    kwargs = client_mock.create_composite.call_args.kwargs
    assert kwargs["title"] == "May agenda"
    assert kwargs["kind"] == "recurring"
    assert kwargs["view_mode"] == "single"
    assert kwargs["period_date"] == "2026-05-01"
    assert kwargs["workspace_slug"] == "personal-1"


def test_composites_create_seeds_items_from_file(monkeypatch, tmp_path: Path):
    """`--items-file` accepts either a bare list or {items: [...]}; both
    forms must reach the client kwargs verbatim."""
    items_file = tmp_path / "items.json"
    items_file.write_text(json.dumps([
        {"ref_report_id": 1, "note": "a"},
        {"ref_report_id": 2, "note": "b"},
    ]), encoding="utf-8")

    client_mock = MagicMock()
    client_mock.create_composite.return_value = {"id": 99, "title": "x"}
    _install_fake_client(monkeypatch, client_mock)

    result = runner.invoke(cli_mod.app, [
        "composites", "create",
        "--title", "x", "--kind", "recurring",
        "--items-file", str(items_file),
    ])
    assert result.exit_code == 0, result.output
    kwargs = client_mock.create_composite.call_args.kwargs
    assert kwargs["items"] == [
        {"ref_report_id": 1, "note": "a"},
        {"ref_report_id": 2, "note": "b"},
    ]


def test_composites_update_invokes_client(monkeypatch):
    client_mock = MagicMock()
    client_mock.update_composite.return_value = {
        "id": 10, "title": "renamed", "revision": 4,
    }
    _install_fake_client(monkeypatch, client_mock)

    result = runner.invoke(cli_mod.app, [
        "composites", "update", "10",
        "--title", "renamed",
        "--view-mode", "two_col",
        "--description", "hello",
        "--expected-revision", "3",
    ])
    assert result.exit_code == 0, result.output
    call = client_mock.update_composite.call_args
    assert call.args[0] == 10
    kwargs = call.kwargs
    assert kwargs["title"] == "renamed"
    assert kwargs["view_mode"] == "two_col"
    assert kwargs["description"] == "hello"
    assert kwargs["expected_revision"] == 3


def test_composites_items_set_invokes_client(monkeypatch, tmp_path: Path):
    items_file = tmp_path / "items.json"
    payload = {
        "items": [
            {"ref_report_id": 100, "note": "first", "display_column": 1},
            {"ref_report_id": 200, "note": "second", "display_column": 2},
        ],
    }
    items_file.write_text(json.dumps(payload), encoding="utf-8")

    client_mock = MagicMock()
    client_mock.update_composite.return_value = {"id": 10, "revision": 5,
                                                  "items": payload["items"]}
    _install_fake_client(monkeypatch, client_mock)

    result = runner.invoke(cli_mod.app, [
        "composites", "items-set", "10",
        "--items-file", str(items_file),
        "--expected-revision", "4",
    ])
    assert result.exit_code == 0, result.output
    kwargs = client_mock.update_composite.call_args.kwargs
    assert kwargs["items"] == payload["items"]
    assert kwargs["expected_revision"] == 4


# --------------------------------------------------------------------------- #
# composites delete / publish / unpublish
# --------------------------------------------------------------------------- #
def test_composites_delete_requires_confirm(monkeypatch):
    client_mock = MagicMock()
    _install_fake_client(monkeypatch, client_mock)

    # Without --yes the CLI must abort + NOT call the client.
    result = runner.invoke(cli_mod.app, ["composites", "delete", "10"])
    assert result.exit_code != 0
    client_mock.delete_composite.assert_not_called()


def test_composites_delete_with_confirm(monkeypatch):
    client_mock = MagicMock()
    client_mock.delete_composite.return_value = None
    _install_fake_client(monkeypatch, client_mock)

    result = runner.invoke(cli_mod.app, [
        "composites", "delete", "10", "--yes",
    ])
    assert result.exit_code == 0, result.output
    client_mock.delete_composite.assert_called_once_with(10)


def test_composites_publish_invokes_client(monkeypatch):
    client_mock = MagicMock()
    client_mock.publish_composite.return_value = {
        "id": 10, "published_at": "2026-06-06T00:00:00Z",
    }
    _install_fake_client(monkeypatch, client_mock)

    result = runner.invoke(cli_mod.app, ["composites", "publish", "10"])
    assert result.exit_code == 0, result.output
    client_mock.publish_composite.assert_called_once_with(10)


def test_composites_unpublish_invokes_client(monkeypatch):
    client_mock = MagicMock()
    client_mock.unpublish_composite.return_value = {
        "id": 10, "published_at": None,
    }
    _install_fake_client(monkeypatch, client_mock)

    result = runner.invoke(cli_mod.app, ["composites", "unpublish", "10"])
    assert result.exit_code == 0, result.output
    client_mock.unpublish_composite.assert_called_once_with(10)


# --------------------------------------------------------------------------- #
# notifications list / unread-count / mark-read / mark-all-read
# --------------------------------------------------------------------------- #
def test_notifications_list_invokes_client(monkeypatch):
    client_mock = MagicMock()
    client_mock.list_notifications.return_value = {
        "items": [{"id": 1, "type": "x"}],
        "unread_count": 3,
    }
    _install_fake_client(monkeypatch, client_mock)

    result = runner.invoke(cli_mod.app, [
        "notifications", "list",
        "--unread-only", "--limit", "20",
    ])
    assert result.exit_code == 0, result.output
    kwargs = client_mock.list_notifications.call_args.kwargs
    assert kwargs == {"unread_only": True, "limit": 20, "before_id": None}


def test_notifications_unread_count_invokes_client(monkeypatch):
    client_mock = MagicMock()
    client_mock.unread_notification_count.return_value = 5
    _install_fake_client(monkeypatch, client_mock)

    result = runner.invoke(cli_mod.app, ["notifications", "unread-count"])
    assert result.exit_code == 0, result.output
    client_mock.unread_notification_count.assert_called_once_with()
    assert "\"unread_count\": 5" in result.output


def test_notifications_mark_read_invokes_client(monkeypatch):
    client_mock = MagicMock()
    client_mock.mark_notification_read.return_value = {"id": 7}
    _install_fake_client(monkeypatch, client_mock)

    result = runner.invoke(cli_mod.app, [
        "notifications", "mark-read", "7",
    ])
    assert result.exit_code == 0, result.output
    client_mock.mark_notification_read.assert_called_once_with(7)


def test_notifications_mark_all_read_invokes_client(monkeypatch):
    client_mock = MagicMock()
    client_mock.mark_all_notifications_read.return_value = 9
    _install_fake_client(monkeypatch, client_mock)

    result = runner.invoke(cli_mod.app, ["notifications", "mark-all-read"])
    assert result.exit_code == 0, result.output
    client_mock.mark_all_notifications_read.assert_called_once_with()
    assert "\"marked_read\": 9" in result.output
