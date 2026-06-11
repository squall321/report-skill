"""Live-backend E2E for the v0.15.0 surface (RA v0.26.0~v0.29.0 parity).

Covers the four families this release added — exactly the contract-drift
shapes a fake client can never catch:

  * table `header` (multi-row/merged) + `expanded` content fields survive
    the create round-trip (server content_schema accepts what the adapter
    passes through)
  * per-board mount note PUT path + response shape
  * composite preset (종합보고 양식) full lifecycle:
    create → list → instantiate → update → delete
  * report copy mode="summary" creates the copy AND the system summary link

Same conventions as test_lifecycle.py: success OR a documented typed 403
both validate the wiring; cleanup is best-effort and individually swallowed.
"""
from __future__ import annotations

import uuid

import pytest

from report_skill import orchestrator, report_builder, report_ops, schemas
from report_skill.client import (
    ApiError,
    CompositePresetPermissionError,
    MountForbiddenError,
    OutOfWorkspaceScopeError,
)
from report_skill.config import settings

pytestmark = pytest.mark.e2e


def _purge_report(ra_client, rid) -> None:
    """Best-effort unmount-all → trash → hard DELETE (mirrors conftest)."""
    try:
        for m in report_ops.list_mounts(ra_client, rid):
            slug = (m or {}).get("workspace_slug") or (m or {}).get("slug")
            if slug:
                try:
                    report_ops.unmount_report(ra_client, rid, slug)
                except Exception:
                    pass
    except Exception:
        pass
    for step in (ra_client.trash_report, lambda r: report_ops.delete_report(ra_client, r)):
        try:
            step(rid)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 1. table header + expanded round-trip (RA 795c60c / 8b5788f)
# --------------------------------------------------------------------------- #
def test_e2e_table_header_expanded_roundtrip(ra_client):
    """The adapter passes `header`/`expanded` through untouched; the server
    must accept them (content_schema) and return them verbatim on GET."""
    header = {
        "row_count": 2,
        "cells": {
            "0::issue": {"text": "이슈 상세", "bg": "slate", "fg": "ink"},
            "1::severity": {"text": "심각도(병합)"},
        },
        "merges": [{"r": 0, "c": 0, "rs": 1, "cs": 2}],
    }
    blocks = {
        "meta": {"sprint": "rs-e2e-v0150"},
        "summary": "v0.15.0 e2e — table header/expanded round-trip fixture.",
        "progress": ["header + expanded passthrough"],
        "next_week": ["cleanup in finally"],
        "issues": {
            "rows": [
                {"issue": "헤더 병합 검증", "severity": "보통", "owner": "rs-e2e"},
            ],
            "header": header,
            "expanded": True,
        },
    }
    tpl = ra_client.fetch_template("weekly-dev")
    snap = schemas.load()
    result = orchestrator.normalize_report(tpl, blocks, snap)
    assert result.failed_count == 0, (
        f"normalize failed: "
        f"{[(b.block_id, b.detail) for b in result.blocks if b.status == 'failed']}"
    )
    issues_out = result.content.get("issues") or {}
    assert issues_out.get("header") == header, (
        f"adapter dropped/altered header: {issues_out.get('header')!r}"
    )
    assert issues_out.get("expanded") is True, "adapter dropped expanded"

    payload = report_builder.build_create_payload(
        tpl, result.content,
        title=f"[rs-e2e] v0150 header {uuid.uuid4().hex[:8]}",
        tags=["rs-e2e"],
        extra_blocks=result.extra_blocks,
    )
    created = ra_client.create_report(payload)
    rid = created.get("id")
    assert rid is not None, f"POST /reports returned no id: {created!r}"
    try:
        fetched = report_ops.fetch_report(ra_client, rid)
        content = (fetched.get("pages") or [{}])[0].get("content") or {}
        got = content.get("issues") or {}
        assert got.get("header") == header, (
            f"server did not round-trip header: {got.get('header')!r}"
        )
        assert got.get("expanded") is True, (
            f"server did not round-trip expanded: {got.get('expanded')!r}"
        )
    finally:
        _purge_report(ra_client, rid)


