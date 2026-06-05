"""Thin HTTP client for the ReportArchive API.

Handles login (caches the JWT for the process lifetime), attaches the
required `Authorization` and `X-Workspace-Slug` headers, and unwraps the
standard `{success, data, message, errors}` envelope.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from report_skill.config import settings


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


class ReportArchiveClient:
    """Synchronous httpx-based client. One instance per process is fine."""

    def __init__(self) -> None:
        self._token: Optional[str] = None
        self._user_id: Optional[int] = None
        self._http = httpx.Client(
            base_url=settings.report_api_base_url,
            timeout=httpx.Timeout(30.0, read=60.0),
        )

    # ---- auth ---------------------------------------------------------- #

    def login(self) -> dict:
        """Authenticate the service account. Idempotent (re-logging overwrites)."""
        env = self._post("/auth/login", json={
            "email": settings.report_api_email,
            "password": settings.report_api_password,
        }, _no_auth=True)
        self._token = env["access_token"]
        self._user_id = env["user_id"]
        return env

    def ensure_logged_in(self) -> None:
        if not self._token:
            self.login()

    # ---- public endpoints ---------------------------------------------- #

    def fetch_widgets(self) -> dict:
        """GET /widgets — returns {schema_version, widgets:[...]}.

        Note: each widget entry contains props_schema but NOT content_schema
        (which is computed server-side as a Python function of props). Use
        the bridge script for the latter.
        """
        return self.get("/widgets")

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

    def publish_report(self, report_id) -> dict:
        """POST /reports/{report_id}/publish — flip phase to finalized.

        Owner-only; idempotent. Fans out activity + notifications.
        """
        return self.post(f"/reports/{report_id}/publish", json={})

    def unpublish_report(self, report_id) -> dict:
        """POST /reports/{report_id}/unpublish — flip phase back to drafting.

        Owner-only; no-op if not currently finalized.
        """
        return self.post(f"/reports/{report_id}/unpublish", json={})

    def fetch_report_lock_status(self, report_id) -> dict:
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
        'coauthor'.
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
    ) -> dict:
        """POST /presets — capture a report's structure as a reusable preset.

        `owner_workspace_slugs=None`/empty means 전사(global) preset.
        """
        body: dict[str, Any] = {
            "source_report_id": int(report_id),
            "name": name,
            "description": "",
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

    def get_composite(self, composite_id) -> dict:
        """GET /composites/{composite_id} — returns the full composite
        report record (summary_widgets, items, perms).
        """
        return self.get(f"/composites/{composite_id}")

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

    def accept_composite_request(self, composite_id, request_id) -> dict:
        """POST /composites/{composite_id}/requests/{request_id}/accept —
        composite owner / sys admin only.
        """
        return self.post(
            f"/composites/{composite_id}/requests/{request_id}/accept", json={}
        )

    def reject_composite_request(
        self,
        composite_id,
        request_id,
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

    def withdraw_composite_request(self, composite_id, request_id) -> dict:
        """POST /composites/{composite_id}/requests/{request_id}/withdraw —
        requester self / composite owner / sys admin only.
        """
        return self.post(
            f"/composites/{composite_id}/requests/{request_id}/withdraw", json={}
        )

    # ---- low-level wrappers -------------------------------------------- #

    def get(self, path: str, *, params: Optional[dict] = None) -> Any:
        return self._request("GET", path, params=params)

    def post(self, path: str, *, json: Optional[dict] = None) -> Any:
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
            if resp.status_code == 403 and "작성자가 수정 잠금" in str(message):
                raise self._build_author_locked_error(message, path)
            raise ApiError(
                message,
                status_code=resp.status_code,
                payload=body,
            )

        # Non-envelope endpoints (eg. health) — just return body
        if resp.is_error:
            text_body = ""
            if isinstance(body, dict):
                text_body = str(body.get("message") or body.get("detail") or "")
            elif isinstance(body, str):
                text_body = body
            if resp.status_code == 403 and "작성자가 수정 잠금" in text_body:
                raise self._build_author_locked_error(text_body, path)
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
        report_id: Optional[int] = None
        # /reports/<id>/... or /reports/<id>
        try:
            import re as _re
            m = _re.search(r"/reports/(\d+)", path)
            if m:
                report_id = int(m.group(1))
        except Exception:
            report_id = None
        return AuthorLockedError(reason=reason, report_id=report_id, status_code=403)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ReportArchiveClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
