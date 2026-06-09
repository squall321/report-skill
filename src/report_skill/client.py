"""Thin HTTP client for the ReportArchive API.

Handles login (caches the JWT for the process lifetime), attaches the
required `Authorization` and `X-Workspace-Slug` headers, and unwraps the
standard `{success, data, message, errors}` envelope.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from report_skill.config import settings

logger = logging.getLogger(__name__)


class ApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class AuthorLockedError(ApiError):
    """Raised when the report owner has enabled author-edit-lock (403).

    Subclasses ApiError so existing `except ApiError` paths keep working.
    The MCP layer maps this to {"error":"author_locked","reason":...,"report_id":...}.
    """

    def __init__(self, reason: str = "", report_id: Optional[int] = None,
                 status_code: int = 403):
        super().__init__(
            f"author_locked: {reason}" if reason else "author_locked",
            status_code=status_code,
        )
        self.reason = reason
        self.report_id = report_id


# ---- 409 typed subclasses ------------------------------------------------ #
# RA backend returns a stable `payload.errors[0].code` for these via
# error_response(). We detect that envelope first, then fall back to
# Korean substring matching for plain HTTPException paths.


class LockHeldByOtherError(ApiError):
    """Raised when another user holds the edit lock (409, code=lock_held_by_other).

    Backend envelope: errors[0]={code, message, holder?:{user_id,user_name,...}}.
    `holder` is exposed for diagnostics.
    """

    def __init__(self, message: str = "", *, payload: Any = None,
                 holder: Optional[dict] = None, status_code: int = 409):
        super().__init__(message or "lock_held_by_other",
                         status_code=status_code, payload=payload)
        self.code = "lock_held_by_other"
        self.holder = holder


class LockNotHeldError(ApiError):
    """Raised when caller has no edit lock (409, code=lock_not_held)."""

    def __init__(self, message: str = "", *, payload: Any = None,
                 status_code: int = 409):
        super().__init__(message or "lock_not_held",
                         status_code=status_code, payload=payload)
        self.code = "lock_not_held"


class RevisionMismatchError(ApiError):
    """Raised when expected_revision != current revision (409, code=revision_mismatch).

    Callers should reload the report (to refresh revision) and retry. The
    report_ops.append_to_blocks retry loop catches this and rebases up to
    `max_retries` times before surfacing.
    """

    def __init__(self, message: str = "", *, payload: Any = None,
                 status_code: int = 409):
        super().__init__(message or "revision_mismatch",
                         status_code=status_code, payload=payload)
        self.code = "revision_mismatch"


class CompositeRevisionConflict(ApiError):
    """Raised on PATCH /composites/{id} when items[] expected_revision mismatches.

    Wrapped by the RA global error envelope (backend/app/shared/errors.py:49-54)
    into {"detail": "...", "code": "...", "errors": [...]} where
    `code == "composite_revision_mismatch"`. Caller should reload + retry.
    """

    def __init__(self, message: str = "", *, payload: Any = None,
                 composite_id: Optional[int] = None, status_code: int = 409):
        super().__init__(message or "composite_revision_mismatch",
                         status_code=status_code, payload=payload)
        self.code = "composite_revision_mismatch"
        self.composite_id = composite_id


# ---- 403 typed subclasses ------------------------------------------------ #
# Detected by Korean / ASCII substring on the response message.


class FinalizedReadOnlyError(ApiError):
    """Raised when editing a finalized (발행된) report (403).

    Backend message prefix: "발행된 보고서는 편집할 수 없습니다.".
    Caller should unpublish via report_unpublish first.
    """

    def __init__(self, message: str = "", *, payload: Any = None,
                 report_id: Optional[int] = None, status_code: int = 403):
        super().__init__(message or "finalized_read_only",
                         status_code=status_code, payload=payload)
        self.code = "finalized_read_only"
        self.report_id = report_id


class NoEditPermissionError(ApiError):
    """Raised when caller lacks edit permission (403, 편집할 권한이 없).

    Covers 3 backend variants:
      - "이 보고서를 편집할 권한이 없습니다."
      - "...편집할 권한이 없어 link 를 추가할 수 없습니다."
      - "...편집할 권한이 없어 link 를 끊을 수 없습니다."
    """

    def __init__(self, message: str = "", *, payload: Any = None,
                 report_id: Optional[int] = None, status_code: int = 403):
        super().__init__(message or "no_edit_permission",
                         status_code=status_code, payload=payload)
        self.code = "no_edit_permission"
        self.report_id = report_id


class OutOfWorkspaceScopeError(ApiError):
    """Raised when the request targets a workspace outside the caller's scope (403).

    Backend message is ASCII exact: "Out of workspace scope".
    """

    def __init__(self, message: str = "", *, payload: Any = None,
                 status_code: int = 403):
        super().__init__(message or "out_of_workspace_scope",
                         status_code=status_code, payload=payload)
        self.code = "out_of_workspace_scope"


class ShareSetupForbiddenError(ApiError):
    """Raised when the caller is not the content owner or sys admin and tries to
    add/remove a content-level share (403).

    Backend message: "공유 설정은 작성자(또는 시스템 관리자)만 변경할 수 있습니다.".
    Hits `content_share_add` / `content_share_remove` from non-owners.
    """

    def __init__(self, message: str = "", *, payload: Any = None,
                 status_code: int = 403):
        super().__init__(message or "share_setup_forbidden",
                         status_code=status_code, payload=payload)
        self.code = "share_setup_forbidden"


class BoardShareForbiddenError(ApiError):
    """Raised when the caller is not a board manager or sys admin and tries to
    add/remove a board / folder grant (403).

    Backend message: "게시판 공유는 그 게시판 매니저(또는 시스템 관리자)만 변경할 수 있습니다.".
    Hits `board_share_*` and `folder_share_*` from non-managers.
    """

    def __init__(self, message: str = "", *, payload: Any = None,
                 status_code: int = 403):
        super().__init__(message or "board_share_forbidden",
                         status_code=status_code, payload=payload)
        self.code = "board_share_forbidden"


# v0.6.0 — sentinel for update_composite tri-state semantics on nullable
# scalar fields (period_date): default = omit key from body
# (server leaves alone); explicit None = send {"key": null} so server
# clears; explicit value = send {"key": value}.
_UNSET_COMP: Any = object()


class ReportArchiveClient:
    """Synchronous httpx-based client. One instance per process is fine."""

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._user_id: Optional[int] = None
        self._http = httpx.Client(
            base_url=settings.report_api_base_url,
            timeout=httpx.Timeout(30.0, read=60.0),
        )
        try:
            # Reserved for future init steps (e.g., warm-up auth or capability
            # probe). Anything that may raise must go inside this try so the
            # httpx client is closed on partial-construction failure.
            pass
        except Exception:
            try:
                self._http.close()
            except Exception:
                logger.debug("error closing httpx client during failed init", exc_info=True)
            raise

    # ---- auth ---------------------------------------------------------- #

    def login(self) -> dict:
        """Authenticate the service account. Idempotent (re-logging overwrites)."""
        env = self._post("/auth/login", json={
            "email": settings.report_api_email,
            "password": settings.report_api_password,
        }, _no_auth=True)
        self._token = env["access_token"]
        self._user_id = env["user_id"]
        logger.info("login OK user_id=%s", self._user_id)
        return env

    def ensure_logged_in(self) -> None:
        if not self._token:
            self.login()

    # ---- public endpoints ---------------------------------------------- #

    def fetch_widgets(self) -> dict:
        """GET /widgets — returns {schema_version, widgets:[...], ref_categories:[...]}.

        Note: each widget entry contains props_schema but NOT content_schema
        (which is computed server-side as a Python function of props). Use
        the bridge script for the latter.

        v0.9.0+ (RA 074233d): the response also includes `ref_categories`,
        ordered metadata ({key, label}) the rich_text body uses for #widget
        cross-references (그림 N / 표 N / 비교표 N / 수식 N / 목록 N ...).
        """
        return self.get("/widgets")

    def list_ref_categories(self) -> list[dict]:
        """Convenience projection: GET /widgets → ref_categories.

        Returns the ordered category metadata the rich_text body uses for
        #widget references. v0.9.0+ (RA 074233d).
        """
        body = self.fetch_widgets()
        if isinstance(body, dict):
            cats = body.get("ref_categories")
            if isinstance(cats, list):
                return cats
        return []

    def fetch_templates(self, *, latest_only: bool = True) -> list[dict]:
        params = {"latest_only": "true"} if latest_only else None
        env = self.get("/templates", params=params)
        return env if isinstance(env, list) else env.get("items", env)

    def fetch_template(self, template_id: str, version: Optional[int] = None,
                       *, allow_cache: bool = False) -> dict:
        """GET a template. When `allow_cache=True`, falls back to the local
        `.skill-cache/templates/` snapshot (and then the bundled
        `data/templates/` baseline) if the API is unreachable — used by
        the offline export flow so callers can normalize/validate without
        a live server."""
        try:
            if version is not None:
                return self.get(f"/templates/{template_id}/versions/{version}")
            return self.get(f"/templates/{template_id}")
        except (ApiError, httpx.RequestError) as exc:
            if not allow_cache:
                raise
            from report_skill import catalog as _catalog
            cached = _catalog.load_cached_template(template_id, version)
            if cached is None:
                raise FileNotFoundError(
                    f"template '{template_id}' not in offline cache or "
                    f"bundled data/templates/. Either run "
                    f"`report-skill templates sync` (online), or ship a "
                    f"bundled snapshot. (origin: {exc})"
                ) from exc
            return cached

    def create_report(self, payload: dict) -> dict:
        """POST /reports — returns the created Report record."""
        return self.post("/reports", json=payload)

    # ---- mention-resolver wrappers ------------------------------------- #
    # Thin HTTP wrappers that the MCP `reports_search` / `workspaces_list` /
    # `entity_types_list` / `entities_list` tools call. The MCP layer does
    # the filtering / ranking; these just return the raw envelope-unwrapped
    # rows so a CLI or test can call them too.

    def fetch_linkable_reports(self) -> list[dict]:
        """GET /reports/linkable — returns the full system-wide pool of
        reports the current user can link to (no server-side filtering).
        Used as the search base for mention://report/<id> resolution."""
        body = self.get("/reports/linkable")
        if isinstance(body, dict) and "items" in body:
            return list(body.get("items") or [])
        return list(body or []) if isinstance(body, list) else []

    def fetch_workspaces(self) -> list[dict]:
        """GET /workspaces — returns the org/personal/virtual workspace
        catalog. Source of mention://dept/<slug> candidates after filtering
        by kind=='org'."""
        body = self.get("/workspaces")
        if isinstance(body, dict) and "items" in body:
            return list(body.get("items") or [])
        return list(body or []) if isinstance(body, list) else []

    def fetch_entity_types(self) -> list[dict]:
        """GET /entity-types — returns the entity-axis catalog
        (model_name, customer_name, …). Cheap and stable; callers should
        cache the result per-process."""
        body = self.get("/entity-types")
        if isinstance(body, dict) and "items" in body:
            return list(body.get("items") or [])
        return list(body or []) if isinstance(body, list) else []

    def fetch_entities(self, *, type_id: Optional[int] = None,
                       q: Optional[str] = None,
                       include_deprecated: bool = False,
                       limit: int = 50) -> list[dict]:
        """GET /entities — search entities by axis (`type_id`) + free text.

        Hard-caps `limit` at 200 (backend max is 500 but 200 keeps LLM
        context manageable). Pass `type_id` from a prior
        `fetch_entity_types()` call.
        """
        params: dict[str, Any] = {
            "include_deprecated": str(bool(include_deprecated)).lower(),
            "limit": max(1, min(int(limit), 200)),
        }
        if type_id is not None:
            params["type_id"] = int(type_id)
        if q:
            params["q"] = q
        body = self.get("/entities", params=params)
        if isinstance(body, dict) and "items" in body:
            return list(body.get("items") or [])
        return list(body or []) if isinstance(body, list) else []

    def download_file(self, file_id: str) -> tuple[bytes, str, str]:
        """GET /files/{file_id} — returns (bytes, filename, mime_type).

        Used by the `report dump` bundle path to round-trip media between
        ReportArchive instances. The backend serves the raw file with the
        original filename in `Content-Disposition` (RFC 6266 utf-8 form
        when the name has non-ASCII chars). We prefer the structured
        /meta endpoint for the filename + mime so we don't have to
        unparse the header ourselves.
        """
        self.ensure_logged_in()
        headers = {
            "X-Workspace-Slug": settings.report_api_workspace_slug,
            "Authorization": f"Bearer {self._token}",
        }
        meta_resp = self._http.get(f"/files/{file_id}/meta", headers=headers)
        if meta_resp.is_error:
            raise ApiError(
                f"GET /files/{file_id}/meta returned {meta_resp.status_code}",
                status_code=meta_resp.status_code,
            )
        try:
            meta_body = meta_resp.json()
        except Exception:
            raise ApiError(
                f"GET /files/{file_id}/meta returned non-JSON",
                status_code=meta_resp.status_code,
            )
        meta = (meta_body.get("data") or meta_body or {}) if isinstance(meta_body, dict) else {}

        bin_resp = self._http.get(f"/files/{file_id}", headers=headers)
        if bin_resp.is_error:
            raise ApiError(
                f"GET /files/{file_id} returned {bin_resp.status_code}",
                status_code=bin_resp.status_code,
            )
        filename = meta.get("filename") or f"{file_id}.bin"
        mime_type = meta.get("mime_type") or bin_resp.headers.get("content-type") or "application/octet-stream"
        return bin_resp.content, filename, mime_type

    def upload_file(self, path, *, mime_type: Optional[str] = None) -> dict:
        """POST /files (multipart) — returns the FileMeta dict (file_id, filename, size, mime_type, ...).

        Streams the file from disk; goes through the same auth + workspace-slug
        header path as every other request. `mime_type` is auto-detected from
        the extension when omitted.
        """
        from pathlib import Path as _Path
        import mimetypes as _mime

        p = _Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"upload source not found: {p}")
        mt = mime_type or _mime.guess_type(p.name)[0] or "application/octet-stream"

        self.ensure_logged_in()
        headers = {
            "X-Workspace-Slug": settings.report_api_workspace_slug,
            "Authorization": f"Bearer {self._token}",
        }
        with p.open("rb") as fh:
            resp = self._http.post(
                "/files",
                files={"file": (p.name, fh, mt)},
                headers=headers,
            )

        try:
            body = resp.json()
        except Exception:
            resp.raise_for_status()
            raise ApiError(
                f"POST /files returned non-JSON ({resp.status_code})",
                status_code=resp.status_code,
            )

        if isinstance(body, dict) and "success" in body:
            if body.get("success"):
                return body.get("data") or {}
            raise ApiError(
                body.get("message") or "POST /files failed",
                status_code=resp.status_code,
                payload=body,
            )
        if resp.is_error:
            raise ApiError(
                f"POST /files returned {resp.status_code}",
                status_code=resp.status_code,
                payload=body,
            )
        return body if isinstance(body, dict) else {}

    # ---- reports: copy / link / publish / types ------------------------ #

    def copy_report(
        self,
        report_id,
        *,
        title: str,
        mode: str = "full",
        folder_id: Optional[int] = None,
    ) -> dict:
        """POST /reports/{report_id}/copy — duplicate a report.

        `mode` is 'content' (blocks only) or 'full' (blocks + settings).
        The copy lands in the caller's personal workspace.
        """
        body: dict[str, Any] = {"title": title, "mode": mode}
        if folder_id is not None:
            body["folder_id"] = int(folder_id)
        return self.post(f"/reports/{report_id}/copy", json=body)

    def add_report_link(
        self,
        report_id,
        *,
        to_report_id: int,
        kind: str = "related",
        label: Optional[str] = None,
        direction: str = "outgoing",
    ) -> dict:
        """POST /reports/{report_id}/links — create a link to another report.

        `kind` defaults to 'related'; `label` becomes the link note (server
        cap 200 chars). `direction` is 'outgoing' (default — report_id → to_report_id)
        or 'incoming' (server swaps from/to so the link points the other way).
        """
        if direction not in ("outgoing", "incoming"):
            raise ValueError(
                f"direction must be 'outgoing' or 'incoming', got {direction!r}"
            )
        body: dict[str, Any] = {
            "to_report_id": int(to_report_id),
            "kind": kind,
            "direction": direction,
        }
        if label is not None:
            body["note"] = label
        return self.post(f"/reports/{report_id}/links", json=body)

    def fetch_report_types(self) -> list[dict]:
        """GET /report-types — returns the report-type catalog (items[])."""
        body = self.get("/report-types")
        if isinstance(body, dict) and "items" in body:
            return list(body.get("items") or [])
        return list(body or []) if isinstance(body, list) else []

    # ---- soft delete (v0.10.0 — RA dc8bd45 + ff64778) ----------------- #

    def trash_report(self, report_id: int) -> dict:
        """POST /reports/{report_id}/trash — move to trash (soft delete).

        Returns the report with deleted_at set. Blocked while the report is
        mounted to any board (RA ff64778 게시 중 차단 가드); restore from
        trash via restore_report. Permanent purge happens only on a manual
        admin action — the trash is recoverable.
        """
        logger.info("trash_report id=%s", report_id)
        return self.post(f"/reports/{report_id}/trash", json={})

    def restore_report(self, report_id: int) -> dict:
        """POST /reports/{report_id}/restore — recover from trash."""
        logger.info("restore_report id=%s", report_id)
        return self.post(f"/reports/{report_id}/restore", json={})

    # ---- takedown requests (v0.10.0 — RA 3e92860) --------------------- #
    # Non-managers (the report owner) cannot unmount directly once a board
    # manager has accepted the mount; they raise a takedown request instead.
    # The board manager (or sys admin) approves or rejects it.

    def request_report_takedown(
        self,
        report_id: int,
        *,
        workspace_slug: str,
        reason: Optional[str] = None,
    ) -> dict:
        """POST /reports/{report_id}/takedown-requests — submit takedown request."""
        logger.info(
            "request_report_takedown id=%s slug=%s",
            report_id, workspace_slug,
        )
        body: dict[str, Any] = {"workspace_slug": workspace_slug}
        if reason is not None:
            body["reason"] = reason
        return self.post(
            f"/reports/{report_id}/takedown-requests", json=body
        )

    def list_takedown_requests(
        self,
        *,
        workspace_slug: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict]:
        """GET /takedown-requests — manager / sys admin view of the queue."""
        params: dict[str, Any] = {}
        if workspace_slug is not None:
            params["workspace_slug"] = workspace_slug
        if status is not None:
            params["status"] = status
        body = self.get("/takedown-requests", params=params or None)
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            return list(body.get("items", body) or [])
        return []

    def approve_takedown_request(self, request_id: int) -> dict:
        """POST /takedown-requests/{request_id}/approve — manager only.
        Unmounts the report from the board and closes the request."""
        logger.info("approve_takedown_request id=%s", request_id)
        return self.post(
            f"/takedown-requests/{request_id}/approve", json={}
        )

    def reject_takedown_request(
        self, request_id: int, *, reason: Optional[str] = None
    ) -> dict:
        """POST /takedown-requests/{request_id}/reject — manager only.
        Leaves the report mounted; surfaces the rejection back to the requester."""
        logger.info("reject_takedown_request id=%s", request_id)
        body: dict[str, Any] = {}
        if reason is not None:
            body["reason"] = reason
        return self.post(
            f"/takedown-requests/{request_id}/reject", json=body
        )

    def publish_report(self, report_id: int) -> dict:
        """POST /reports/{report_id}/publish — flip phase to finalized.

        Owner-only; idempotent. Fans out activity + notifications.
        """
        return self.post(f"/reports/{report_id}/publish", json={})

    def unpublish_report(self, report_id: int) -> dict:
        """POST /reports/{report_id}/unpublish — flip phase back to drafting.

        Owner-only; no-op if not currently finalized.
        """
        return self.post(f"/reports/{report_id}/unpublish", json={})

    def fetch_report_lock_status(self, report_id: int) -> dict:
        """GET /reports/{report_id} projected to the 3 author-lock fields.

        Returns {author_lock_enabled, author_lock_reason, author_lock_set_at}.
        The service account is not the owner so we cannot toggle the lock;
        this is read-only for diagnostics.
        """
        body = self.get(f"/reports/{report_id}")
        if not isinstance(body, dict):
            body = {}
        return {
            "author_lock_enabled": bool(body.get("author_lock_enabled", False)),
            "author_lock_reason": body.get("author_lock_reason"),
            "author_lock_set_at": body.get("author_lock_set_at"),
        }

    # ---- grants / unified sharing (v0.8.0) ----------------------------- #
    # Three resource taxonomies — content (reports + composites), folders,
    # board (workspace). Each exposes list/create/delete. The new sharing
    # surface replaces the prior ad-hoc collab_workspace_slugs + mount
    # edit-policy hand-waving with a single grant model:
    #   principal_type ∈ {workspace, workspace_manager, all_org, user}
    #   level          ∈ {view, edit}
    # all_org grants force level=view and ignore principal_ref (전체 공개).

    def list_content_shares(self, content_type: str, content_id: int) -> list[dict]:
        """GET /{content_type}/{id}/shares — content_type ∈ {reports, composites}."""
        body = self.get(f"/{content_type}/{content_id}/shares")
        return body if isinstance(body, list) else (body.get("items", body) if isinstance(body, dict) else [])

    def add_content_share(
        self,
        content_type: str,
        content_id: int,
        *,
        principal_type: str,
        principal_ref: Optional[str] = None,
        level: str = "view",
    ) -> dict:
        """POST /{content_type}/{id}/shares — owner / sys admin only."""
        logger.info("add_content_share %s id=%s principal=%s ref=%s level=%s",
                    content_type, content_id, principal_type, principal_ref, level)
        body = {"principal_type": principal_type, "level": level}
        if principal_ref is not None:
            body["principal_ref"] = principal_ref
        return self.post(f"/{content_type}/{content_id}/shares", json=body)

    def remove_content_share(self, content_type: str, content_id: int, grant_id: int) -> None:
        """DELETE /{content_type}/{id}/shares/{grant_id} — owner / sys admin only."""
        logger.info("remove_content_share %s id=%s grant=%s", content_type, content_id, grant_id)
        self._request("DELETE", f"/{content_type}/{content_id}/shares/{grant_id}")

    def list_folder_shares(self, folder_id: int) -> list[dict]:
        """GET /folders/{id}/shares — org folders only."""
        body = self.get(f"/folders/{folder_id}/shares")
        return body if isinstance(body, list) else (body.get("items", body) if isinstance(body, dict) else [])

    def add_folder_share(
        self,
        folder_id: int,
        *,
        principal_type: str,
        principal_ref: Optional[str] = None,
        level: str = "view",
    ) -> dict:
        """POST /folders/{id}/shares — board manager / sys admin only."""
        logger.info("add_folder_share id=%s principal=%s ref=%s level=%s",
                    folder_id, principal_type, principal_ref, level)
        body = {"principal_type": principal_type, "level": level}
        if principal_ref is not None:
            body["principal_ref"] = principal_ref
        return self.post(f"/folders/{folder_id}/shares", json=body)

    def remove_folder_share(self, folder_id: int, grant_id: int) -> None:
        """DELETE /folders/{id}/shares/{grant_id} — board manager / sys admin only."""
        logger.info("remove_folder_share id=%s grant=%s", folder_id, grant_id)
        self._request("DELETE", f"/folders/{folder_id}/shares/{grant_id}")

    def list_board_shares(self, workspace_slug: str) -> list[dict]:
        """GET /workspaces/{slug}/shares — org boards only."""
        body = self.get(f"/workspaces/{workspace_slug}/shares")
        return body if isinstance(body, list) else (body.get("items", body) if isinstance(body, dict) else [])

    def add_board_share(
        self,
        workspace_slug: str,
        *,
        principal_type: str,
        principal_ref: Optional[str] = None,
        level: str = "view",
    ) -> dict:
        """POST /workspaces/{slug}/shares — board manager / sys admin only."""
        logger.info("add_board_share slug=%s principal=%s ref=%s level=%s",
                    workspace_slug, principal_type, principal_ref, level)
        body = {"principal_type": principal_type, "level": level}
        if principal_ref is not None:
            body["principal_ref"] = principal_ref
        return self.post(f"/workspaces/{workspace_slug}/shares", json=body)

    def remove_board_share(self, workspace_slug: str, grant_id: int) -> None:
        """DELETE /workspaces/{slug}/shares/{grant_id} — board manager / sys admin only."""
        logger.info("remove_board_share slug=%s grant=%s", workspace_slug, grant_id)
        self._request("DELETE", f"/workspaces/{workspace_slug}/shares/{grant_id}")

    # ---- widget relations --------------------------------------------- #

    def list_widget_relations(self) -> list[dict]:
        """GET /widget-relations — list relation slugs for rich_text mention chips."""
        body = self.get("/widget-relations")
        if isinstance(body, dict) and "items" in body:
            return list(body.get("items") or [])
        return list(body or []) if isinstance(body, list) else []

    # ---- folders / mounts ---------------------------------------------- #

    def list_folders(self, workspace_slug: Optional[str] = None) -> list[dict]:
        """GET /folders?workspace_slug=... — returns folders for a workspace.

        Omit `workspace_slug` to get the caller's personal folders. Use a
        `personal-{N}` slug for that user's personal folders (self/sys admin
        only).
        """
        params: dict[str, Any] = {}
        if workspace_slug is not None:
            params["workspace_slug"] = workspace_slug
        body = self.get("/folders", params=params or None)
        if isinstance(body, dict) and "items" in body:
            return list(body.get("items") or [])
        return list(body or []) if isinstance(body, list) else []

    def set_mount_folder(
        self,
        report_id,
        workspace_slug: str,
        *,
        folder_id: Optional[int],
    ) -> dict:
        """PUT /mounts/{report_id}/{workspace_slug}/folder — move a mount
        into a folder. Pass `folder_id=None` to clear (uncategorized).
        """
        body: dict[str, Any] = {"folder_id": folder_id}
        return self._request(
            "PUT", f"/mounts/{report_id}/{workspace_slug}/folder", json=body
        )

    def set_mount_edit_policy(
        self,
        report_id,
        workspace_slug: str,
        *,
        edit_policy: str,
    ) -> dict:
        """PUT /mounts/{report_id}/{workspace_slug}/edit-policy — change
        the per-mount edit policy. Valid values: 'default', 'owner_only',
        'coauthor', 'manager' (RA p27 — 작성자+게시판 매니저, auto-syncs a
        workspace_manager grant).
        """
        body = {"edit_policy": edit_policy}
        return self._request(
            "PUT", f"/mounts/{report_id}/{workspace_slug}/edit-policy", json=body
        )

    # ---- templates ----------------------------------------------------- #

    def set_template_scope(
        self,
        template_id,
        *,
        owner_workspace_slugs: Optional[list[str]],
    ) -> dict:
        """PATCH /templates/{template_id}/scope — change template scope.

        Pass `owner_workspace_slugs=None` (or []) for 전사(global) scope.
        Manager-only; does not bump template version.
        """
        body: dict[str, Any] = {"owner_workspace_slugs": owner_workspace_slugs}
        return self._request("PATCH", f"/templates/{template_id}/scope", json=body)

    # ---- presets ------------------------------------------------------- #

    def list_presets(self, template_id=None) -> list[dict]:
        """GET /presets — returns presets visible to the caller's workspace
        tree. Filter by `template_id` to narrow to a single template.
        """
        params: dict[str, Any] = {}
        if template_id is not None:
            params["template_id"] = str(template_id)
        body = self.get("/presets", params=params or None)
        if isinstance(body, dict) and "items" in body:
            return list(body.get("items") or [])
        return list(body or []) if isinstance(body, list) else []

    def create_preset(
        self,
        report_id,
        *,
        name: str,
        owner_workspace_slugs: Optional[list[str]] = None,
        description: Optional[str] = None,
    ) -> dict:
        """POST /presets — capture a report's structure as a reusable preset.

        `owner_workspace_slugs=None`/empty means 전사(global) preset.
        `description=None` keeps the existing server default (empty string);
        pass any string to override.
        """
        body: dict[str, Any] = {
            "source_report_id": int(report_id),
            "name": name,
            "description": "" if description is None else str(description),
        }
        if owner_workspace_slugs is not None:
            body["owner_workspace_slugs"] = list(owner_workspace_slugs)
        return self.post("/presets", json=body)

    def new_report_from_preset(
        self,
        preset_id,
        *,
        title: Optional[str] = None,
        folder_id: Optional[int] = None,
    ) -> dict:
        """POST /presets/{preset_id}/new-report — instantiate a new report
        from a preset. `title` defaults to the preset name server-side.
        """
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if folder_id is not None:
            body["folder_id"] = int(folder_id)
        return self.post(f"/presets/{preset_id}/new-report", json=body)

    def delete_preset(self, preset_id) -> None:
        """DELETE /presets/{preset_id} — only the preset author (or system
        admin) may delete.
        """
        self._request("DELETE", f"/presets/{preset_id}")

    # ---- composites ---------------------------------------------------- #

    def get_composite(self, composite_id: int) -> dict:
        """GET /composites/{composite_id} — returns the full composite
        report record (summary_widgets, items, perms).
        """
        return self.get(f"/composites/{composite_id}")

    # ---- v0.6.0 — composites body editing ----------------------------- #

    def create_composite(
        self,
        *,
        title: str,
        period_date: Optional[str] = None,
        kind: str,
        view_mode: str = "single",
        workspace_slug: Optional[str] = None,
        description: str = "",
        two_col_view: Optional[bool] = None,
        summary_widgets: Optional[list[dict]] = None,
        items: Optional[list[dict]] = None,
    ) -> dict:
        """POST /composites — create a new composite report.

        `kind` is one of the CompositeKind enum values (e.g. 'recurring' /
        'theme'). `workspace_slug` defaults to the active workspace when
        omitted (the server takes the value off the X-Workspace-Slug
        header — we forward it here as a positional override).

        `items` is a list of {note?, ref_report_id?|ref_composite_id?,
        display_column?, group_name?} dicts — exactly one of the two ref
        ids per row. Defaults to empty so the caller can do an
        edit-after-create flow.
        """
        body: dict[str, Any] = {
            "title": title,
            "kind": kind,
            "view_mode": view_mode,
            "description": description,
            "workspace_slug": workspace_slug or settings.report_api_workspace_slug,
        }
        if period_date is not None:
            body["period_date"] = period_date
        if two_col_view is not None:
            body["two_col_view"] = bool(two_col_view)
        if summary_widgets is not None:
            body["summary_widgets"] = list(summary_widgets)
        if items is not None:
            body["items"] = list(items)
        return self.post("/composites", json=body)

    def update_composite(
        self,
        composite_id,
        *,
        items: Optional[list[dict]] = None,
        period_date: Any = _UNSET_COMP,
        view_mode: Optional[str] = None,
        description: Optional[str] = None,
        two_col_view: Optional[bool] = None,
        title: Optional[str] = None,
        summary_widgets: Optional[list[dict]] = None,
        expected_revision: Optional[int] = None,
    ) -> dict:
        """PATCH /composites/{id} — full body editing.

        Only sends the fields the caller supplied. `items` replaces the
        entire items list (matching position order); omit to leave items
        untouched. `period_date` is forwarded ONLY when explicitly passed —
        callers can clear it by passing None.

        `expected_revision` enables optimistic concurrency. Backend returns
        409 (CompositeRevisionConflict) on mismatch.
        """
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if items is not None:
            body["items"] = list(items)
        if period_date is not _UNSET_COMP:
            body["period_date"] = period_date
        if view_mode is not None:
            body["view_mode"] = view_mode
        if description is not None:
            body["description"] = description
        if two_col_view is not None:
            body["two_col_view"] = bool(two_col_view)
        if summary_widgets is not None:
            body["summary_widgets"] = list(summary_widgets)
        if expected_revision is not None:
            body["expected_revision"] = int(expected_revision)
        return self._request("PATCH", f"/composites/{composite_id}", json=body)

    def delete_composite(self, composite_id) -> None:
        """DELETE /composites/{id} — owner / sys admin only."""
        self._request("DELETE", f"/composites/{composite_id}")

    def publish_composite(self, composite_id) -> dict:
        """POST /composites/{id}/publish — owner-only. Stamps
        `published_at`; for recurring composites freezes every item's
        content into `snapshot_content`. Idempotent."""
        return self.post(f"/composites/{composite_id}/publish", json={})

    def unpublish_composite(self, composite_id) -> dict:
        """POST /composites/{id}/unpublish — owner-only. Clears
        `published_at` and per-item snapshots. Idempotent."""
        return self.post(f"/composites/{composite_id}/unpublish", json={})

    def update_composite_summary(
        self,
        composite_id,
        *,
        summary_widgets: list[dict],
        expected_revision: Optional[int] = None,
    ) -> dict:
        """PATCH /composites/{composite_id} — update only summary_widgets
        (other fields are left untouched). Pass `expected_revision` for
        optimistic-concurrency control (409 on mismatch).
        """
        body: dict[str, Any] = {"summary_widgets": list(summary_widgets)}
        if expected_revision is not None:
            body["expected_revision"] = int(expected_revision)
        return self._request("PATCH", f"/composites/{composite_id}", json=body)

    def list_submittable_composites(self, report_id) -> list[dict]:
        """GET /composites/submittable-for/{report_id} — composites the
        caller could submit this report into (with already_item /
        already_pending flags).
        """
        body = self.get(f"/composites/submittable-for/{report_id}")
        if isinstance(body, dict) and "items" in body:
            return list(body.get("items") or [])
        return list(body or []) if isinstance(body, list) else []

    def list_composite_requests(
        self,
        composite_id,
        *,
        status_filter: Optional[str] = None,
    ) -> list[dict]:
        """GET /composites/{composite_id}/requests — pending requests by
        default (server-side filter). Pass `status_filter` (e.g. 'pending',
        'accepted', 'rejected', 'withdrawn', 'all') to override.
        """
        params: dict[str, Any] = {}
        if status_filter is not None:
            params["status_filter"] = status_filter
        body = self.get(
            f"/composites/{composite_id}/requests",
            params=params or None,
        )
        if isinstance(body, dict) and "items" in body:
            return list(body.get("items") or [])
        return list(body or []) if isinstance(body, list) else []

    def submit_to_composite(
        self,
        composite_id,
        *,
        report_id: int,
        note: Optional[str] = None,
    ) -> dict:
        """POST /composites/{composite_id}/requests — request that a
        report be added to a composite. Server stores empty note when
        omitted.
        """
        body: dict[str, Any] = {"ref_report_id": int(report_id)}
        if note is not None:
            body["note"] = note
        return self.post(f"/composites/{composite_id}/requests", json=body)

    def accept_composite_request(self, composite_id: int, request_id: int) -> dict:
        """POST /composites/{composite_id}/requests/{request_id}/accept —
        composite owner / sys admin only.
        """
        return self.post(
            f"/composites/{composite_id}/requests/{request_id}/accept", json={}
        )

    def reject_composite_request(
        self,
        composite_id: int,
        request_id: int,
        *,
        reason: Optional[str] = None,
    ) -> dict:
        """POST /composites/{composite_id}/requests/{request_id}/reject —
        composite owner / sys admin only. `reason` is accepted for
        forward-compat but currently ignored server-side.
        """
        body: dict[str, Any] = {}
        if reason is not None:
            body["reason"] = reason
        return self.post(
            f"/composites/{composite_id}/requests/{request_id}/reject", json=body
        )

    def withdraw_composite_request(self, composite_id: int, request_id: int) -> dict:
        """POST /composites/{composite_id}/requests/{request_id}/withdraw —
        requester self / composite owner / sys admin only.
        """
        return self.post(
            f"/composites/{composite_id}/requests/{request_id}/withdraw", json={}
        )

    # ---- v0.6.0 — activities timeline ---------------------------------- #

    def fetch_report_activities(
        self,
        report_id,
        *,
        limit: int = 20,
        before_id: Optional[int] = None,
    ) -> dict:
        """GET /reports/{id}/activities — newest-first activity timeline.

        Cursor pagination via `before_id` (pass the smallest id of the
        previous page to get the next page). Public-only viewers receive
        an empty list per backend policy. Returns the raw envelope
        `{items: [...]}` so callers can detect end-of-list.
        """
        params: dict[str, Any] = {"limit": int(limit)}
        if before_id is not None:
            params["before_id"] = int(before_id)
        body = self.get(f"/reports/{report_id}/activities", params=params)
        if isinstance(body, dict):
            return body
        if isinstance(body, list):
            return {"items": body}
        return {"items": []}

    # ---- v0.6.0 — notifications inbox ---------------------------------- #

    def list_notifications(
        self,
        *,
        unread_only: bool = False,
        limit: int = 50,
        before_id: Optional[int] = None,
    ) -> dict:
        """GET /notifications — caller's inbox, newest-first.

        Returns the raw envelope `{items: [...], unread_count: N}` so
        callers can render the badge in one shot.
        """
        params: dict[str, Any] = {
            "unread_only": "true" if unread_only else "false",
            "limit": int(limit),
        }
        if before_id is not None:
            params["before_id"] = int(before_id)
        body = self.get("/notifications", params=params)
        if isinstance(body, dict):
            return body
        if isinstance(body, list):
            return {"items": body, "unread_count": 0}
        return {"items": [], "unread_count": 0}

    def unread_notification_count(self) -> int:
        """GET /notifications/unread-count — single integer badge count."""
        body = self.get("/notifications/unread-count")
        if isinstance(body, dict):
            return int(body.get("unread_count", 0) or 0)
        return 0

    def mark_notification_read(self, notification_id) -> dict:
        """PATCH /notifications/{id}/read — mark a single notification read.
        Idempotent."""
        return self._request("PATCH",
                             f"/notifications/{notification_id}/read",
                             json={})

    def mark_all_notifications_read(self) -> int:
        """POST /notifications/mark-all-read — returns the number of rows
        flipped from unread to read."""
        body = self.post("/notifications/mark-all-read", json={})
        if isinstance(body, dict):
            return int(body.get("count", 0) or 0)
        return 0

    # ---- low-level wrappers -------------------------------------------- #

    def get(self, path: str, *, params: Optional[dict] = None) -> dict[str, Any] | list[Any]:
        return self._request("GET", path, params=params)

    def post(self, path: str, *, json: Optional[dict] = None) -> dict[str, Any] | list[Any]:
        return self._request("POST", path, json=json)

    def _post(self, path: str, *, json: Optional[dict] = None, _no_auth: bool = False) -> Any:
        return self._request("POST", path, json=json, _no_auth=_no_auth)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json: Optional[dict] = None,
        _no_auth: bool = False,
    ) -> Any:
        if not _no_auth:
            self.ensure_logged_in()

        headers: dict[str, str] = {"X-Workspace-Slug": settings.report_api_workspace_slug}
        if self._token and not _no_auth:
            headers["Authorization"] = f"Bearer {self._token}"

        resp = self._http.request(method, path, params=params, json=json, headers=headers)

        try:
            body = resp.json()
        except Exception:
            resp.raise_for_status()
            return None

        # Standard envelope: {success, data, message, errors}
        if isinstance(body, dict) and "success" in body:
            if body.get("success"):
                return body.get("data")
            message = body.get("message") or f"{method} {path} failed"
            typed = self._build_typed_error(
                resp.status_code, str(message), path, body
            )
            if typed is not None:
                raise typed
            raise ApiError(
                message,
                status_code=resp.status_code,
                payload=body,
            )

        # Non-envelope endpoints (eg. health, FastAPI default {detail:str}) — just return body
        if resp.is_error:
            text_body = ""
            if isinstance(body, dict):
                text_body = str(body.get("message") or body.get("detail") or "")
            elif isinstance(body, str):
                text_body = body
            typed = self._build_typed_error(
                resp.status_code, text_body, path, body
            )
            if typed is not None:
                raise typed
            raise ApiError(
                f"{method} {path} returned {resp.status_code}",
                status_code=resp.status_code,
                payload=body,
            )
        return body

    @staticmethod
    def _build_author_locked_error(message: str, path: str) -> "AuthorLockedError":
        """Parse the Korean lock message and extract reason + report_id from the URL.

        Server format: "작성자가 수정 잠금 상태입니다 (사유: <reason>)".
        """
        reason = ""
        marker = "사유: "
        idx = message.find(marker)
        if idx != -1:
            tail = message[idx + len(marker):]
            close = tail.rfind(")")
            reason = (tail[:close] if close != -1 else tail).strip()
        report_id = ReportArchiveClient._extract_report_id(path)
        return AuthorLockedError(reason=reason, report_id=report_id, status_code=403)

    @staticmethod
    def _extract_report_id(path: str) -> Optional[int]:
        try:
            import re as _re
            m = _re.search(r"/reports/(\d+)", path)
            if m:
                return int(m.group(1))
        except Exception:
            return None
        return None

    @staticmethod
    def _extract_composite_id(path: str) -> Optional[int]:
        try:
            import re as _re
            m = _re.search(r"/composites/(\d+)", path)
            if m:
                return int(m.group(1))
        except Exception:
            return None
        return None

    @classmethod
    def _build_typed_error(
        cls,
        status_code: int,
        message: str,
        path: str,
        body: Any,
    ) -> Optional[ApiError]:
        """Map a (status, message, payload) tuple to the correct typed subclass.

        Returns None when no typed mapping applies — caller falls back to generic
        ApiError. Order matters: 409 envelope codes first (stable signals), then
        409 composite-path heuristic, then 403 Korean / ASCII substrings.
        """
        # ---- 409: prefer payload.errors[0].code (stable backend code) ---- #
        if status_code == 409:
            code = ""
            if isinstance(body, dict):
                errs = body.get("errors") or []
                if errs and isinstance(errs[0], dict):
                    code = str(errs[0].get("code") or "")
            if code == "lock_held_by_other":
                holder = None
                if isinstance(body, dict):
                    errs = body.get("errors") or []
                    if errs and isinstance(errs[0], dict):
                        holder = errs[0].get("holder")
                return LockHeldByOtherError(
                    message, payload=body, holder=holder, status_code=409
                )
            if code == "lock_not_held":
                return LockNotHeldError(message, payload=body, status_code=409)
            if code == "revision_mismatch":
                return RevisionMismatchError(message, payload=body, status_code=409)
            if code == "composite_revision_mismatch":
                return CompositeRevisionConflict(
                    message, payload=body,
                    composite_id=cls._extract_composite_id(path),
                    status_code=409,
                )
            # Composite revision conflict via FastAPI default {detail:str}
            # (no errors[] envelope). Detect by path.
            if path.startswith("/composites/") or "/composites/" in path:
                return CompositeRevisionConflict(
                    message, payload=body,
                    composite_id=cls._extract_composite_id(path),
                    status_code=409,
                )
            return None

        # ---- 403: Korean / ASCII substring detection --------------------- #
        if status_code == 403:
            if "작성자가 수정 잠금" in message:
                return cls._build_author_locked_error(message, path)
            if message.startswith("발행된 보고서"):
                return FinalizedReadOnlyError(
                    message, payload=body,
                    report_id=cls._extract_report_id(path),
                    status_code=403,
                )
            if "편집할 권한이 없" in message:
                return NoEditPermissionError(
                    message, payload=body,
                    report_id=cls._extract_report_id(path),
                    status_code=403,
                )
            if message == "Out of workspace scope" or "Out of workspace scope" in message:
                return OutOfWorkspaceScopeError(
                    message, payload=body, status_code=403
                )
            # v0.8.1 — grants 403 paths from RA dbdbf99/c6308ae.
            if message.startswith("공유 설정은 작성자"):
                return ShareSetupForbiddenError(
                    message, payload=body, status_code=403
                )
            if message.startswith("게시판 공유는"):
                return BoardShareForbiddenError(
                    message, payload=body, status_code=403
                )
            return None

        return None

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:
            logger.debug("error closing httpx client", exc_info=True)

    def __enter__(self) -> "ReportArchiveClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
