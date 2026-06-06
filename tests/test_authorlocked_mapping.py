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
    ReportArchiveClient,
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
    """Run the MCP `call_tool` async coroutine and return the parsed JSON."""
    content = asyncio.run(mcp_server.call_tool(name, args))
    # `call_tool` returns list[TextContent]. Parse the single text element.
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
