"""End-to-end lock: a 403 with the Korean author-lock signature must
surface through `call_tool` as a structured `{"error":"author_locked",
"reason":..., "report_id":...}` envelope — NOT as a generic
`{"error":"API error", ...}` payload.

Two layers exercised:
  1. `client._build_typed_error` correctly classifies the 403 as
     `AuthorLockedError` and parses out reason + report_id.
  2. `mcp_server.call_tool` catches AuthorLockedError BEFORE the generic
     ApiError catch — so the structured envelope wins.

The MCP server's except-block ordering is the critical part — if a
future refactor reorders them so `except ApiError` comes first, the
AuthorLockedError (an ApiError subclass) gets the generic envelope and
the LLM has to parse Korean to know what happened.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from unittest.mock import MagicMock

from report_skill import mcp_server
from report_skill.client import (
    ApiError,
    AuthorLockedError,
    BoardShareForbiddenError,
    CompositePresetPermissionError,
    CompositeRevisionConflict,
    FinalizedReadOnlyError,
    LockHeldByOtherError,
    LockNotHeldError,
    NoEditPermissionError,
    OutOfWorkspaceScopeError,
    ReportArchiveClient,
    RevisionMismatchError,
    ShareSetupForbiddenError,
    TakedownAlreadyProcessedError,
    TakedownManagerForbiddenError,
    TakedownOwnerForbiddenError,
    TrashRestoreForbiddenError,
)


# --------------------------------------------------------------------------- #
# 1) Client typed-error mapping: 403 + Korean signature → AuthorLockedError
# --------------------------------------------------------------------------- #
def test_build_typed_error_classifies_korean_author_lock() -> None:
    msg = "작성자가 수정 잠금 상태입니다 (사유: 검토 중). 잠금 해제 후 다시 시도하세요."
    err = ReportArchiveClient._build_typed_error(
        status_code=403,
        message=msg,
        path="/reports/42",
        body={"success": False, "message": msg, "errors": None},
    )
    assert isinstance(err, AuthorLockedError), (
        f"403 with Korean author-lock prefix must map to AuthorLockedError; "
        f"got {type(err).__name__ if err else 'None'}"
    )
    # reason parsed from `(사유: …)` and report_id from URL.
    assert err.reason == "검토 중", f"reason parse failed: {err.reason!r}"
    assert err.report_id == 42, f"report_id parse failed: {err.report_id!r}"


def test_build_typed_error_passes_through_other_403() -> None:
    """A 403 without the author-lock signature must NOT be mis-classified."""
    err = ReportArchiveClient._build_typed_error(
        status_code=403,
        message="something else",
        path="/reports/99",
        body={"success": False, "message": "something else"},
    )
    assert not isinstance(err, AuthorLockedError), (
        "non-author-lock 403 was wrongly classified as AuthorLockedError"
    )


# --------------------------------------------------------------------------- #
# 2) MCP call_tool envelope: AuthorLockedError → structured error
# --------------------------------------------------------------------------- #
def _install_fake_client_that_raises(monkeypatch, exc: BaseException) -> None:
    """Patch ReportArchiveClient so any method call raises `exc`."""
    client_mock = MagicMock()
    # Make every client method raise — the dispatcher's first client call
    # propagates the exception up to call_tool.
    client_mock.fetch_report_lock_status.side_effect = exc
    client_mock.publish_report.side_effect = exc
    client_mock.fetch_template.side_effect = exc
    client_mock.create_report.side_effect = exc
    client_mock.get.side_effect = exc

    class _FakeClientCtor:
        def __call__(self, *_a, **_kw):
            return self

        def __enter__(self):
            return client_mock

        def __exit__(self, *_exc):
            return None

    monkeypatch.setattr(mcp_server, "ReportArchiveClient", _FakeClientCtor())
    return client_mock


def _invoke_call_tool(name: str, args: dict) -> dict:
    """Run the MCP `call_tool` async coroutine and return the parsed JSON envelope.

    v0.7.0 — error paths now raise Exception(json_envelope) so the MCP
    framework yields CallToolResult.isError=True. Success path still returns
    list[TextContent]. Both forms are unwrapped here for test convenience.
    """
    try:
        content = asyncio.run(mcp_server.call_tool(name, args))
    except Exception as exc:
        return json.loads(str(exc))
    assert isinstance(content, list) and content, (
        f"call_tool returned unexpected shape: {content!r}"
    )
    text = content[0].text
    return json.loads(text)


def test_call_tool_returns_author_locked_envelope_on_korean_lock(monkeypatch) -> None:
    exc = AuthorLockedError(reason="검토 중", report_id=42, status_code=403)
    _install_fake_client_that_raises(monkeypatch, exc)

    out = _invoke_call_tool("report_lock_status", {"report_id": 42})

    # Critical contract: the LLM sees `error="author_locked"` (not the
    # generic "API error") so it can react without parsing Korean.
    assert out.get("error") == "author_locked", (
        f"expected structured author_locked envelope, got: {out}"
    )
    assert out.get("reason") == "검토 중", f"reason missing: {out}"
    assert out.get("report_id") == 42, f"report_id missing: {out}"


def test_call_tool_uses_generic_envelope_for_non_author_locked(monkeypatch) -> None:
    """Plain ApiError (NOT AuthorLockedError subclass) still flows through
    the generic ApiError branch. Ensures the typed dispatch isn't
    over-eager and doesn't accidentally classify unrelated 5xx errors."""
    exc = ApiError("boom", status_code=500, payload={"detail": "boom"})
    _install_fake_client_that_raises(monkeypatch, exc)

    out = _invoke_call_tool("report_lock_status", {"report_id": 1})

    assert out.get("error") == "API error", out
    assert out.get("status_code") == 500
    assert "message" in out


