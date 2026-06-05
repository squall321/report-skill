"""Edit existing reports — PATCH /reports/{id} flows.

Operations:

- `update_blocks` — REPLACE specific blocks' content on a page (other blocks
  preserved).
- `append_to_blocks` — MERGE incoming content into existing blocks via
  per-widget-type append strategy (`merge.py`). Used for "add another
  milestone event", "append a table row", etc.
- `add_page` — append a brand new page (different template allowed).
- `replace_page` — wholesale-replace one page.
- `delete_report`

All write ops go through the lock-acquire context manager. Append uses
optimistic concurrency via `expected_revision` so two parallel processes
can safely race: the loser detects the revision bump, re-fetches, re-merges,
and retries up to `max_retries` times before giving up with a clear error.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from report_skill import merge as merge_mod
from report_skill.adapters import ADAPTERS
from report_skill.adapters.base import NormalizeError
from report_skill.client import ApiError, ReportArchiveClient


class RevisionConflict(RuntimeError):
    """Raised when expected_revision retries are exhausted."""


@contextmanager
def edit_lock(client: ReportArchiveClient, report_id: int) -> Iterator[None]:
    """Acquire the report's edit lock, do work, release. Required before PATCH.

    Retries on transient backend failures (HTTP 5xx + connection errors),
    which happen when multiple parallel skill processes hit the lock table
    simultaneously. Uses exponential backoff with jitter to spread out
    contention. After retries are exhausted, raises RuntimeError.

    Concurrency model:
      - For the service account, `?force=true` is fine — bot always wins
        over other bots / leftover stale CLI locks.
      - In real multi-user setups, drop `force=true` and surface 409
        (lock_held_by_other) so the user can decide whether to take over.
    """
    import random
    max_acquire_retries = 5
    for attempt in range(max_acquire_retries + 1):
        try:
            client.post(f"/reports/{report_id}/lock?force=true")
            break
        except ApiError as e:
            transient = e.status_code >= 500 or e.status_code == 0
            if transient and attempt < max_acquire_retries:
                backoff = 0.15 * (2 ** attempt) + random.uniform(0, 0.2)
                time.sleep(backoff)
                continue
            raise RuntimeError(
                f"could not acquire edit lock on report {report_id} "
                f"(after {attempt + 1} attempt(s)): {e}"
            ) from e
    try:
        yield
    finally:
        try:
            client._request("DELETE", f"/reports/{report_id}/lock")
        except ApiError:
            pass


def fetch_report(client: ReportArchiveClient, report_id: int) -> dict:
    """GET /reports/{id}. Returns the full Report record."""
    return client.get(f"/reports/{report_id}")


def update_blocks(
    client: ReportArchiveClient,
    report_id: int,
    *,
    page_index: int = 0,
    block_patches: Optional[dict[str, Any]] = None,
    add_extra_blocks: Optional[list[dict]] = None,
    blocks_order: Optional[list] = None,
    title: Optional[str] = None,
    phase: Optional[str] = None,
    lifecycle: Optional[str] = None,
    status: Optional[str] = None,   # legacy alias, mapped to phase
    tags: Optional[list[str]] = None,
    # v0.5.0 — related-info fields (empty list clears all)
    collab_workspace_slugs: Optional[list[str]] = None,
    entity_ids: Optional[list[int]] = None,
    report_type_id: Optional[int] = None,
    # v0.5.0 — page-level layout / slide / rich-text-prefix settings
    page_width_px: Optional[int] = None,
    page_gap_px: Optional[int] = None,
    page_blend_blocks: Optional[bool] = None,
    page_slide_guide: Optional[bool] = None,
    page_slide_ratio: Optional[str] = None,
    page_slide_ratio_custom_w: Optional[int] = None,
    page_slide_ratio_custom_h: Optional[int] = None,
    page_rich_text_prefix_d0: Optional[str] = None,
    page_rich_text_prefix_d1: Optional[str] = None,
    page_rich_text_prefix_d2: Optional[str] = None,
) -> dict:
    """Patch specific blocks of an existing report.

    - `block_patches`: {block_id: new_content_dict} — overwrites those keys
      in page[page_index].content, leaving every other block untouched.
    - `add_extra_blocks`: each {id, type, props, content?} gets appended to
      that page's extra_blocks array AND its content gets stored under the
      id in content.
    - `blocks_order` (CR-11): if provided, REPLACES the page's blocks_order
      verbatim. Use this when the caller wants explicit control over render
      order. When None, new extras (from add_extra_blocks) are merged into
      the existing blocks_order at the end so they actually render.
    - `title`/`status`/`tags`: optional top-level field updates.
    - `collab_workspace_slugs` / `entity_ids` / `report_type_id`: related-
      info tagging. For the two list fields, passing `[]` clears all
      existing values; passing `None` (default) leaves them untouched.
    - `page_*` (10 fields): page-level layout, slide-guide, slide-ratio and
      rich-text-prefix glyphs. Each is omitted from the PATCH body when
      None so the server keeps the current value.

    Returns the updated Report record.
    """
    current = fetch_report(client, report_id)
    pages = list(current.get("pages", []))
    if not pages:
        raise ValueError(f"report {report_id} has no pages")
    if page_index < 0 or page_index >= len(pages):
        raise IndexError(f"page_index {page_index} out of range (report has {len(pages)} pages)")

    page = dict(pages[page_index])
    content = dict(page.get("content", {}))

    if block_patches:
        for bid, ctn in block_patches.items():
            content[bid] = ctn

    # CR-11 — when add_extra_blocks is supplied, merge the new ids into the
    # page's existing blocks_order so the new extras actually render. The
    # backend hides anything missing from blocks_order, so without this the
    # new heading/chart/etc would never appear even though it's in content.
    new_extra_ids: list = []
    if add_extra_blocks:
        extras = list(page.get("extra_blocks") or [])
        for b in add_extra_blocks:
            extras.append({k: v for k, v in b.items()
                           if k in ("id", "type", "props", "layout")})
            if b.get("id"):
                new_extra_ids.append(b["id"])
            if "content" in b and b.get("id"):
                content[b["id"]] = b["content"]
        page["extra_blocks"] = extras

    # Explicit blocks_order override wins over the auto-merge. Otherwise we
    # auto-merge new extras into the existing order (preserves the user's
    # ordering and just appends the new ids at the end).
    if blocks_order is not None:
        page["blocks_order"] = list(blocks_order)
    elif new_extra_ids:
        from report_skill.report_builder import merge_blocks_order
        current_order = page.get("blocks_order") or []
        page["blocks_order"] = merge_blocks_order(current_order, new_extra_ids)

    page["content"] = content
    pages[page_index] = page

    body: dict = {"pages": pages}
    if title is not None:
        body["title"] = title
    # Backend renamed status→phase. Accept legacy `status` and map.
    from report_skill.report_builder import normalize_phase
    resolved_phase = normalize_phase(phase) or normalize_phase(status)
    if resolved_phase is not None:
        body["phase"] = resolved_phase
    if lifecycle is not None:
        body["lifecycle"] = lifecycle
    if tags is not None:
        body["tags"] = tags
    # v0.5.0 — related-info: lists use empty-list-clears-all semantics, so
    # include the key whenever the caller passed any list (even []). None
    # means "leave unset" so the key is omitted entirely.
    if collab_workspace_slugs is not None:
        body["collab_workspace_slugs"] = list(collab_workspace_slugs)
    if entity_ids is not None:
        body["entity_ids"] = list(entity_ids)
    if report_type_id is not None:
        body["report_type_id"] = report_type_id
    # v0.5.0 — page-level settings. Each one is an Optional scalar: include
    # only when explicitly set so the PATCH stays narrow.
    if page_width_px is not None:
        body["page_width_px"] = page_width_px
    if page_gap_px is not None:
        body["page_gap_px"] = page_gap_px
    if page_blend_blocks is not None:
        body["page_blend_blocks"] = page_blend_blocks
    if page_slide_guide is not None:
        body["page_slide_guide"] = page_slide_guide
    if page_slide_ratio is not None:
        body["page_slide_ratio"] = page_slide_ratio
    if page_slide_ratio_custom_w is not None:
        body["page_slide_ratio_custom_w"] = page_slide_ratio_custom_w
    if page_slide_ratio_custom_h is not None:
        body["page_slide_ratio_custom_h"] = page_slide_ratio_custom_h
    if page_rich_text_prefix_d0 is not None:
        body["page_rich_text_prefix_d0"] = page_rich_text_prefix_d0
    if page_rich_text_prefix_d1 is not None:
        body["page_rich_text_prefix_d1"] = page_rich_text_prefix_d1
    if page_rich_text_prefix_d2 is not None:
        body["page_rich_text_prefix_d2"] = page_rich_text_prefix_d2
    with edit_lock(client, report_id):
        return client._request("PATCH", f"/reports/{report_id}", json=body)


def add_page(
    client: ReportArchiveClient,
    report_id: int,
    *,
    template_id: str,
    template_version: int = 1,
    name: Optional[str] = None,
    content: Optional[dict] = None,
    extra_blocks: Optional[list[dict]] = None,
    template: Optional[dict] = None,
    blocks_order: Optional[list] = None,
) -> dict:
    """Append a new page to an existing report.

    The new page can use a DIFFERENT template than existing pages —
    multi-page reports legitimately mix layouts (cover + detail + appendix
    etc.). Returns the updated Report record.

    CR-11 — if `template` is supplied (the fetched template dict), the
    page's `blocks_order` is auto-computed via `compute_blocks_order` so
    empty template blocks stay hidden and heading extras float to top
    (same rules as create). Explicit `blocks_order` overrides the auto
    computation. When `template` is None, no blocks_order is set — the
    backend keeps its old default (all template blocks visible).
    """
    current = fetch_report(client, report_id)
    pages = list(current.get("pages", []))

    new_page: dict = {
        "template_id": template_id,
        "template_version": template_version,
        "content": content or {},
    }
    if name is not None:
        new_page["name"] = name
    if extra_blocks:
        new_page["extra_blocks"] = extra_blocks

    if template is not None or blocks_order is not None:
        from report_skill.report_builder import compute_blocks_order
        new_page["blocks_order"] = compute_blocks_order(
            template or {}, content or {}, extra_blocks or [],
            explicit=blocks_order,
        )

    pages.append(new_page)
    with edit_lock(client, report_id):
        return client._request("PATCH", f"/reports/{report_id}", json={"pages": pages})


def replace_page(
    client: ReportArchiveClient,
    report_id: int,
    page_index: int,
    *,
    template_id: Optional[str] = None,
    template_version: Optional[int] = None,
    name: Optional[str] = None,
    content: Optional[dict] = None,
    extra_blocks: Optional[list[dict]] = None,
) -> dict:
    """Wholesale-replace one page. Use when blocks have been added/removed
    rather than just had their content updated."""
    current = fetch_report(client, report_id)
    pages = list(current.get("pages", []))
    if not pages:
        raise ValueError(f"report {report_id} has no pages")
    if page_index < 0 or page_index >= len(pages):
        raise IndexError(f"page_index {page_index} out of range")

    existing = pages[page_index]
    pages[page_index] = {
        "template_id": template_id or existing["template_id"],
        "template_version": template_version or existing["template_version"],
        "name": name if name is not None else existing.get("name"),
        "content": content if content is not None else existing.get("content", {}),
        **({"extra_blocks": extra_blocks} if extra_blocks is not None
           else ({"extra_blocks": existing["extra_blocks"]} if existing.get("extra_blocks") else {})),
    }
    with edit_lock(client, report_id):
        return client._request("PATCH", f"/reports/{report_id}", json={"pages": pages})


def delete_report(client: ReportArchiveClient, report_id: int) -> dict:
    """DELETE /reports/{id}. No edit lock needed — delete is unconditional."""
    return client._request("DELETE", f"/reports/{report_id}")


# --------------------------------------------------------------------------- #
# Mount / unmount — promote personal reports to org-board workspaces.
# Backend policy: POST /reports lands every new report in the user's
# `personal-<id>` workspace. To make a report visible on a department
# board (dx, etc), it must be MOUNTED there as a separate, deliberate
# step. The relationship is many-to-many — one report can be mounted on
# multiple boards, each with its own folder + edit_policy.
# --------------------------------------------------------------------------- #
def list_mounts(client: ReportArchiveClient, report_id: int) -> list[dict]:
    """GET /mounts?report_id=N — every board the report is visible on."""
    data = client.get("/mounts", params={"report_id": report_id})
    if isinstance(data, dict) and "items" in data:
        return data["items"]
    return data if isinstance(data, list) else []


def mount_report(
    client: ReportArchiveClient,
    report_id: int,
    *,
    workspace_slugs: list[str],
    edit_policy: str = "default",
    note: str = "",
    folder_id: Optional[int] = None,
) -> list[dict]:
    """POST /mounts — promote `report_id` to one or more org boards.

    `edit_policy`: `default` (author + board lead) / `owner_only`
    (author only) / `coauthor` (all board members can edit).
    Idempotent per-board: already-mounted targets get silently skipped.
    Returns the list of NEWLY-created mounts (may be empty if all
    requested boards already had a mount).
    """
    body: dict = {
        "report_id": report_id,
        "workspace_slugs": list(workspace_slugs),
        "edit_policy": edit_policy,
        "note": note,
    }
    if folder_id is not None:
        body["folder_id"] = folder_id
    data = client.post("/mounts", json=body)
    if isinstance(data, dict) and "items" in data:
        return data["items"]
    return data if isinstance(data, list) else []


def unmount_report(
    client: ReportArchiveClient,
    report_id: int,
    workspace_slug: str,
) -> dict:
    """DELETE /mounts/{report_id}/{workspace_slug} — remove one mount."""
    return client._request("DELETE", f"/mounts/{report_id}/{workspace_slug}")


def set_mount_folder(
    client: ReportArchiveClient,
    report_id: int,
    workspace_slug: str,
    *,
    folder_id: Optional[int],
) -> dict:
    """PUT /mounts/{report_id}/{workspace_slug}/folder — move the mount.

    `folder_id=None` clears the folder (mount moves to the workspace root).
    Permission: report owner OR mount creator OR workspace admin/manager.
    Returns {report_id, workspace_slug, folder_id}.
    """
    return client.set_mount_folder(report_id, workspace_slug, folder_id=folder_id)


def set_mount_edit_policy(
    client: ReportArchiveClient,
    report_id: int,
    workspace_slug: str,
    *,
    edit_policy: str,
) -> dict:
    """PUT /mounts/{report_id}/{workspace_slug}/edit-policy — update policy.

    `edit_policy` must be one of `default` / `owner_only` / `coauthor`.
    Owner-only (Phase 3). Returns {report_id, workspace_slug, edit_policy}.
    """
    return client.set_mount_edit_policy(
        report_id, workspace_slug, edit_policy=edit_policy
    )


def remove_items(
    client: ReportArchiveClient,
    report_id: int,
    *,
    block_id: str,
    page_index: int = 0,
    match: dict[str, Any],
) -> tuple[dict, int]:
    """Remove items from a list-type block (milestone, bulleted_list, etc).

    `match` is a dict of {field_name: value_to_match}. For milestone:
        {"date": "2026-08-01"} → removes all items with that date
        {"label": "..."} → removes by label
        {"date": "...", "label": "..."} → only items matching BOTH
    For bulleted_list:
        {"text": "..."} or {"item": "..."}

    Returns (updated_report, n_removed). Uses edit_lock + revision check.
    """
    current = fetch_report(client, report_id)
    pages = current.get("pages") or []
    if page_index >= len(pages):
        raise IndexError(f"page_index {page_index} out of range")
    page = pages[page_index]
    content = page.get("content") or {}
    block = content.get(block_id)
    if not isinstance(block, dict):
        raise KeyError(f"block '{block_id}' not found or has no content")

    n_removed = 0
    new_block = dict(block)

    # List-of-dicts case (milestone, table.rows, chart.rows, etc)
    if "items" in block and isinstance(block["items"], list):
        kept = []
        for it in block["items"]:
            if isinstance(it, dict):
                if all(it.get(k) == v for k, v in match.items()):
                    n_removed += 1
                    continue
                kept.append(it)
            elif "text" in match and it == match["text"]:
                n_removed += 1
            elif "item" in match and it == match["item"]:
                n_removed += 1
            else:
                kept.append(it)
        new_block["items"] = kept
    elif "rows" in block and isinstance(block["rows"], list):
        kept = []
        for r in block["rows"]:
            if isinstance(r, dict) and all(r.get(k) == v for k, v in match.items()):
                n_removed += 1
                continue
            kept.append(r)
        new_block["rows"] = kept
    else:
        raise ValueError(
            f"block '{block_id}' is not a list-type block "
            f"(no 'items' or 'rows'); use update to replace its content"
        )

    if n_removed == 0:
        return current, 0

    updated_pages = _patch_page_content(pages, page_index, {block_id: new_block})
    body = {"expected_revision": current.get("revision"), "pages": updated_pages}
    with edit_lock(client, report_id):
        updated = client._request("PATCH", f"/reports/{report_id}", json=body)
    return updated, n_removed


# --------------------------------------------------------------------------- #
# Append (merge incoming content into existing blocks)
# --------------------------------------------------------------------------- #
def _index_block_types(report: dict, page_index: int) -> dict[str, str]:
    """Build {block_id: widget_type} for a page, including extra_blocks."""
    out: dict[str, str] = {}
    pages = report.get("pages") or []
    if page_index >= len(pages):
        return out
    page = pages[page_index]
    # extra_blocks first so template blocks override (defensive — usually disjoint)
    for b in page.get("extra_blocks") or []:
        if isinstance(b, dict) and b.get("id") and b.get("type"):
            out[b["id"]] = b["type"]
    # template blocks have to come from a fetched template
    return out


def _merge_one_block(
    widget_type: str,
    existing_content: Optional[dict],
    raw_input: Any,
    block_props: dict,
) -> dict:
    """Adapter-normalize the raw input, then call merge_block_content."""
    adapter = ADAPTERS.get(widget_type)
    if adapter is None:
        raise ValueError(f"no adapter for widget type '{widget_type}'")
    new_content = adapter.normalize(raw_input, block_props)
    return merge_mod.merge_block_content(widget_type, existing_content, new_content)


def append_to_blocks(
    client: ReportArchiveClient,
    report_id: int,
    *,
    block_appends: dict[str, Any],
    page_index: int = 0,
    template_blocks_by_id: Optional[dict[str, dict]] = None,
    max_retries: int = 3,
    retry_delay: float = 0.4,
) -> dict:
    """Merge `block_appends` (block_id → anything-ish raw input) into the
    matching blocks' existing content using per-widget append strategies.

    Uses optimistic locking (`expected_revision`) — on a 409 revision_mismatch
    response, re-fetches, re-merges with the latest state, and retries up to
    `max_retries` times. Raises `RevisionConflict` after exhaustion.

    `template_blocks_by_id`: optional pre-fetched template block defs
    {block_id: {type, props}}. When omitted, fetched on-demand from the
    report's first template per page.
    """
    last_err: Optional[ApiError] = None
    for attempt in range(max_retries + 1):
        report = fetch_report(client, report_id)
        pages = report.get("pages") or []
        if page_index >= len(pages):
            raise IndexError(f"page_index {page_index} out of range")

        # Resolve widget types per block id: from extra_blocks + the page's template.
        type_map = _index_block_types(report, page_index)
        page = pages[page_index]
        if template_blocks_by_id is None:
            tpl = client.fetch_template(page["template_id"], page["template_version"])
            tpl_blocks = (tpl.get("schema") or {}).get("blocks") or []
            template_blocks_by_id = {b["id"]: b for b in tpl_blocks if isinstance(b, dict)}
        for bid, bdef in template_blocks_by_id.items():
            if bid not in type_map:
                type_map[bid] = bdef.get("type", "")

        # Merge each requested block.
        patches: dict[str, dict] = {}
        existing_content_map = page.get("content") or {}
        for bid, raw in block_appends.items():
            wtype = type_map.get(bid)
            if not wtype:
                raise KeyError(f"block id '{bid}' not found on page {page_index}")
            props = (template_blocks_by_id.get(bid, {}) or {}).get("props", {}) or {}
            existing = existing_content_map.get(bid)
            try:
                patches[bid] = _merge_one_block(wtype, existing, raw, props)
            except NormalizeError as e:
                raise ValueError(f"normalize for '{bid}' ({wtype}) failed: {e}")

        # Issue PATCH with expected_revision from the same fetch.
        body: dict = {
            "expected_revision": report.get("revision"),
            "pages": _patch_page_content(pages, page_index, patches),
        }
        try:
            with edit_lock(client, report_id):
                return client._request("PATCH", f"/reports/{report_id}", json=body)
        except ApiError as e:
            last_err = e
            code = ""
            if isinstance(e.payload, dict):
                errs = e.payload.get("errors") or []
                if errs and isinstance(errs[0], dict):
                    code = errs[0].get("code", "")
            if code == "revision_mismatch":
                if attempt < max_retries:
                    time.sleep(retry_delay * (2 ** attempt))  # exponential backoff
                    continue
                # Exhausted retries on a true revision conflict — raise our
                # typed exception so callers can catch it specifically.
                raise RevisionConflict(
                    f"append_to_blocks: gave up after {max_retries + 1} attempt(s) "
                    f"of revision-mismatch on report {report_id}: {e}"
                ) from e
            # Non-revision error — propagate as-is.
            raise
    # Unreachable in normal flow (the for loop either returns or raises) but
    # keeps mypy/pyright happy by guaranteeing the function returns or raises.
    raise RevisionConflict(
        f"append_to_blocks: exhausted retries without success: {last_err}"
    )


def _patch_page_content(pages: list[dict], page_index: int, patches: dict[str, dict]) -> list[dict]:
    """Return a new pages list with `patches` applied to page[page_index].content."""
    new_pages = [dict(p) for p in pages]
    p = new_pages[page_index]
    content = dict(p.get("content") or {})
    content.update(patches)
    p["content"] = content
    new_pages[page_index] = p
    return new_pages
