"""Live-backend E2E gate + fixtures.

WHY THIS SUITE EXISTS
=====================
All 479 unit/integration tests run against fake clients. The gap families
that escaped to production over 12 releases — endpoint path typos, body
field-name drift, Korean error-string detection misses, schema mismatches —
are exactly the ones a fake can never catch. This suite issues REAL HTTP
calls against a running ReportArchive backend.

HOW TO RUN
==========
``pyproject.toml`` pins ``addopts = "-m 'not e2e'"``, so a plain ``pytest``
run DESELECTS every test in this directory. Running the suite requires BOTH:

  1. ``RS_E2E=1`` in the environment, and
  2. an explicit marker override on the command line (pytest uses the LAST
     ``-m`` value, and addopts are prepended — so a trailing ``-m e2e`` wins):

         RS_E2E=1 pytest tests/e2e -m e2e

Failure modes are graceful by design:
  * ``RS_E2E`` unset/!=1  -> every e2e item is collected but SKIPPED
    (``pytest_collection_modifyitems`` below).
  * ``RS_E2E=1`` but the backend is down / .env incomplete -> the
    session-scoped ``ra_client`` health probe skips each test at setup.
The suite must never FAIL merely because the live environment is absent.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

_E2E_DIR = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
# Collection gate — RS_E2E=1 required
# --------------------------------------------------------------------------- #
def pytest_collection_modifyitems(config, items):
    """Skip every test under tests/e2e/ unless RS_E2E=1.

    NOTE: this hook receives ALL collected items (the whole repo when run
    from the root), not just this directory's — the path filter below is
    load-bearing. Without it a root-level ``pytest -m e2e``-style override
    would skip the entire 479-test suite too.
    """
    if os.environ.get("RS_E2E") == "1":
        return
    skip = pytest.mark.skip(
        reason="RS_E2E not set — live-backend e2e suite disabled "
               "(run: RS_E2E=1 pytest tests/e2e -m e2e)"
    )
    for item in items:
        try:
            in_e2e = Path(str(item.fspath)).resolve().is_relative_to(_E2E_DIR)
        except (OSError, ValueError):
            in_e2e = False
        if in_e2e:
            item.add_marker(skip)


# --------------------------------------------------------------------------- #
# Health probe — session-scoped real client
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def ra_client():
    """Real ``ReportArchiveClient``, logged in once per session.

    Skips (never fails) when the backend is unreachable or the .env is
    incomplete. ``SystemExit`` is included because the lazy settings proxy
    exits(2) on missing REPORT_API_* vars instead of raising ValidationError.
    """
    from report_skill.client import ReportArchiveClient

    try:
        client = ReportArchiveClient()
    except (Exception, SystemExit) as exc:  # noqa: BLE001 — gate, not logic
        pytest.skip(f"backend unreachable (client init failed): {exc}")
    try:
        client.login()
    except (Exception, SystemExit) as exc:  # noqa: BLE001 — gate, not logic
        client.close()
        pytest.skip(f"backend unreachable: {exc}")
    yield client
    client.close()


# --------------------------------------------------------------------------- #
# Disposable report fixture
# --------------------------------------------------------------------------- #
# The draft the fixture posts. Mirrors the real MCP report_create pipeline
# (normalize_report -> build_create_payload -> POST /reports) so the e2e
# create exercises the same code path production traffic does.
E2E_FIXTURE_BLOCKS: dict = {
    "meta": {"sprint": "rs-e2e"},
    "summary": "E2E smoke fixture — created by tests/e2e; safe to delete.",
    "progress": ["e2e harness fixture block"],
    "next_week": ["cleanup happens in the fixture finalizer (trash + DELETE)"],
}


@pytest.fixture()
def e2e_report(ra_client) -> dict:
    """Create a throwaway weekly-dev report on the live backend; yield the
    created record (with ``_e2e_input`` attached for round-trip assertions);
    then best-effort cleanup: unmount all -> trash -> hard DELETE.

    Every cleanup step is individually swallowed — a half-failed test must
    never cascade into a teardown error.
    """
    from report_skill import orchestrator, report_builder, report_ops, schemas

    tpl = ra_client.fetch_template("weekly-dev")
    snap = schemas.load()
    result = orchestrator.normalize_report(tpl, dict(E2E_FIXTURE_BLOCKS), snap)
    assert result.failed_count == 0, (
        f"fixture draft failed normalization: "
        f"{[(b.block_id, b.detail) for b in result.blocks if b.status == 'failed']}"
    )
    payload = report_builder.build_create_payload(
        tpl, result.content,
        title=f"[rs-e2e] {uuid.uuid4().hex[:8]}",
        tags=["rs-e2e"],
        extra_blocks=result.extra_blocks,
    )
    created = ra_client.create_report(payload)
    assert isinstance(created, dict) and created.get("id") is not None, (
        f"POST /reports returned no id: {created!r}"
    )
    created["_e2e_input"] = dict(E2E_FIXTURE_BLOCKS)

    yield created

    rid = created["id"]
    # 1) unmount everywhere (purge is blocked while mounted)
    try:
        for m in report_ops.list_mounts(ra_client, rid):
            slug = (m or {}).get("workspace_slug") or (m or {}).get("slug")
            if not slug:
                continue
            try:
                report_ops.unmount_report(ra_client, rid, slug)
            except Exception:
                pass
    except Exception:
        pass
    # 2) soft delete
    try:
        ra_client.trash_report(rid)
    except Exception:
        pass
    # 3) hard delete
    try:
        report_ops.delete_report(ra_client, rid)
    except Exception:
        pass