# --------------------------------------------------------------------------- #
# 3) Except-block ordering invariant (source-level check)
# --------------------------------------------------------------------------- #
def test_typed_error_map_lists_authorlocked_before_generic_path() -> None:
    """Source-level invariant: in mcp_server.call_tool the typed-error
    dispatch (the `_TYPED_ERROR_MAP` walk) MUST run BEFORE the fallback
    generic-ApiError envelope. Since AuthorLockedError IS-A ApiError,
    reversing the order would route every author-lock through the
    generic envelope and the LLM would have to parse Korean.

    v0.5.1 used `except AuthorLockedError:` + `except ApiError:` blocks.
    v0.5.2 unified them: a single `except ApiError:` walks the typed map
    first, then falls back to `_api_error_payload`. Either shape is
    acceptable so long as AuthorLockedError gets routed to the
    structured envelope BEFORE the generic fallback runs.

    We assert by source-level inspection so the test reads as
    documentation of the contract.
    """
    src = inspect.getsource(mcp_server.call_tool)
    # The typed-map walk OR a literal `except AuthorLockedError` must appear
    # before the generic ApiError envelope return / `_api_error_payload`.
    typed_marker = src.find("_TYPED_ERROR_MAP")
    legacy_marker = src.find("except AuthorLockedError")
    generic_marker = src.find("_api_error_payload")
    legacy_generic = src.find("API error")  # v0.5.1 inline literal
    early_idx = min(i for i in (typed_marker, legacy_marker) if i != -1)
    late_idx = min(i for i in (generic_marker, legacy_generic) if i != -1)
    assert early_idx != -1, (
        "call_tool no longer routes AuthorLockedError before generic ApiError "
        "— either restore `except AuthorLockedError` or `_TYPED_ERROR_MAP`."
    )
    assert early_idx < late_idx, (
        "typed-error routing invariant violated: AuthorLockedError must be "
        "matched BEFORE the generic ApiError envelope "
        f"(typed/legacy marker at {early_idx}, generic at {late_idx})."
    )


# --------------------------------------------------------------------------- #
# 4) End-to-end through write tool — dispatcher → client → call_tool envelope
# --------------------------------------------------------------------------- #
def test_call_tool_report_publish_maps_authorlocked(monkeypatch) -> None:
    """Verify the envelope flows through a write-path dispatcher
    (report_publish), not just the read-path `report_lock_status`."""
    exc = AuthorLockedError(reason="review", report_id=7, status_code=403)
    _install_fake_client_that_raises(monkeypatch, exc)

    out = _invoke_call_tool("report_publish", {"report_id": 7})

    assert out.get("error") == "author_locked", out
    assert out.get("reason") == "review", out
    assert out.get("report_id") == 7, out


