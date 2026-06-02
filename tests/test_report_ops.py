"""Tests for ``report_skill.report_ops`` — edit-lock + PATCH flows.

ReportArchiveClient is fully stubbed via unittest.mock.MagicMock so these
tests need no backend, no network, no DB. ``time.sleep`` is patched out
so retry/backoff loops run instantly.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from report_skill import report_ops
from report_skill.client import ApiError


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _make_client(*, get_return=None, post_return=None, request_return=None,
                 fetch_template_return=None):
    """Build a MagicMock that quacks like ReportArchiveClient."""
    client = MagicMock()
    client.get = MagicMock(return_value=get_return or {})
    client.post = MagicMock(return_value=post_return or {"locked": True})
    client._request = MagicMock(return_value=request_return or {})
    client.fetch_template = MagicMock(return_value=fetch_template_return or {})
    return client


def _two_page_report(revision: int = 1) -> dict:
    return {
        "id": 42,
        "revision": revision,
        "pages": [
            {
                "template_id": "weekly-dev",
                "template_version": 1,
                "content": {
                    "summary": {"markdown": "old summary"},
                    "progress": {"items": ["task A"]},
                },
                "extra_blocks": [
                    {"id": "extra1", "type": "milestone", "props": {}},
                ],
            },
            {
                "template_id": "weekly-dev",
                "template_version": 1,
                "content": {"summary": {"markdown": "page2 stuff"}},
            },
        ],
    }


def _weekly_dev_template() -> dict:
    return {
        "template_id": "weekly-dev",
        "template_version": 1,
        "schema": {
            "blocks": [
                {"id": "summary", "type": "rich_text", "props": {}},
                {"id": "progress", "type": "bulleted_list", "props": {}},
            ],
        },
    }


# --------------------------------------------------------------------------- #
# edit_lock — happy path
# --------------------------------------------------------------------------- #
def test_edit_lock_happy_path_acquires_and_releases():
    client = _make_client()
    with patch("report_skill.report_ops.time.sleep") as _:
        with report_ops.edit_lock(client, 42):
            assert client.post.call_count == 1
            client.post.assert_called_with("/reports/42/lock?force=true")
    # Released on exit via DELETE
    delete_calls = [c for c in client._request.call_args_list
                    if c.args and c.args[0] == "DELETE"]
    assert len(delete_calls) == 1
    assert delete_calls[0].args[1] == "/reports/42/lock"


# --------------------------------------------------------------------------- #
# edit_lock — transient 5xx retry, then success
# --------------------------------------------------------------------------- #
def test_edit_lock_retries_transient_5xx_then_succeeds():
    client = _make_client()
    # First two calls raise 500, third succeeds.
    err_500 = ApiError("server overloaded", status_code=500)
    client.post.side_effect = [err_500, err_500, {"locked": True}]

    with patch("report_skill.report_ops.time.sleep") as sleep_mock:
        with report_ops.edit_lock(client, 42):
            pass

    # All three POST attempts were made
    assert client.post.call_count == 3
    # time.sleep called between the two failed attempts
    assert sleep_mock.call_count == 2
    # Each backoff is positive
    for call in sleep_mock.call_args_list:
        assert call.args[0] > 0


# --------------------------------------------------------------------------- #
# edit_lock — connection-error (status_code=0) treated as transient
# --------------------------------------------------------------------------- #
def test_edit_lock_treats_status_zero_as_transient():
    client = _make_client()
    conn_err = ApiError("connection refused", status_code=0)
    client.post.side_effect = [conn_err, {"locked": True}]
    with patch("report_skill.report_ops.time.sleep"):
        with report_ops.edit_lock(client, 42):
            pass
    assert client.post.call_count == 2


# --------------------------------------------------------------------------- #
# edit_lock — non-transient 4xx must NOT retry
# --------------------------------------------------------------------------- #
def test_edit_lock_403_does_not_retry_and_raises_runtime_error():
    client = _make_client()
    client.post.side_effect = ApiError("forbidden", status_code=403)
    with patch("report_skill.report_ops.time.sleep"):
        with pytest.raises(RuntimeError) as exc_info:
            with report_ops.edit_lock(client, 42):
                pass
    assert "could not acquire edit lock" in str(exc_info.value)
    assert client.post.call_count == 1  # one attempt only


# --------------------------------------------------------------------------- #
# edit_lock — all retries exhausted
# --------------------------------------------------------------------------- #
def test_edit_lock_exhausted_retries_raises_runtime_error():
    client = _make_client()
    client.post.side_effect = ApiError("perma-5xx", status_code=503)
    with patch("report_skill.report_ops.time.sleep"):
        with pytest.raises(RuntimeError) as exc_info:
            with report_ops.edit_lock(client, 42):
                pass
    # max_acquire_retries = 5 → 6 total attempts
    assert client.post.call_count == 6
    assert "could not acquire edit lock" in str(exc_info.value)


# --------------------------------------------------------------------------- #
# edit_lock — DELETE failure on release is swallowed
# --------------------------------------------------------------------------- #
def test_edit_lock_release_failure_swallowed():
    client = _make_client()
    client._request.side_effect = ApiError("oh no", status_code=500)
    with patch("report_skill.report_ops.time.sleep"):
        # Should NOT raise even though release failed
        with report_ops.edit_lock(client, 42):
            pass


# --------------------------------------------------------------------------- #
# fetch_report — simple GET wrapper
# --------------------------------------------------------------------------- #
def test_fetch_report_calls_get_with_correct_path():
    client = _make_client(get_return={"id": 99, "pages": []})
    out = report_ops.fetch_report(client, 99)
    client.get.assert_called_once_with("/reports/99")
    assert out == {"id": 99, "pages": []}


# --------------------------------------------------------------------------- #
# update_blocks — PATCHes page-content with patches applied + other blocks kept
# --------------------------------------------------------------------------- #
def test_update_blocks_preserves_other_blocks_on_page():
    client = _make_client(
        get_return=_two_page_report(),
        request_return={"updated": True},
    )
    with patch("report_skill.report_ops.time.sleep"):
        report_ops.update_blocks(
            client,
            42,
            page_index=0,
            block_patches={"summary": {"markdown": "NEW summary"}},
        )

    # The PATCH request was made
    patch_calls = [c for c in client._request.call_args_list
                   if c.args and c.args[0] == "PATCH"]
    assert len(patch_calls) == 1
    patch_body = patch_calls[0].kwargs.get("json") or patch_calls[0].args[2]
    pages = patch_body["pages"]
    page0_content = pages[0]["content"]
    # Patched block has new content
    assert page0_content["summary"] == {"markdown": "NEW summary"}
    # Untouched block preserved
    assert page0_content["progress"] == {"items": ["task A"]}
    # Page 1 untouched
    assert pages[1]["content"] == {"summary": {"markdown": "page2 stuff"}}


def test_update_blocks_with_no_pages_raises_value_error():
    client = _make_client(get_return={"id": 1, "pages": []})
    with patch("report_skill.report_ops.time.sleep"):
        with pytest.raises(ValueError):
            report_ops.update_blocks(client, 1, block_patches={"x": {}})


def test_update_blocks_out_of_range_page_raises_index_error():
    client = _make_client(get_return=_two_page_report())
    with patch("report_skill.report_ops.time.sleep"):
        with pytest.raises(IndexError):
            report_ops.update_blocks(client, 42, page_index=99,
                                     block_patches={"summary": {}})


# --------------------------------------------------------------------------- #
# add_page — PATCH body has pages = old + new_page
# --------------------------------------------------------------------------- #
def test_add_page_appends_new_page_to_pages_array():
    client = _make_client(
        get_return=_two_page_report(),
        request_return={"id": 42, "pages": []},
    )
    with patch("report_skill.report_ops.time.sleep"):
        report_ops.add_page(
            client, 42,
            template_id="rfc",
            template_version=2,
            name="Appendix",
            content={"summary": {"markdown": "appendix"}},
        )

    patch_calls = [c for c in client._request.call_args_list
                   if c.args and c.args[0] == "PATCH"]
    body = patch_calls[0].kwargs.get("json") or patch_calls[0].args[2]
    pages = body["pages"]
    assert len(pages) == 3  # 2 old + 1 new
    new_page = pages[-1]
    assert new_page["template_id"] == "rfc"
    assert new_page["template_version"] == 2
    assert new_page["name"] == "Appendix"
    assert new_page["content"] == {"summary": {"markdown": "appendix"}}


# --------------------------------------------------------------------------- #
# append_to_blocks — happy path (merge runs, PATCH issued)
# --------------------------------------------------------------------------- #
def test_append_to_blocks_calls_merge_and_patches():
    client = _make_client(
        get_return=_two_page_report(),
        fetch_template_return=_weekly_dev_template(),
        request_return={"ok": True},
    )
    with patch("report_skill.report_ops.merge_mod.merge_block_content",
               wraps=report_ops.merge_mod.merge_block_content) as merge_spy, \
         patch("report_skill.report_ops.time.sleep"):
        report_ops.append_to_blocks(
            client,
            42,
            page_index=0,
            block_appends={"progress": ["task B", "task C"]},
        )

    # merge_block_content was invoked once for the one block
    assert merge_spy.call_count == 1
    args, _ = merge_spy.call_args
    assert args[0] == "bulleted_list"  # widget type from template
    assert args[1] == {"items": ["task A"]}  # existing content

    # PATCH body carries the merged content + expected_revision
    patch_calls = [c for c in client._request.call_args_list
                   if c.args and c.args[0] == "PATCH"]
    assert len(patch_calls) == 1
    body = patch_calls[0].kwargs.get("json") or patch_calls[0].args[2]
    assert body["expected_revision"] == 1
    merged = body["pages"][0]["content"]["progress"]
    assert merged["items"] == ["task A", "task B", "task C"]


def test_append_to_blocks_unknown_block_id_raises_key_error():
    client = _make_client(
        get_return=_two_page_report(),
        fetch_template_return=_weekly_dev_template(),
    )
    with patch("report_skill.report_ops.time.sleep"):
        with pytest.raises(KeyError):
            report_ops.append_to_blocks(
                client, 42,
                block_appends={"does_not_exist": ["x"]},
            )


# --------------------------------------------------------------------------- #
# append_to_blocks — revision-mismatch retry succeeds on 2nd attempt
# --------------------------------------------------------------------------- #
def test_append_to_blocks_retries_on_revision_mismatch_then_succeeds():
    """First PATCH returns revision_mismatch error; second succeeds.

    Mock state machine:
      - fetch_report is called once per attempt → returns reports with
        revision=1 then revision=2.
      - First _request("PATCH",...) raises ApiError with the
        revision_mismatch payload; second returns success dict.
      - The lock POST + DELETE go through normally on each attempt.
    """
    client = _make_client(fetch_template_return=_weekly_dev_template())
    rev1 = _two_page_report(revision=1)
    rev2 = _two_page_report(revision=2)
    # GET sequence: attempt-1 fetch, attempt-2 fetch
    client.get.side_effect = [rev1, rev2]

    rev_err = ApiError(
        "revision mismatch",
        status_code=409,
        payload={"success": False, "errors": [{"code": "revision_mismatch"}]},
    )
    # _request is called for: PATCH attempt-1 (raises), DELETE lock (ok),
    # PATCH attempt-2 (ok), DELETE lock (ok).
    success_resp = {"id": 42, "revision": 3, "ok": True}
    client._request.side_effect = [
        rev_err,         # PATCH 1 → fails
        {},              # DELETE lock 1
        success_resp,    # PATCH 2 → succeeds
        {},              # DELETE lock 2
    ]

    with patch("report_skill.report_ops.time.sleep") as sleep_mock:
        out = report_ops.append_to_blocks(
            client, 42,
            block_appends={"progress": ["task B"]},
            max_retries=3,
        )

    assert out == success_resp
    # Two fetches happened → re-merge on retry
    assert client.get.call_count == 2
    # Backoff happened between the two attempts
    assert sleep_mock.call_count >= 1


# --------------------------------------------------------------------------- #
# append_to_blocks — exhaustion behaviour (3 attempts then propagate)
# --------------------------------------------------------------------------- #
def test_append_to_blocks_raises_revision_conflict_after_max_retries():
    """After max_retries+1 attempts all returning revision_mismatch, the
    skill raises its typed `RevisionConflict` so callers can specifically
    catch the "someone else won the race" case (vs other ApiError reasons)."""
    client = _make_client(fetch_template_return=_weekly_dev_template())
    client.get.return_value = _two_page_report()

    rev_err = ApiError(
        "revision mismatch",
        status_code=409,
        payload={"success": False, "errors": [{"code": "revision_mismatch"}]},
    )
    # Every PATCH fails with revision_mismatch; DELETEs succeed.
    # Pattern per attempt: [PATCH(rev_err), DELETE(ok)].  max_retries=2 →
    # 3 attempts → 3 PATCH + 3 DELETE calls.
    seq = []
    for _ in range(3):
        seq.append(rev_err)
        seq.append({})
    client._request.side_effect = seq

    with patch("report_skill.report_ops.time.sleep"):
        with pytest.raises(report_ops.RevisionConflict) as exc_info:
            report_ops.append_to_blocks(
                client, 42,
                block_appends={"progress": ["x"]},
                max_retries=2,
            )

    # The full retry budget was used: 3 fetch_report + 3 PATCH attempts.
    assert client.get.call_count == 3
    # RevisionConflict chains the underlying ApiError via __cause__.
    assert isinstance(exc_info.value.__cause__, ApiError)
    assert exc_info.value.__cause__.payload["errors"][0]["code"] == "revision_mismatch"


# --------------------------------------------------------------------------- #
# append_to_blocks — non-revision ApiError is NOT retried, surfaced as-is
# --------------------------------------------------------------------------- #
def test_append_to_blocks_non_revision_error_is_propagated_immediately():
    client = _make_client(fetch_template_return=_weekly_dev_template())
    client.get.return_value = _two_page_report()

    other_err = ApiError(
        "permission denied",
        status_code=403,
        payload={"success": False, "errors": [{"code": "forbidden"}]},
    )
    client._request.side_effect = [other_err, {}]  # PATCH fails, DELETE ok

    with patch("report_skill.report_ops.time.sleep"):
        with pytest.raises(ApiError):
            report_ops.append_to_blocks(
                client, 42,
                block_appends={"progress": ["x"]},
                max_retries=3,
            )

    # Only one fetch — no retry
    assert client.get.call_count == 1


# --------------------------------------------------------------------------- #
# delete_report — direct DELETE call, no lock
# --------------------------------------------------------------------------- #
def test_delete_report_calls_delete_endpoint_without_lock():
    client = _make_client(request_return={"deleted": True})
    out = report_ops.delete_report(client, 42)
    client._request.assert_called_once_with("DELETE", "/reports/42")
    assert out == {"deleted": True}
    # No lock POST issued
    client.post.assert_not_called()
