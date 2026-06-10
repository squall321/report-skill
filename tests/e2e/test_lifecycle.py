"""E2E lifecycle smoke tests against a LIVE ReportArchive backend.

Run with: ``RS_E2E=1 pytest tests/e2e -m e2e`` (see conftest docstring for
why both the env var AND the explicit ``-m e2e`` override are required).

Each test targets one historical escape family:
  * path typos / body field drift  -> create / mount / trash / purge flows
  * Korean error-string detection  -> finalized-edit 403, mount_forbidden 403
  * schema mismatches              -> snapshot drift vs live /widgets + /templates
  * concurrency envelope codes     -> stale expected_revision -> 409 typed
  * encoding                       -> Korean payload round-trip (no mojibake)

Mount-mutating tests (7-9) accept EITHER the success path OR the documented
typed 403 (``MountForbiddenError``) — the service account may not hold
board-manager rights on the configured workspace. Asserting the exception
TYPE still validates the endpoint path + error-detection wiring, which is
the point of this suite.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from report_skill import orchestrator, report_ops, schemas
from report_skill.catalog import BUNDLED_WIDGETS
from report_skill.client import (
    ApiError,
    FinalizedReadOnlyError,
    RevisionMismatchError,
)
from report_skill.config import settings

# v0.13.0 typed errors — import defensively so the module still collects
# against an older client.py (tests then assert status codes only).
try:
    from report_skill.client import MountForbiddenError
except ImportError:  # pragma: no cover
    MountForbiddenError = None  # type: ignore[assignment]
try:
    from report_skill.client import ReportStillMountedError
except ImportError:  # pragma: no cover
    ReportStillMountedError = None  # type: ignore[assignment]

_REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _send(proc: subprocess.Popen, msg: dict) -> None:
    proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
    proc.stdin.flush()


def _recv(proc: subprocess.Popen, timeout: float = 30.0) -> dict:
    """Read one line-delimited JSON-RPC message from the spawned server."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if line:
            return json.loads(line.decode("utf-8"))
        if proc.poll() is not None:
            err = proc.stderr.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"mcp server exited rc={proc.returncode}\n{err}")
    raise TimeoutError("no MCP message within timeout")


def _assert_mount_forbidden(exc: ApiError) -> None:
    """The documented alternative outcome for mount-mutating tests.

    A 403 here means the service account lacks board-manager rights on the
    configured workspace — legitimate. Asserting the typed class (not just
    the status) is itself the e2e validation of the 403 envelope-code
    detection added in v0.13.0.
    """
    assert isinstance(exc, ApiError) and exc.status_code == 403, (
        f"expected 403 on the mount path, got {type(exc).__name__}: {exc}"
    )
    if MountForbiddenError is not None:
        assert isinstance(exc, MountForbiddenError), (
            f"403 from a mount endpoint was NOT detected as MountForbiddenError "
            f"(got {type(exc).__name__}; message={exc}) — error-detection wiring gap"
        )


def _mount_or_validate_403(ra_client, report_id: int, slug: str) -> bool:
    """Mount the report; return True on success, False after validating the
    typed 403 path (the test should then return early — both outcomes pass)."""
    try:
        report_ops.mount_report(ra_client, report_id, workspace_slugs=[slug])
        return True
    except ApiError as exc:
        _assert_mount_forbidden(exc)
        return False


def _mount_slugs(ra_client, report_id: int) -> set:
    return {
        (m or {}).get("workspace_slug") or (m or {}).get("slug")
        for m in report_ops.list_mounts(ra_client, report_id)
    }