# --------------------------------------------------------------------------- #
# 5) v0.7.0 — sibling tests for the other typed exceptions.
#
# Each test feeds the body/status pair through `_build_typed_error` (the
# single source of truth used by `_request`) and asserts the right subclass
# is built. We don't need to spin up an httpx MockTransport — `_build_typed_error`
# is a pure classifier over (status_code, message, path, body). Going
# through the classifier exercises the exact code path `_request` would
# hit for a real 409/403 from the backend, without any network plumbing.
#
# Pattern matches the existing AuthorLockedError test
# (`test_build_typed_error_classifies_korean_author_lock`).
# --------------------------------------------------------------------------- #
def test_build_typed_error_classifies_lock_held_by_other() -> None:
    """409 + payload.errors[0].code == 'lock_held_by_other' → LockHeldByOtherError.
    Holder dict on the error envelope is surfaced for diagnostics."""
    holder = {"user_id": 42, "user_name": "Alice"}
    body = {
        "success": False,
        "message": "다른 사용자가 잠금을 보유 중입니다.",
        "errors": [{"code": "lock_held_by_other",
                    "message": "lock held",
                    "holder": holder}],
    }
    err = ReportArchiveClient._build_typed_error(
        status_code=409,
        message="다른 사용자가 잠금을 보유 중입니다.",
        path="/reports/42/lock",
        body=body,
    )
    assert isinstance(err, LockHeldByOtherError), (
        f"409 lock_held_by_other must map to LockHeldByOtherError; "
        f"got {type(err).__name__ if err else 'None'}"
    )
    assert err.code == "lock_held_by_other"
    assert err.holder == holder, f"holder not surfaced: {err.holder!r}"


def test_build_typed_error_classifies_lock_not_held() -> None:
    """409 + payload.errors[0].code == 'lock_not_held' → LockNotHeldError."""
    body = {
        "success": False,
        "message": "현재 편집 잠금을 보유하고 있지 않습니다.",
        "errors": [{"code": "lock_not_held", "message": "no lock"}],
    }
    err = ReportArchiveClient._build_typed_error(
        status_code=409,
        message="현재 편집 잠금을 보유하고 있지 않습니다.",
        path="/reports/42/lock",
        body=body,
    )
    assert isinstance(err, LockNotHeldError), (
        f"409 lock_not_held must map to LockNotHeldError; "
        f"got {type(err).__name__ if err else 'None'}"
    )
    assert err.code == "lock_not_held"


def test_build_typed_error_classifies_revision_mismatch() -> None:
    """409 + payload.errors[0].code == 'revision_mismatch' → RevisionMismatchError.
    Callers (append_to_blocks retry loop) match on this subclass to know
    they should reload + rebase."""
    body = {
        "success": False,
        "message": "expected_revision does not match current revision",
        "errors": [{"code": "revision_mismatch",
                    "message": "stale revision"}],
    }
    err = ReportArchiveClient._build_typed_error(
        status_code=409,
        message="expected_revision does not match current revision",
        path="/reports/42",
        body=body,
    )
    assert isinstance(err, RevisionMismatchError), (
        f"409 revision_mismatch must map to RevisionMismatchError; "
        f"got {type(err).__name__ if err else 'None'}"
    )
    assert err.code == "revision_mismatch"


def test_build_typed_error_classifies_composite_revision_mismatch() -> None:
    """409 + payload.errors[0].code == 'composite_revision_mismatch'
    → CompositeRevisionConflict (with composite_id parsed from URL)."""
    body = {
        "success": False,
        "message": "composite revision mismatch",
        "errors": [{"code": "composite_revision_mismatch",
                    "message": "stale composite revision"}],
    }
    err = ReportArchiveClient._build_typed_error(
        status_code=409,
        message="composite revision mismatch",
        path="/composites/77",
        body=body,
    )
    assert isinstance(err, CompositeRevisionConflict), (
        f"409 composite_revision_mismatch must map to CompositeRevisionConflict; "
        f"got {type(err).__name__ if err else 'None'}"
    )
    assert err.code == "composite_revision_mismatch"
    assert err.composite_id == 77, (
        f"composite_id parse failed: {err.composite_id!r}"
    )


def test_build_typed_error_classifies_finalized_read_only() -> None:
    """403 + Korean prefix '발행된 보고서' → FinalizedReadOnlyError
    (with report_id parsed from URL). Caller signal: unpublish first."""
    msg = "발행된 보고서는 편집할 수 없습니다."
    body = {"success": False, "message": msg, "errors": None}
    err = ReportArchiveClient._build_typed_error(
        status_code=403,
        message=msg,
        path="/reports/42",
        body=body,
    )
    assert isinstance(err, FinalizedReadOnlyError), (
        f"403 finalized prefix must map to FinalizedReadOnlyError; "
        f"got {type(err).__name__ if err else 'None'}"
    )
    assert err.code == "finalized_read_only"
    assert err.report_id == 42, f"report_id parse failed: {err.report_id!r}"


