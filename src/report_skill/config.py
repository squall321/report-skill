"""Settings — loaded from .env (and the process env)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env discovery — try, in order:
#   1. $REPORT_SKILL_ENV                  (explicit override)
#   2. ./.env                             (the receiver's cwd)
#   3. <repo-root>/.env                   (dev mode — parent of `src/`)
#   4. %LOCALAPPDATA%/report-skill/.env   (installer-written, v0.16.0)
#   5. ~/.report-skill/.env               (per-user fallback)
# Same precedence for CACHE_DIR (.skill-cache) so the receiver's cache lives
# next to their .env, not deep inside site-packages.
import os as _os


class ConfigError(RuntimeError):
    """Required settings are missing/invalid. Raised (NOT SystemExit) so
    long-lived hosts survive: a SystemExit raised inside the MCP server's
    asyncio.to_thread worker escaped every `except Exception` and wedged the
    whole server with no JSON-RPC error ever sent (fresh-machine audit B1).
    CLI entrypoints catch this and exit 2 with the same friendly message."""


def _resolve_root() -> Path:
    """Pick the directory that owns this install's .env + .skill-cache."""
    explicit = _os.environ.get("REPORT_SKILL_ENV")
    if explicit:
        return Path(explicit).expanduser().resolve().parent
    cwd_env = Path.cwd() / ".env"
    if cwd_env.is_file():
        return cwd_env.parent
    dev_root = Path(__file__).resolve().parents[2]
    if (dev_root / ".env").is_file():
        return dev_root
    # v0.16.0 — the standalone installer writes .env here but only bridged it
    # via a user-scope REPORT_SKILL_ENV; env-scrubbed launches (services, some
    # MCP clients) lost the config. Probe it directly.
    localappdata = _os.environ.get("LOCALAPPDATA")
    if localappdata:
        la_root = Path(localappdata) / "report-skill"
        if (la_root / ".env").is_file():
            return la_root
    user_root = Path.home() / ".report-skill"
    if (user_root / ".env").is_file():
        return user_root
    # Nothing found — default to cwd. The receiver hasn't created .env yet,
    # but env vars (REPORT_API_*) can still satisfy pydantic-settings.
    return Path.cwd()


PROJECT_ROOT = _resolve_root()
ENV_PATH = PROJECT_ROOT / ".env"
CACHE_DIR = PROJECT_ROOT / ".skill-cache"


def _export_extra_env_vars() -> None:
    """v0.16.0 — surface non-Settings keys from .env into the process env.

    Several knobs are read via os.environ only (REPORT_FRONTEND_URL,
    SKILL_LLM_PROVIDER/MODEL, SKILL_BRIDGE_TIMEOUT, OLLAMA_*, …).
    .env.example documents them in the same file as the Settings fields, so
    users naturally set them there — and they silently did nothing
    (fresh-machine audit M10). Parse the resolved .env once and setdefault()
    each key: real process-env values always win; never raises.
    """
    try:
        if not ENV_PATH.is_file():
            return
        # utf-8-sig: PowerShell 5.1 `Out-File -Encoding utf8` writes a BOM,
        # which otherwise glues onto the first key and disables it.
        for raw in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and not key.startswith("#"):
                _os.environ.setdefault(key, value)
    except Exception:  # noqa: BLE001 — config bootstrap must never crash
        pass


_export_extra_env_vars()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_PATH),
        # utf-8-sig (v0.16.0): a UTF-8 BOM glued onto the first key silently
        # disabled it — and the first key is always REPORT_API_BASE_URL, so a
        # PS5.1 `Out-File -Encoding utf8` re-save made the client silently
        # point at the localhost default (fresh-machine audit M9).
        env_file_encoding="utf-8-sig",
        extra="ignore",
    )

    report_api_base_url: str = "http://localhost:3000/api"
    report_api_workspace_slug: str = "dx"
    report_api_email: str
    report_api_password: str
    # REPORT_BACKEND_PATH is dev-maintainer-only — only consumed by `catalog sync`
    # to invoke the backend's Python and extract content_schema_for(). Receivers
    # use the bundled snapshot and never reach this code. Blank by default so a
    # mis-config raises a clear missing-path error instead of leaking d:/ReportArchive.
    report_backend_path: Path = Path("")

    # ---- transport (v0.16.0 — corporate-network knobs) ------------------- #
    # trust_env=False by default: HTTP(S)_PROXY vars (ubiquitous on corporate
    # Windows) otherwise routed even intranet/localhost API traffic through
    # the proxy and broke every call (audit M1). Set true to opt back in to
    # proxy-from-env (httpx then also honors NO_PROXY).
    report_api_trust_env: bool = False
    # Path to a corporate root-CA bundle (PEM) for https servers behind TLS
    # inspection / self-signed certs (audit N1). Blank = certifi default.
    report_api_ca_bundle: str = ""
    # Last-resort escape hatch: disable TLS verification entirely.
    report_api_verify_tls: bool = True

    skill_ai_tier: Optional[Literal["S", "M", "W"]] = None

    @field_validator("skill_ai_tier", mode="before")
    @classmethod
    def _empty_string_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v


# Lazy singleton — pydantic validation deferred until first .attribute access.
# This is what lets `report-skill --help` and `report-skill --version` work
# even when REPORT_API_PASSWORD / REPORT_API_EMAIL are missing. Validation
# still fires on the first real call (eg. ReportArchiveClient instantiation).
class _SettingsProxy:
    """Defers Settings() instantiation until first attribute access.

    On validation failure, prints a human-friendly message naming the
    .env file path + which vars are missing, then exits 2 — instead of
    dumping a pydantic ValidationError traceback.
    """
    _impl: Optional[Settings] = None

    def _resolve(self) -> Settings:
        if self._impl is None:
            try:
                self._impl = Settings()
            except Exception as e:  # pydantic ValidationError or env file IO
                import sys as _sys
                # pydantic v2: walk e.errors() (the str(e) single-line parse
                # from v1 never matched v2's multi-line format — audit M8).
                missing = []
                errors_fn = getattr(e, "errors", None)
                if callable(errors_fn):
                    try:
                        for err in errors_fn():
                            if err.get("type") == "missing" and err.get("loc"):
                                missing.append(str(err["loc"][0]).upper())
                    except Exception:  # noqa: BLE001 — message-building only
                        pass
                message = (
                    "report-skill: configuration error\n"
                    f"  .env path        : {ENV_PATH}  (exists={ENV_PATH.is_file()})\n"
                    f"  REPORT_SKILL_ENV : {_os.environ.get('REPORT_SKILL_ENV') or '(unset)'}\n"
                    + (f"  missing vars     : {', '.join(missing)}\n" if missing else "")
                    + "\nFix: edit the .env above, or set REPORT_SKILL_ENV to a valid .env path,\n"
                    "or pass the missing vars in the process env.\n"
                )
                _sys.stderr.write("\n" + message)
                err = ConfigError(message)
                err.missing_vars = missing  # type: ignore[attr-defined]
                raise err from e
        return self._impl

    def __getattr__(self, name: str):
        if name.startswith("_") or name in {"_impl", "_resolve"}:
            raise AttributeError(name)
        return getattr(self._resolve(), name)


settings = _SettingsProxy()  # type: ignore[assignment]