# --------------------------------------------------------------------------- #
# 1. MCP stdio handshake
# --------------------------------------------------------------------------- #
@pytest.mark.e2e
def test_e2e_handshake_tool_count(ra_client):
    """Spawn report-skill-mcp over stdio, initialize, and confirm tools/list
    advertises exactly len(_DISPATCH) tools (absorbs _verify_path_b_mcp.py)."""
    from report_skill.mcp_server import _DISPATCH

    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "report_skill.mcp_server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(_REPO_ROOT), env=env,
    )
    try:
        _send(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "rs-e2e", "version": "0"}},
        })
        init = _recv(proc)
        assert "result" in init, f"initialize failed: {init}"
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        resp = _recv(proc)
        tools = resp.get("result", {}).get("tools", [])
        assert len(tools) == len(_DISPATCH), (
            f"tools/list advertised {len(tools)} tools but _DISPATCH has "
            f"{len(_DISPATCH)} — advertisement/dispatch drift"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# --------------------------------------------------------------------------- #
# 2-3. create + read round-trip
# --------------------------------------------------------------------------- #
@pytest.mark.e2e
def test_e2e_create_minimal(e2e_report):
    """POST /reports through the real pipeline returns id + revision."""
    assert e2e_report.get("id") is not None
    assert e2e_report.get("revision") is not None
    assert str(e2e_report.get("title", "")).startswith("[rs-e2e] ")


@pytest.mark.e2e
def test_e2e_read_roundtrip(ra_client, e2e_report):
    """GET /reports/{id} returns the same title and every block we posted."""
    fetched = report_ops.fetch_report(ra_client, e2e_report["id"])
    assert fetched.get("title") == e2e_report.get("title")
    pages = fetched.get("pages") or []
    assert pages, "created report came back with no pages"
    content = pages[0].get("content") or {}
    sent = set(e2e_report["_e2e_input"])
    missing = sent - set(content)
    assert not missing, f"blocks lost in round-trip: {sorted(missing)}"
    # spot-check the rich_text body text survived verbatim
    body = json.dumps(content.get("summary"), ensure_ascii=False)
    assert e2e_report["_e2e_input"]["summary"] in body


# --------------------------------------------------------------------------- #
# 4. snapshot drift
# --------------------------------------------------------------------------- #
@pytest.mark.e2e
def test_e2e_snapshot_drift(ra_client):
    """Bundled data/ snapshot vs live /widgets + /templates.

    Not xfail'd: the bundled snapshot's fetched_at (2026-06-01 at authoring
    time) is recent and no known catalog change has landed since. If this
    starts failing, the fix is mechanical — see the assert messages.
    """
    bundled = json.loads(BUNDLED_WIDGETS.read_text(encoding="utf-8"))
    bw = bundled.get("widgets") or {}
    bundled_types = set(bw) if isinstance(bw, dict) else {
        w.get("type") for w in bw if isinstance(w, dict)
    }
    live = ra_client.fetch_widgets()
    lw = (live or {}).get("widgets") or []
    live_types = set(lw) if isinstance(lw, dict) else {
        w.get("type") for w in lw if isinstance(w, dict)
    }
    assert live_types == bundled_types, (
        f"widget catalog drift (bundled fetched_at={bundled.get('fetched_at')}): "
        f"live-only={sorted(live_types - bundled_types)} "
        f"bundled-only={sorted(bundled_types - live_types)} — "
        f"run `report-skill catalog sync` + rebundle src/report_skill/data/"
    )

    bundled_tpl_ids = set()
    for p in sorted((BUNDLED_WIDGETS.parent / "templates").glob("*.latest.json")):
        tpl = json.loads(p.read_text(encoding="utf-8"))
        if tpl.get("template_id"):
            bundled_tpl_ids.add(tpl["template_id"])
    live_tpl_ids = {
        t.get("template_id") for t in ra_client.fetch_templates(latest_only=True)
        if isinstance(t, dict)
    }
    assert live_tpl_ids == bundled_tpl_ids, (
        f"template catalog drift: live-only={sorted(live_tpl_ids - bundled_tpl_ids)} "
        f"bundled-only={sorted(bundled_tpl_ids - live_tpl_ids)} — "
        f"run `report-skill templates sync` (catalog_sync) + rebundle "
        f"src/report_skill/data/templates/"
    )


# --------------------------------------------------------------------------- #
# 5. optimistic concurrency — stale revision 409
# --------------------------------------------------------------------------- #
@pytest.mark.e2e
def test_e2e_revise_stale_revision(ra_client, e2e_report):
    """PATCH with a stale expected_revision must surface the typed
    RevisionMismatchError (409 envelope code); refetch + retry succeeds."""
    rid = e2e_report["id"]
    first = report_ops.fetch_report(ra_client, rid)
    stale_rev = int(first["revision"])

    # Bump the server-side revision so `stale_rev` actually goes stale.
    bumped = report_ops.update_blocks(
        ra_client, rid, title=f"{first.get('title')} (rev-bump)",
    )
    if int(bumped.get("revision", stale_rev)) == stale_rev:
        pytest.skip("backend did not bump revision on PATCH — "
                    "cannot stage a stale-revision conflict")

    with report_ops.edit_lock(ra_client, rid):
        with pytest.raises(RevisionMismatchError):
            ra_client._request(
                "PATCH", f"/reports/{rid}",
                json={"pages": first["pages"], "expected_revision": stale_rev},
            )
        # Recovery path: refetch -> retry with the current revision.
        fresh = report_ops.fetch_report(ra_client, rid)
        updated = ra_client._request(
            "PATCH", f"/reports/{rid}",
            json={"pages": fresh["pages"],
                  "expected_revision": int(fresh["revision"])},
        )
    assert updated.get("revision") is not None
    assert int(updated["revision"]) != stale_rev


# --------------------------------------------------------------------------- #
# 6. Korean 403 detection (best-effort)
# --------------------------------------------------------------------------- #
@pytest.mark.e2e
def test_e2e_korean_403_detection(ra_client, e2e_report):
    """Validate Korean error-string -> typed 403 mapping on a live message.

    Best-effort by design: the author-lock and no-edit-permission 403s need
    a SECOND account (or a foreign locked report) which this harness does
    not have. The one Korean 403 a single service account can self-trigger
    safely is finalized-read-only: publish our own report, then attempt an
    edit -> backend replies "발행된 보고서는 편집할 수 없습니다..." which the
    client must map to FinalizedReadOnlyError. If the backend permits owner
    edits on finalized reports (policy drift), we skip with documentation.
    """
    rid = e2e_report["id"]
    ra_client.publish_report(rid)
    try:
        try:
            report_ops.update_blocks(
                ra_client, rid, title="[rs-e2e] finalized edit probe",
            )
        except FinalizedReadOnlyError as exc:
            assert exc.status_code == 403
            assert "발행된 보고서" in str(exc), (
                f"typed error raised but Korean message lost: {exc}"
            )
            return
        except RuntimeError as exc:
            # edit_lock wraps non-transient ApiError into RuntimeError — the
            # lock endpoint itself may reject finalized reports.
            if isinstance(exc.__cause__, FinalizedReadOnlyError):
                return
            raise
        pytest.skip(
            "backend allowed the owner to edit a finalized report — cannot "
            "trigger a Korean 403 with a single service account (needs a "
            "second account or a foreign author-locked report)"
        )
    finally:
        try:
            ra_client.unpublish_report(rid)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 7-9. mounts (success OR documented typed 403 — both validate the wiring)
# --------------------------------------------------------------------------- #
@pytest.mark.e2e
def test_e2e_mount_list_unmount(ra_client, e2e_report):
    rid = e2e_report["id"]
    slug = settings.report_api_workspace_slug
    if not _mount_or_validate_403(ra_client, rid, slug):
        return  # typed 403 path validated
    assert slug in _mount_slugs(ra_client, rid), (
        f"mount succeeded but GET /mounts?report_id={rid} does not list {slug!r}"
    )
    try:
        report_ops.unmount_report(ra_client, rid, slug)
    except ApiError as exc:
        # Non-managers may mount but not unmount once accepted — the typed
        # 403 still proves DELETE /mounts/{id}/{slug} path + detection.
        _assert_mount_forbidden(exc)
        return
    assert slug not in _mount_slugs(ra_client, rid)


@pytest.mark.e2e
def test_e2e_purge_blocked_while_mounted(ra_client, e2e_report):
    """Hard DELETE on a mounted report must 409 (report_still_mounted)."""
    rid = e2e_report["id"]
    slug = settings.report_api_workspace_slug
    if not _mount_or_validate_403(ra_client, rid, slug):
        return
    try:
        with pytest.raises(ApiError) as ei:
            report_ops.delete_report(ra_client, rid)
        exc = ei.value
        assert exc.status_code == 409, (
            f"DELETE /reports/{rid} while mounted returned "
            f"{exc.status_code}, expected 409 report_still_mounted"
        )
        if ReportStillMountedError is not None:
            assert isinstance(exc, ReportStillMountedError), (
                f"409 not detected as ReportStillMountedError "
                f"(got {type(exc).__name__}) — envelope-code wiring gap"
            )
    finally:
        try:
            report_ops.unmount_report(ra_client, rid, slug)
        except Exception:
            pass


@pytest.mark.e2e
def test_e2e_trash_succeeds_while_mounted(ra_client, e2e_report):
    """Trash (soft delete) must SUCCEED while mounted — RA design keeps the
    board copies; only hard purge is blocked (see previous test)."""
    rid = e2e_report["id"]
    slug = settings.report_api_workspace_slug
    if not _mount_or_validate_403(ra_client, rid, slug):
        return
    try:
        trashed = ra_client.trash_report(rid)
        assert trashed.get("deleted_at"), (
            "trash while mounted should succeed with deleted_at set "
            f"(board copies preserved per RA design) — got {trashed!r}"
        )
        restored = ra_client.restore_report(rid)
        assert not restored.get("deleted_at"), (
            f"restore did not clear deleted_at: {restored!r}"
        )
    finally:
        try:
            ra_client.restore_report(rid)  # in case an assert fired mid-flow
        except Exception:
            pass
        try:
            report_ops.unmount_report(ra_client, rid, slug)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 10. trash / restore
# --------------------------------------------------------------------------- #
@pytest.mark.e2e
def test_e2e_trash_restore(ra_client, e2e_report):
    rid = e2e_report["id"]
    trashed = ra_client.trash_report(rid)
    assert trashed.get("deleted_at"), f"trash did not set deleted_at: {trashed!r}"
    restored = ra_client.restore_report(rid)
    assert not restored.get("deleted_at"), (
        f"restore did not clear deleted_at: {restored!r}"
    )


# --------------------------------------------------------------------------- #
# 11. Korean payload round-trip (mojibake)
# --------------------------------------------------------------------------- #
@pytest.mark.e2e
def test_e2e_korean_payload_roundtrip(ra_client, e2e_report):
    """Korean key_value keys + Korean rich_text body must survive
    normalize -> PATCH -> GET byte-identical (no mojibake).

    Note on shape: the key_value content schema constrains item keys to
    ``^[a-z][a-z0-9_]*$``, so the pipeline routes Korean KEYS through the
    items[]/rich_text-fallback path by design. This test asserts ENCODING
    integrity (every Korean string present verbatim after the round-trip),
    not the storage shape.
    """
    ko_key = "프로젝트"
    ko_key_value = "한글 키 라운드트립 값"
    ko_value = "리포트아카이브 — 한글 값 검증 ㈜①②③"
    ko_body = "한글 본문입니다. 인코딩 무결성(모지바케 없음)을 검증하는 문장 — 별표 ★ 포함."

    rid = e2e_report["id"]
    fetched = report_ops.fetch_report(ra_client, rid)
    page = (fetched.get("pages") or [{}])[0]
    tpl = ra_client.fetch_template(page["template_id"], page.get("template_version"))

    result = orchestrator.normalize_report(
        tpl,
        {"meta": {"sprint": ko_value, ko_key: ko_key_value}, "summary": ko_body},
        schemas.load(),
    )
    assert result.failed_count == 0, (
        f"Korean draft failed normalization: "
        f"{[(b.block_id, b.detail) for b in result.blocks if b.status == 'failed']}"
    )
    report_ops.update_blocks(
        ra_client, rid,
        block_patches=result.content,
        add_extra_blocks=result.extra_blocks,
    )

    after = report_ops.fetch_report(ra_client, rid)
    text = json.dumps(after.get("pages"), ensure_ascii=False)
    for s in (ko_key, ko_key_value, ko_value, ko_body):
        assert s in text, f"Korean string lost or mojibake'd after round-trip: {s!r}"


# --------------------------------------------------------------------------- #
# 12. dry_run revise — no side effects
# --------------------------------------------------------------------------- #
@pytest.mark.e2e
def test_e2e_dry_run_no_side_effect(ra_client, e2e_report):
    """report_revise dry_run=True must not change the live report.

    Needs an LLM provider (the revise prompt is real) — skipped when none
    is configured.
    """
    from report_skill import llm

    if not llm.is_configured():
        pytest.skip("no LLM provider configured (ANTHROPIC_API_KEY / "
                    "OPENAI_API_KEY / OLLAMA_BASE_URL / SKILL_LLM_PROVIDER)")

    from report_skill import mcp_server

    rid = e2e_report["id"]
    before = report_ops.fetch_report(ra_client, rid)
    out = mcp_server._do_report_revise({
        "report_id": rid,
        "instruction": "요약을 한 문장으로 더 간결하게 다듬어 주세요.",
        "block_ids": ["summary"],
        "dry_run": True,
    })
    # Two valid shapes: a dry-run patch preview, or "no blocks changed".
    assert out.get("dry_run") is True or out.get("patched") == [], (
        f"unexpected report_revise output shape: {out!r}"
    )
    after = report_ops.fetch_report(ra_client, rid)
    assert int(after["revision"]) == int(before["revision"]), (
        f"dry_run=True mutated the report: revision "
        f"{before['revision']} -> {after['revision']}"
    )
    assert (after.get("pages") or [{}])[0].get("content") == \
           (before.get("pages") or [{}])[0].get("content"), (
        "dry_run=True changed page content"
    )
