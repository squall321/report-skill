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
            raise ApiError(
                body.get("message") or f"{method} {path} failed",
                status_code=resp.status_code,
                payload=body,
            )

        # Non-envelope endpoints (eg. health) — just return body
        if resp.is_error:
            raise ApiError(
                f"{method} {path} returned {resp.status_code}",
                status_code=resp.status_code,
                payload=body,
            )
        return body

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ReportArchiveClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