# --------------------------------------------------------------------------- #
# 2. mount note (RA b435a0f)
# --------------------------------------------------------------------------- #
def test_e2e_mount_set_note(ra_client, e2e_report):
    """PUT /mounts/{rid}/{slug}/note — path + body + response shape."""
    rid = e2e_report["id"]
    slug = settings.report_api_workspace_slug
    try:
        report_ops.mount_report(ra_client, rid, workspace_slugs=[slug])
    except ApiError as exc:
        if isinstance(exc, MountForbiddenError) or exc.status_code == 403:
            pytest.skip(f"account cannot mount to {slug!r} — note path untestable")
        raise

    note = "v0.15.0 e2e 게시 메모 — safe to delete"
    try:
        row = ra_client.set_mount_note(rid, slug, note=note)
        assert isinstance(row, dict), f"unexpected response: {row!r}"
        assert row.get("note") == note, f"note not echoed back: {row!r}"
        assert row.get("workspace_slug") == slug

        # '' clears — exercise the second contract branch too.
        cleared = ra_client.set_mount_note(rid, slug, note="")
        assert (cleared.get("note") or "") == "", f"clear failed: {cleared!r}"
    finally:
        try:
            report_ops.unmount_report(ra_client, rid, slug)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 3. composite preset lifecycle (RA c5c57ca)
# --------------------------------------------------------------------------- #
def test_e2e_composite_preset_lifecycle(ra_client):
    """create → list → new-composite → update → delete, with cleanup of the
    two composites it touches. A typed scope/permission 403 also validates
    the wiring (documented alternative outcome)."""
    tag = uuid.uuid4().hex[:8]
    src_id = preset_id = inst_id = None
    try:
        try:
            src = ra_client.create_composite(
                title=f"[rs-e2e] preset source {tag}",
                kind="theme",
                description="v0.15.0 e2e — composite preset source",
            )
        except OutOfWorkspaceScopeError as exc:
            pytest.skip(f"account cannot create composites here: {exc}")
        src_id = src.get("id")
        assert src_id is not None, f"composite create returned no id: {src!r}"

        preset = ra_client.create_composite_preset(
            src_id,
            name=f"[rs-e2e] 양식 {tag}",
            description="v0.15.0 e2e preset",
            groups=["개발", "품질"],
        )
        preset_id = preset.get("id")
        assert preset_id is not None, f"preset create returned no id: {preset!r}"
        assert preset.get("groups") == ["개발", "품질"], preset

        listed = ra_client.list_composite_presets()
        assert any(p.get("id") == preset_id for p in listed), (
            f"preset {preset_id} missing from GET /composite-presets"
        )

        inst = ra_client.new_composite_from_preset(
            preset_id,
            workspace_slug=settings.report_api_workspace_slug,
            title=f"[rs-e2e] from-preset {tag}",
            kind="theme",
        )
        composite = inst.get("composite") or {}
        inst_id = composite.get("id")
        assert inst_id is not None, f"instantiate returned no composite id: {inst!r}"
        assert inst.get("seed_groups") == ["개발", "품질"], (
            f"seed_groups did not carry the preset skeleton: {inst!r}"
        )

        try:
            renamed = ra_client.update_composite_preset(
                preset_id, name=f"[rs-e2e] 양식 {tag} (renamed)")
            assert renamed.get("name", "").endswith("(renamed)"), renamed
        except CompositePresetPermissionError:
            pass  # non-manager non-creator path — typed detection validated
    finally:
        if preset_id is not None:
            try:
                ra_client.delete_composite_preset(preset_id)
            except Exception:
                pass
        for cid in (inst_id, src_id):
            if cid is not None:
                try:
                    ra_client.delete_composite(cid)
                except Exception:
                    pass


# --------------------------------------------------------------------------- #
# 4. copy mode="summary" (RA ef4e441 + p32-p34)
# --------------------------------------------------------------------------- #
def test_e2e_copy_summary_creates_link(ra_client, e2e_report):
    rid = e2e_report["id"]
    copy_id = None
    try:
        copied = ra_client.copy_report(
            rid, title=f"[rs-e2e] 요약본 {uuid.uuid4().hex[:8]}", mode="summary")
        copy_id = copied.get("id")
        assert copy_id is not None, f"summary copy returned no id: {copied!r}"

        # The system link is only exposed via GET /reports/{id}/links
        # (NOT embedded in the report detail) — which is exactly why this
        # release added list_report_links. Direction after p33: 원본 → 요약본.
        links = ra_client.list_report_links(rid)
        summary_links = [lk for lk in links if (lk or {}).get("kind") == "summary"]
        assert summary_links, (
            f"no kind='summary' link on the source after summary copy; "
            f"saw: {links!r}"
        )
    finally:
        if copy_id is not None:
            _purge_report(ra_client, copy_id)
