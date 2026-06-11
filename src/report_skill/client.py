"""Thin HTTP client for the ReportArchive API.

Handles login (caches the JWT for the process lifetime), attaches the
required `Authorization` and `X-Workspace-Slug` headers, and unwraps the
standard `{success, data, message, errors}` envelope.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import httpx

from report_skill import telemetry
from report_skill.config import settings

logger = logging.getLogger(__name__)

# ---- module-level token cache (v0.13.0) ----------------------------------- #
# Keyed by base_url+email so every `with ReportArchiveClient()` block in the
# process reuses one JWT instead of paying the ~250ms bcrypt login per MCP
# tool call. RA access tokens last 12h (backend config); 11h TTL keeps a
# conservative margin. A token that goes stale early is healed by the
# 401 re-login path in _request().
_TOKEN_TTL_SECONDS = 11 * 60 * 60
_TOKEN_CACHE: dict[str, Any] = {}


def _token_cache_key() -> str:
    return f"{settings.report_api_base_url}|{settings.report_api_email}"


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


# ---- network / availability typed errors (v0.13.0) ------------------------ #
# Both subclass ApiError so existing `except ApiError` paths keep working.
# `retryable = True` signals the MCP layer that this is an environment
# problem (server down / unreachable), not a code bug.


class NetworkUnreachableError(ApiError):
    """Raised when the RA backend cannot be reached (connect error / timeout).

    This is NOT a code bug — the LLM should tell the user the server looks
    down and may retry later. `status_code` is 0 because no HTTP response
    was received.
    """

    def __init__(self, message: str = "", *, status_code: int = 0,
                 payload: Any = None):
        super().__init__(message or "network_unreachable",
                         status_code=status_code, payload=payload)
        self.code = "network_unreachable"
        self.retryable = True


class AuthUnavailableError(ApiError):
    """Raised when login itself fails due to network (backend down during auth).

    Same semantics as NetworkUnreachableError — the server looks down and the
    user may retry later; this is not a credentials problem (bad credentials
    surface as a regular 401 ApiError instead).
    """

    def __init__(self, message: str = "", *, status_code: int = 0,
                 payload: Any = None):
        super().__init__(message or "auth_unavailable",
                         status_code=status_code, payload=payload)
        self.code = "auth_unavailable"
        self.retryable = True


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


class TrashRestoreForbiddenError(ApiError):
    """Raised when the caller is not the owner / sys admin and tries to trash or
    restore a report (403). RA v0.10.0 / dc8bd45.

    Backend messages: "이 보고서를 삭제할 권한이 없습니다 (소유자만 가능)." /
    "이 보고서를 복구할 권한이 없습니다 (소유자만 가능).".
    """

    def __init__(self, message: str = "", *, payload: Any = None,
                 report_id: Optional[int] = None, status_code: int = 403):
        super().__init__(message or "trash_restore_forbidden",
                         status_code=status_code, payload=payload)
        self.code = "trash_restore_forbidden"
        self.report_id = report_id


class TakedownManagerForbiddenError(ApiError):
    """Raised when the caller is not the target board's manager / sys admin and
    tries to act on the takedown queue (403). RA v0.10.0 / 3e92860.

    Backend messages cover both variants:
      - "이 게시판에서 게시취소할 권한이 없습니다 (게시판 매니저만 가능 — ..."
      - "이 게시판의 게시취소 요청을 처리할 권한이 없습니다 (게시판 매니저만 가능)."
    """

    def __init__(self, message: str = "", *, payload: Any = None,
                 status_code: int = 403):
        super().__init__(message or "takedown_manager_forbidden",
                         status_code=status_code, payload=payload)
        self.code = "takedown_manager_forbidden"


class TakedownOwnerForbiddenError(ApiError):
    """Raised when a non-owner tries to submit a takedown request on a report
    they do not own (403). RA v0.10.0 / 3e92860.

    Backend message: "본인 보고서만 게시취소를 요청할 수 있습니다.".
    """

    def __init__(self, message: str = "", *, payload: Any = None,
                 report_id: Optional[int] = None, status_code: int = 403):
        super().__init__(message or "takedown_owner_forbidden",
                         status_code=status_code, payload=payload)
        self.code = "takedown_owner_forbidden"
        self.report_id = report_id


class TakedownAlreadyProcessedError(ApiError):
    """Raised when trying to approve/reject a takedown request that already
    settled (already approved / rejected / withdrawn). RA v0.10.0 / 3e92860.

    Backend message: "이미 처리된 요청입니다." (MountForbiddenError envelope).
    HTTP code is 403 in this RA build but caller should NOT retry — the
    request is closed.
    """

    def __init__(self, message: str = "", *, payload: Any = None,
                 status_code: int = 403):
        super().__init__(message or "takedown_already_processed",
                         status_code=status_code, payload=payload)
        self.code = "takedown_already_processed"


# ---- mounts typed errors (v0.13.0) ---------------------------------------- #
# RA emits a stable `errors[0].code` for mount operations — matched in
# _build_typed_error the same way the 409 envelope codes are.


class MountForbiddenError(ApiError):
    """Raised when unmount / takedown-processing is attempted without
    board-manager rights (403, code=mount_forbidden)."""

    def __init__(self, message: str = "", *, payload: Any = None,
                 status_code: int = 403):
        super().__init__(message or "mount_forbidden",
                         status_code=status_code, payload=payload)
        self.code = "mount_forbidden"


class MountTargetInvalidError(ApiError):
    """Raised when the mount target board / folder is invalid
    (400, code=mount_target_invalid)."""

    def __init__(self, message: str = "", *, payload: Any = None,
                 status_code: int = 400):
        super().__init__(message or "mount_target_invalid",
                         status_code=status_code, payload=payload)
        self.code = "mount_target_invalid"


class ReportStillMountedError(ApiError):
    """Raised when an operation requires the report to be unmounted first
    (409, code=report_still_mounted) — e.g. trashing a mounted report."""

    def __init__(self, message: str = "", *, payload: Any = None,
                 report_id: Optional[int] = None, status_code: int = 409):
        super().__init__(message or "report_still_mounted",
                         status_code=status_code, payload=payload)
        self.code = "report_still_mounted"
        self.report_id = report_id


class CompositePresetPermissionError(ApiError):
    """Raised when editing / deleting a composite preset (종합보고 양식)
    without manage rights — creator, system admin, or manager only (403).

    Backend messages: "이 양식을 수정할 권한이 없습니다." /
    "이 양식을 삭제할 권한이 없습니다." (RA c5c57ca composite_presets module).
    """

    def __init__(self, message: str = "", *, payload: Any = None,
                 status_code: int = 403):
        super().__init__(message or "composite_preset_forbidden",
                         status_code=status_code, payload=payload)
        self.code = "composite_preset_forbidden"


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
        """Authenticate the service account. Idempotent (re-logging overwrites).

        Reuses the module-level cached token (per base_url+email) when it is
        younger than `_TOKEN_TTL_SECONDS`, so repeated short-lived client
        instances skip the ~250ms bcrypt round-trip per MCP tool call.
        Raises AuthUnavailableError when the backend cannot be reached.
        """
        key = _token_cache_key()
        cached = _TOKEN_CACHE.get(key)
        if cached and (time.time() - cached["obtained_at"]) < _TOKEN_TTL_SECONDS:
            self._token = cached["token"]
            self._user_id = cached["user_id"]
            logger.debug("login cache hit user_id=%s", self._user_id)
            return {"access_token": self._token, "user_id": self._user_id,
                    "cached": True}
        try:
            env = self._post("/auth/login", json={
                "email": settings.report_api_email,
                "password": settings.report_api_password,
            }, _no_auth=True)
        except (httpx.RequestError, NetworkUnreachableError) as exc:
            raise AuthUnavailableError(str(exc), status_code=0) from exc
        self._token = env["access_token"]
        self._user_id = env["user_id"]
        _TOKEN_CACHE[key] = {
            "token": self._token,
            "user_id": self._user_id,
            "obtained_at": time.time(),
        }
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
        logger.info("create_report title=%s", (payload or {}).get("title"))
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
        logger.info("upload_file path=%s mime=%s", path, mime_type)
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

        `mode` is 'content' (blocks only), 'full' (blocks + settings), or
        'summary' (v0.15.0 — RA ef4e441: blocks only, PLUS the server links
        the new copy to the source with a kind='summary' link, direction
        원본 → 요약본). The copy lands in the caller's personal workspace.
        """
        logger.info("copy_report id=%s mode=%s title=%s", report_id, mode, title)
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
        logger.info("add_report_link id=%s to=%s kind=%s direction=%s",
                    report_id, to_report_id, kind, direction)
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

    def list_report_links(self, report_id) -> list[dict]:
        """GET /reports/{report_id}/links — every link touching this report
        (both directions). v0.15.0 — needed since copy_report(mode='summary')
        creates system kind='summary' links that only this endpoint exposes
        (the report detail does NOT embed links)."""
        body = self.get(f"/reports/{report_id}/links")
        if isinstance(body, dict) and "items" in body:
            return list(body.get("items") or [])
        return list(body or []) if isinstance(body, list) else []

    def fetch_report_types(self) -> list[dict]:
        """GET /report-types — returns the report-type catalog (items[])."""
        body = self.get("/report-types")
        if isinstance(body, dict) and "items" in body:
            return list(body.get("items") or [])
        return list(body or []) if isinstance(body, list) else []

    # ---- soft delete (v0.10.0 — RA dc8bd45 + ff64778) ----------------- #

    # ---- v0.12.0 — high-value read tools (P1 audit backlog) ----------- #

    def list_composites_by_report(self, report_id: int) -> list[dict]:
        """GET /composites/by-report/{report_id} — every composite that
        references this report as an item. Backs reverse-navigation: "what
        weekly composites does this report appear in?"."""
        body = self.get(f"/composites/by-report/{report_id}")
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            return list(body.get("items", body) or [])
        return []

    def list_reports(
        self,
        *,
        entity_ids: Optional[list[int]] = None,
        folder_id: Optional[str] = None,
        include_public: bool = False,
        include_descendants: bool = False,
        workspace_slug: Optional[str] = None,
    ) -> list[dict]:
        """GET /reports — general report list with filters. Distinct from
        reports_search (which targets mention chips on linkable subset).

        entity_ids       narrow to reports tagged with all the given entities
        folder_id        "uncategorized" or numeric id (personal only)
        include_public   org-context cross-org public reports
        include_descendants  org-context: include child boards
        workspace_slug   X-Workspace-Slug override (rarely needed — the
                         client sends the configured slug automatically)
        """
        params: dict[str, Any] = {}
        if entity_ids:
            params["entity_ids"] = list(entity_ids)
        if folder_id is not None:
            params["folder_id"] = folder_id
        if include_public:
            params["include_public"] = "true"
        if include_descendants:
            params["include_descendants"] = "true"
        headers: Optional[dict[str, str]] = None
        if workspace_slug is not None:
            headers = {"X-Workspace-Slug": workspace_slug}
        body = self._request("GET", "/reports", params=params or None,
                             headers=headers)
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            return list(body.get("items", body) or [])
        return []

    def list_comments_inbox(self) -> dict:
        """GET /comments/inbox — current user's comment inbox: unread / open
        threads across all reports the actor can see. The shape (dict with
        items, counts) is preserved as-is so the LLM sees the same envelope
        the frontend reads."""
        body = self.get("/comments/inbox")
        return body if isinstance(body, dict) else {"items": body or []}

    def list_entities_with_usage(
        self,
        *,
        type_id: Optional[int] = None,
        q: Optional[str] = None,
        include_deprecated: bool = False,
        limit: int = 50,
    ) -> list[dict]:
        """GET /entities?with_usage=true — list entities (taxonomy values)
        with their `usage_count` populated. Useful for deprecation / merge
        planning: "which entity has been used on 0 reports?"."""
        params: dict[str, Any] = {"with_usage": "true", "limit": int(limit)}
        if type_id is not None:
            params["type_id"] = int(type_id)
        if q is not None:
            params["q"] = q
        if include_deprecated:
            params["include_deprecated"] = "true"
        body = self.get("/entities", params=params)
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            return list(body.get("items", body) or [])
        return []

    def list_workspace_members(
        self,
        workspace_slug: str,
        *,
        include_inherited: bool = False,
    ) -> list[dict]:
        """GET /workspaces/{slug}/members — board members + roles.

        Useful for the LLM to answer "who can edit this board?" / "who is
        the manager?" before recommending a mount edit-policy. The members
        route is on its own /workspaces/{slug}/members router.
        """
        params: dict[str, Any] = {}
        if include_inherited:
            params["include_inherited"] = "true"
        body = self.get(f"/workspaces/{workspace_slug}/members",
                        params=params or None)
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            return list(body.get("items", body) or [])
        return []

    def trash_report(self, report_id: int) -> None:
        """POST /reports/{report_id}/trash — move to trash (soft delete).

        Returns None — RA responds `success_response(data=None)` (verified
        live by the e2e suite); re-fetch the report if you need deleted_at.
        Trash SUCCEEDS even while mounted (board copies are preserved —
        게시분 보존); only permanent delete is blocked while mounted
        (409 report_still_mounted). Recoverable via restore_report.
        """
        logger.info("trash_report id=%s", report_id)
        return self.post(f"/reports/{report_id}/trash", json={})

    def restore_report(self, report_id: int) -> None:
        """POST /reports/{report_id}/restore — recover from trash.

        Returns None (RA responds data=None — verified live); re-fetch the
        report if you need to confirm deleted_at was cleared.
        """
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
        logger.info("publish_report id=%s", report_id)
        return self.post(f"/reports/{report_id}/publish", json={})

    def unpublish_report(self, report_id: int) -> dict:
        """POST /reports/{report_id}/unpublish — flip phase back to drafting.

        Owner-only; no-op if not currently finalized.
        """
        logger.info("unpublish_report id=%s", report_id)
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
        logger.info("set_mount_folder id=%s slug=%s folder_id=%s",
                    report_id, workspace_slug, folder_id)
        body: dict[str, Any] = {"folder_id": folder_id}
        return self._request(
            "PUT", f"/mounts/{report_id}/{workspace_slug}/folder", json=body
        )

    def set_mount_note(
        self,
        report_id,
        workspace_slug: str,
        *,
        note: str,
    ) -> dict:
        """PUT /mounts/{report_id}/{workspace_slug}/note — set the per-board
        게시 메모 shown next to the mount (v0.15.0 — RA b435a0f). Server cap
        1000 chars; empty string clears. Author / publisher / board manager.
        Returns {report_id, workspace_slug, note}.
        """
        logger.info("set_mount_note id=%s slug=%s len=%s",
                    report_id, workspace_slug, len(note or ""))
        body = {"note": note}
        return self._request(
            "PUT", f"/mounts/{report_id}/{workspace_slug}/note", json=body
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
        logger.info("set_mount_edit_policy id=%s slug=%s policy=%s",
                    report_id, workspace_slug, edit_policy)
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
        logger.info("set_template_scope id=%s scopes=%s",
                    template_id, owner_workspace_slugs)
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
        logger.info("create_preset source_report_id=%s name=%s", report_id, name)
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
        logger.info("new_report_from_preset preset_id=%s title=%s",
                    preset_id, title)
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
        logger.info("delete_preset id=%s", preset_id)
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
        logger.info("create_composite title=%s kind=%s slug=%s",
                    title, kind, workspace_slug)
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
        logger.info("update_composite id=%s expected_revision=%s",
                    composite_id, expected_revision)
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
        logger.info("delete_composite id=%s", composite_id)
        self._request("DELETE", f"/composites/{composite_id}")

    def publish_composite(self, composite_id) -> dict:
        """POST /composites/{id}/publish — owner-only. Stamps
        `published_at`; for recurring composites freezes every item's
        content into `snapshot_content`. Idempotent."""
        logger.info("publish_composite id=%s", composite_id)
        return self.post(f"/composites/{composite_id}/publish", json={})

    def unpublish_composite(self, composite_id) -> dict:
        """POST /composites/{id}/unpublish — owner-only. Clears
        `published_at` and per-item snapshots. Idempotent."""
        logger.info("unpublish_composite id=%s", composite_id)
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
        logger.info("update_composite_summary id=%s expected_revision=%s",
                    composite_id, expected_revision)
        body: dict[str, Any] = {"summary_widgets": list(summary_widgets)}
        if expected_revision is not None:
            body["expected_revision"] = int(expected_revision)
        return self._request("PATCH", f"/composites/{composite_id}", json=body)

    # ---- v0.15.0 — composite presets / 종합보고 양식 (RA c5c57ca) -------- #

    def list_composite_presets(self) -> list[dict]:
        """GET /composite-presets — presets visible to the caller's
        workspace tree (전사 + own tree). Each item is a summary projection:
        {id, name, description, source_kind, owner_workspace_slugs, groups,
        summary_widget_count, created_by_*, ...} — the heavy summary_widgets
        blob is NOT included."""
        body = self.get("/composite-presets")
        if isinstance(body, dict) and "items" in body:
            return list(body.get("items") or [])
        return list(body or []) if isinstance(body, list) else []

    def create_composite_preset(
        self,
        source_composite_id: int,
        *,
        name: str,
        description: str = "",
        owner_workspace_slugs: Optional[list[str]] = None,
        groups: Optional[list[str]] = None,
    ) -> dict:
        """POST /composite-presets — snapshot an existing composite into a
        reusable 양식. Caller must be able to read the source composite.

        owner_workspace_slugs None/empty = 전사(global) preset.
        groups: full ordered group skeleton (including empty groups); when
        omitted the server derives groups from the source's saved items.
        """
        logger.info("create_composite_preset source=%s name=%s",
                    source_composite_id, name)
        body: dict[str, Any] = {
            "source_composite_id": int(source_composite_id),
            "name": name,
            "description": description,
        }
        if owner_workspace_slugs is not None:
            body["owner_workspace_slugs"] = list(owner_workspace_slugs)
        if groups is not None:
            body["groups"] = list(groups)
        return self.post("/composite-presets", json=body)

    def new_composite_from_preset(
        self,
        preset_id: int,
        *,
        workspace_slug: str,
        title: str,
        kind: str,
        period_date: Optional[str] = None,
    ) -> dict:
        """POST /composite-presets/{id}/new-composite — create a composite
        seeded from a preset. Same writable-scope gate as POST /composites
        (현재 부서 + 하위 부서만; 403 → OutOfWorkspaceScopeError).

        Returns {composite, seed_groups} — seed_groups is the empty-group
        skeleton; the composite itself starts with the preset's
        summary_widgets and no items.
        """
        logger.info("new_composite_from_preset id=%s slug=%s kind=%s title=%s",
                    preset_id, workspace_slug, kind, title)
        body: dict[str, Any] = {
            "workspace_slug": workspace_slug,
            "title": title,
            "kind": kind,
        }
        if period_date is not None:
            body["period_date"] = period_date
        return self.post(f"/composite-presets/{preset_id}/new-composite",
                         json=body)

    def update_composite_preset(
        self,
        preset_id: int,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        owner_workspace_slugs: Any = _UNSET_COMP,
        groups: Optional[list[str]] = None,
    ) -> dict:
        """PATCH /composite-presets/{id} — edit 메타정보 + 그룹 목록.
        Creator / sys admin / manager only (403 →
        CompositePresetPermissionError). Summary widgets can NOT be edited
        here — re-save from the composite editor instead.

        Server uses exclude_unset, so only keys actually sent are applied:
        owner_workspace_slugs uses the tri-state sentinel — omit = 변경 안 함,
        explicit None = 전사(global)로 변경, list = scope to those slugs.
        """
        logger.info("update_composite_preset id=%s", preset_id)
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if owner_workspace_slugs is not _UNSET_COMP:
            body["owner_workspace_slugs"] = (
                list(owner_workspace_slugs)
                if owner_workspace_slugs is not None else None
            )
        if groups is not None:
            body["groups"] = list(groups)
        return self._request("PATCH", f"/composite-presets/{preset_id}",
                             json=body)

    def delete_composite_preset(self, preset_id: int) -> dict:
        """DELETE /composite-presets/{id} — creator / sys admin / manager
        only (403 → CompositePresetPermissionError). Returns {deleted: true}.
        """
        logger.info("delete_composite_preset id=%s", preset_id)
        return self._request("DELETE", f"/composite-presets/{preset_id}")

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
        logger.info("submit_to_composite id=%s report_id=%s",
                    composite_id, report_id)
        body: dict[str, Any] = {"ref_report_id": int(report_id)}
        if note is not None:
            body["note"] = note
        return self.post(f"/composites/{composite_id}/requests", json=body)

    def accept_composite_request(self, composite_id: int, request_id: int) -> dict:
        """POST /composites/{composite_id}/requests/{request_id}/accept —
        composite owner / sys admin only.
        """
        logger.info("accept_composite_request composite=%s request=%s",
                    composite_id, request_id)
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
        logger.info("reject_composite_request composite=%s request=%s",
                    composite_id, request_id)
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
        logger.info("withdraw_composite_request composite=%s request=%s",
                    composite_id, request_id)
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
        logger.info("mark_notification_read id=%s", notification_id)
        return self._request("PATCH",
                             f"/notifications/{notification_id}/read",
                             json={})

    def mark_all_notifications_read(self) -> int:
        """POST /notifications/mark-all-read — returns the number of rows
        flipped from unread to read."""
        logger.info("mark_all_notifications_read")
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
        _retried_auth: bool = False,
    ) -> Any:
        # ---- telemetry (v0.14.0) ---------------------------------------- #
        # Policy: record the FINAL outcome of each logical call exactly once.
        # - The GET network-retry loop records nothing per attempt; only the
        #   eventual response, or the last failure (network_unreachable).
        # - The 401 re-login replay returns the recursive _request() result
        #   WITHOUT recording at this frame — the inner call records the
        #   final outcome, so a re-auth'd call yields one record, not two.
        # record_http() itself never raises (telemetry no-ops on failure),
        # so the calls below are deliberately unguarded.
        _t0 = time.monotonic()

        def _rec(status: int, *, error_code: Optional[str] = None,
                 error_message: Optional[str] = None) -> None:
            telemetry.record_http(
                method, path, status,
                (time.monotonic() - _t0) * 1000.0,
                error_code=error_code, error_message=error_message,
            )

        if not _no_auth:
            self.ensure_logged_in()

        headers: dict[str, str] = {"X-Workspace-Slug": settings.report_api_workspace_slug}
        if self._token and not _no_auth:
            headers["Authorization"] = f"Bearer {self._token}"

        # Network send. GET only gets up to 2 retries with a short backoff —
        # non-GET methods get NO network retry (avoid duplicate writes).
        max_attempts = 3 if method == "GET" else 1
        backoffs = (0.5, 1.0)
        for attempt in range(max_attempts):
            try:
                resp = self._http.request(method, path, params=params, json=json, headers=headers)
                break
            except httpx.RequestError as exc:
                if attempt + 1 < max_attempts:
                    logger.debug(
                        "network error on %s %s (attempt %s/%s): %s — retrying",
                        method, path, attempt + 1, max_attempts, exc,
                    )
                    time.sleep(backoffs[attempt])
                    continue
                _rec(0, error_code="network_unreachable",
                     error_message=str(exc))
                raise NetworkUnreachableError(str(exc), status_code=0) from exc

        # 401 → clear the cached token, re-login once, replay the request.
        # Never for the login call itself (_no_auth), and the _retried_auth
        # flag guarantees at most one retry (no loop).
        if resp.status_code == 401 and not _no_auth and not _retried_auth:
            logger.info("401 on %s %s — re-login and retry once", method, path)
            _TOKEN_CACHE.pop(_token_cache_key(), None)
            self._token = None
            self.login()
            return self._request(method, path, params=params, json=json,
                                 _no_auth=_no_auth, _retried_auth=True)

        try:
            body = resp.json()
        except Exception:
            if resp.is_error:
                _rec(resp.status_code, error_code="http_error",
                     error_message=f"{method} {path} returned non-JSON "
                                   f"{resp.status_code} body")
            resp.raise_for_status()
            _rec(resp.status_code)
            return None

        # Standard envelope: {success, data, message, errors}
        if isinstance(body, dict) and "success" in body:
            if body.get("success"):
                _rec(resp.status_code)
                return body.get("data")
            message = body.get("message") or f"{method} {path} failed"
            typed = self._build_typed_error(
                resp.status_code, str(message), path, body
            )
            err: ApiError = typed if typed is not None else ApiError(
                message,
                status_code=resp.status_code,
                payload=body,
            )
            _rec(resp.status_code,
                 error_code=getattr(err, "code", None) or "api_error",
                 error_message=str(err))
            raise err

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
            err = typed if typed is not None else ApiError(
                f"{method} {path} returned {resp.status_code}",
                status_code=resp.status_code,
                payload=body,
            )
            _rec(resp.status_code,
                 error_code=getattr(err, "code", None) or "api_error",
                 error_message=str(err))
            raise err
        _rec(resp.status_code)
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
        ApiError. Order matters: envelope codes first (stable signals — 409,
        400, 403), then 409 composite-path heuristic, then 403 Korean / ASCII
        substrings.
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
            if code == "report_still_mounted":
                return ReportStillMountedError(
                    message, payload=body,
                    report_id=cls._extract_report_id(path),
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

        # ---- 400: envelope-code detection (v0.13.0 mounts) ---------------- #
        if status_code == 400:
            code = ""
            if isinstance(body, dict):
                errs = body.get("errors") or []
                if errs and isinstance(errs[0], dict):
                    code = str(errs[0].get("code") or "")
            if code == "mount_target_invalid":
                return MountTargetInvalidError(
                    message, payload=body, status_code=400
                )
            return None

        # ---- 403: envelope code first (stable), then Korean / ASCII ------- #
        if status_code == 403:
            code = ""
            if isinstance(body, dict):
                errs = body.get("errors") or []
                if errs and isinstance(errs[0], dict):
                    code = str(errs[0].get("code") or "")
            if code == "mount_forbidden":
                return MountForbiddenError(
                    message, payload=body, status_code=403
                )
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
            # v0.15.0 — composites + composite-preset instantiate share the
            # same writable-scope gate with a Korean message (RA composites
            # routes + c5c57ca new-composite): semantically the same scope
            # violation as the ASCII variant above.
            if message.startswith("종합보고는 현재 부서"):
                return OutOfWorkspaceScopeError(
                    message, payload=body, status_code=403
                )
            # v0.15.0 — composite preset (종합보고 양식) manage gate
            # (RA c5c57ca: PATCH/DELETE /composite-presets/{id}).
            if message.startswith("이 양식을"):
                return CompositePresetPermissionError(
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
            # v0.10.1 — RA trash/restore + takedown queue 403/409 family.
            # Order: more specific takedown-related strings first, then the
            # owner-only trash/restore variants. All keep ApiError as parent
            # so existing `except ApiError` paths continue to catch them.
            if message.startswith("이미 처리된 요청"):
                return TakedownAlreadyProcessedError(
                    message, payload=body, status_code=403
                )
            if (message.startswith("이 게시판에서 게시취소할 권한")
                    or message.startswith("이 게시판의 게시취소 요청을 처리할 권한")):
                return TakedownManagerForbiddenError(
                    message, payload=body, status_code=403
                )
            if message.startswith("본인 보고서만 게시취소"):
                return TakedownOwnerForbiddenError(
                    message, payload=body,
                    report_id=cls._extract_report_id(path),
                    status_code=403,
                )
            if (message.startswith("이 보고서를 삭제할 권한")
                    or message.startswith("이 보고서를 복구할 권한")):
                return TrashRestoreForbiddenError(
                    message, payload=body,
                    report_id=cls._extract_report_id(path),
                    status_code=403,
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