def test_build_typed_error_classifies_no_edit_permission() -> None:
    """403 + '편집할 권한이 없' substring → NoEditPermissionError
    (covers the three backend variants — base, link-add, link-remove)."""
    msg = "이 보고서를 편집할 권한이 없습니다."
    body = {"success": False, "message": msg, "errors": None}
    err = ReportArchiveClient._build_typed_error(
        status_code=403,
        message=msg,
        path="/reports/99",
        body=body,
    )
    assert isinstance(err, NoEditPermissionError), (
        f"403 no-edit-permission must map to NoEditPermissionError; "
        f"got {type(err).__name__ if err else 'None'}"
    )
    assert err.code == "no_edit_permission"
    assert err.report_id == 99, f"report_id parse failed: {err.report_id!r}"


def test_build_typed_error_classifies_out_of_workspace_scope() -> None:
    """403 + ASCII exact 'Out of workspace scope' → OutOfWorkspaceScopeError."""
    msg = "Out of workspace scope"
    body = {"success": False, "message": msg, "errors": None}
    err = ReportArchiveClient._build_typed_error(
        status_code=403,
        message=msg,
        path="/reports/1",
        body=body,
    )
    assert isinstance(err, OutOfWorkspaceScopeError), (
        f"403 'Out of workspace scope' must map to OutOfWorkspaceScopeError; "
        f"got {type(err).__name__ if err else 'None'}"
    )
    assert err.code == "out_of_workspace_scope"


# --------------------------------------------------------------------------- #
# v0.8.1/v0.8.2 — grants 403 typed exceptions (RA dbdbf99)
# --------------------------------------------------------------------------- #
def test_build_typed_error_classifies_share_setup_forbidden() -> None:
    """403 + Korean 'public/content share gate' message → ShareSetupForbiddenError."""
    msg = "공유 설정은 작성자(또는 시스템 관리자)만 변경할 수 있습니다."
    body = {"success": False, "message": msg, "errors": None}
    err = ReportArchiveClient._build_typed_error(
        status_code=403,
        message=msg,
        path="/reports/7/shares",
        body=body,
    )
    assert isinstance(err, ShareSetupForbiddenError), (
        f"403 share-owner gate must map to ShareSetupForbiddenError; "
        f"got {type(err).__name__ if err else 'None'}"
    )
    assert err.code == "share_setup_forbidden"


def test_build_typed_error_classifies_board_share_forbidden() -> None:
    """403 + Korean 'board share gate' message → BoardShareForbiddenError."""
    msg = "게시판 공유는 그 게시판 매니저(또는 시스템 관리자)만 변경할 수 있습니다."
    body = {"success": False, "message": msg, "errors": None}
    err = ReportArchiveClient._build_typed_error(
        status_code=403,
        message=msg,
        path="/workspaces/dx/shares",
        body=body,
    )
    assert isinstance(err, BoardShareForbiddenError), (
        f"403 board-share gate must map to BoardShareForbiddenError; "
        f"got {type(err).__name__ if err else 'None'}"
    )
    assert err.code == "board_share_forbidden"


def test_build_typed_error_classifies_trash_restore_forbidden() -> None:
    """v0.10.1 — 403 + Korean trash/restore owner gate → TrashRestoreForbiddenError."""
    for msg in (
        "이 보고서를 삭제할 권한이 없습니다 (소유자만 가능).",
        "이 보고서를 복구할 권한이 없습니다 (소유자만 가능).",
    ):
        body = {"success": False, "message": msg, "errors": None}
        err = ReportArchiveClient._build_typed_error(
            status_code=403, message=msg, path="/reports/7/trash", body=body,
        )
        assert isinstance(err, TrashRestoreForbiddenError), (
            f"trash/restore owner gate must map to TrashRestoreForbiddenError; "
            f"got {type(err).__name__ if err else 'None'} for: {msg}"
        )
        assert err.code == "trash_restore_forbidden"
        assert err.report_id == 7  # extracted from path


def test_build_typed_error_classifies_takedown_manager_forbidden() -> None:
    """v0.10.1 — 403 + Korean takedown-manager gate → TakedownManagerForbiddenError."""
    for msg in (
        "이 게시판에서 게시취소할 권한이 없습니다 (게시판 매니저만 가능 — 작성자 본인).",
        "이 게시판의 게시취소 요청을 처리할 권한이 없습니다 (게시판 매니저만 가능).",
    ):
        body = {"success": False, "message": msg, "errors": None}
        err = ReportArchiveClient._build_typed_error(
            status_code=403, message=msg, path="/takedown-requests/3/approve",
            body=body,
        )
        assert isinstance(err, TakedownManagerForbiddenError), (
            f"takedown manager gate must map to TakedownManagerForbiddenError; "
            f"got {type(err).__name__ if err else 'None'} for: {msg}"
        )
        assert err.code == "takedown_manager_forbidden"


def test_build_typed_error_classifies_takedown_already_processed() -> None:
    """v0.10.1 — 403 + 'already processed' → TakedownAlreadyProcessedError."""
    msg = "이미 처리된 요청입니다."
    body = {"success": False, "message": msg, "errors": None}
    err = ReportArchiveClient._build_typed_error(
        status_code=403, message=msg,
        path="/takedown-requests/3/approve", body=body,
    )
    assert isinstance(err, TakedownAlreadyProcessedError), (
        f"already-processed must map to TakedownAlreadyProcessedError; "
        f"got {type(err).__name__ if err else 'None'}"
    )
    assert err.code == "takedown_already_processed"


def test_build_typed_error_classifies_takedown_owner_forbidden() -> None:
    """v0.10.2 — 403 + Korean takedown-owner gate → TakedownOwnerForbiddenError."""
    msg = "본인 보고서만 게시취소를 요청할 수 있습니다."
    body = {"success": False, "message": msg, "errors": None}
    err = ReportArchiveClient._build_typed_error(
        status_code=403, message=msg,
        path="/reports/9/takedown-requests", body=body,
    )
    assert isinstance(err, TakedownOwnerForbiddenError), (
        f"takedown owner gate must map to TakedownOwnerForbiddenError; "
        f"got {type(err).__name__ if err else 'None'}"
    )
    assert err.code == "takedown_owner_forbidden"
    assert err.report_id == 9  # extracted from path


def test_typed_error_map_includes_v010_classes(monkeypatch) -> None:
    """v0.10.1 + v0.10.2 — _TYPED_ERROR_MAP must include the 4 new soft-delete +
    takedown subclasses so the LLM receives structured envelopes instead of
    opaque ApiError."""
    table = mcp_server._build_typed_error_map()
    codes = [code for (_cls, code, _) in table]
    for expected in (
        "trash_restore_forbidden",
        "takedown_manager_forbidden",
        "takedown_already_processed",
        "takedown_owner_forbidden",
    ):
        assert expected in codes, (
            f"{expected} missing from _TYPED_ERROR_MAP: {codes}"
        )


def test_typed_error_map_includes_grants_classes(monkeypatch) -> None:
    """v0.8.2 — the call_tool error map must include the two grants subclasses
    so the LLM receives structured {error: 'share_setup_forbidden' | 'board_share_forbidden'}
    instead of opaque ApiError."""
    table = mcp_server._build_typed_error_map()
    codes = [code for (_cls, code, _) in table]
    assert "share_setup_forbidden" in codes, (
        f"share_setup_forbidden missing from _TYPED_ERROR_MAP: {codes}"
    )
    assert "board_share_forbidden" in codes, (
        f"board_share_forbidden missing from _TYPED_ERROR_MAP: {codes}"
    )


# --------------------------------------------------------------------------- #
# v0.15.0 — composite presets (RA c5c57ca) typed 403s
# --------------------------------------------------------------------------- #
def test_build_typed_error_classifies_composite_preset_forbidden() -> None:
    """403 + Korean 양식 manage gate → CompositePresetPermissionError."""
    for msg in (
        "이 양식을 수정할 권한이 없습니다.",
        "이 양식을 삭제할 권한이 없습니다.",
    ):
        body = {"success": False, "message": msg, "errors": None}
        err = ReportArchiveClient._build_typed_error(
            status_code=403, message=msg, path="/composite-presets/3", body=body,
        )
        assert isinstance(err, CompositePresetPermissionError), (
            f"양식 manage gate must map to CompositePresetPermissionError; "
            f"got {type(err).__name__ if err else 'None'} for: {msg}"
        )
        assert err.code == "composite_preset_forbidden"


def test_build_typed_error_classifies_composite_scope_korean() -> None:
    """v0.15.0 — the Korean composite writable-scope gate (shared by
    POST /composites and POST /composite-presets/{id}/new-composite) maps to
    the same OutOfWorkspaceScopeError as the ASCII variant."""
    msg = "종합보고는 현재 부서 또는 하위 부서에만 작성할 수 있습니다."
    body = {"success": False, "message": msg, "errors": None}
    err = ReportArchiveClient._build_typed_error(
        status_code=403, message=msg,
        path="/composite-presets/3/new-composite", body=body,
    )
    assert isinstance(err, OutOfWorkspaceScopeError), (
        f"Korean composite scope gate must map to OutOfWorkspaceScopeError; "
        f"got {type(err).__name__ if err else 'None'}"
    )
