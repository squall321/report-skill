"""Settings — loaded from .env (and the process env)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env discovery — try, in order:
#   1. $REPORT_SKILL_ENV         (explicit override)
#   2. ./.env                    (the receiver's cwd — install.ps1 writes here)
#   3. <repo-root>/.env          (dev mode — parent of `src/`)
#   4. ~/.report-skill/.env      (per-user fallback)
# Same precedence for CACHE_DIR (.skill-cache) so the receiver's cache lives
# next to their .env, not deep inside site-packages.
import os as _os


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
    user_root = Path.home() / ".report-skill"
    if (user_root / ".env").is_file():
        return user_root
    # Nothing found — default to cwd. The receiver hasn't created .env yet,
    # but env vars (REPORT_API_*) can still satisfy pydantic-settings.
    return Path.cwd()


PROJECT_ROOT = _resolve_root()
ENV_PATH = PROJECT_ROOT / ".env"
CACHE_DIR = PROJECT_ROOT / ".skill-cache"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_PATH),
        env_file_encoding="utf-8",
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
                missing = []
                for line in str(e).splitlines():
                    s = line.strip()
                    if s.lower().startswith("report_api_") and "field required" in s.lower():
                        missing.append(s.split()[0].upper())
                _sys.stderr.write(
                    "\nreport-skill: configuration error\n"
                    f"  .env path        : {ENV_PATH}  (exists={ENV_PATH.is_file()})\n"
                    f"  REPORT_SKILL_ENV : {_os.environ.get('REPORT_SKILL_ENV') or '(unset)'}\n"
                )
                if missing:
                    _sys.stderr.write(f"  missing vars     : {', '.join(missing)}\n")
                _sys.stderr.write(
                    "\nFix: edit the .env above, or set REPORT_SKILL_ENV to a valid .env path,\n"
                    "or pass the missing vars in the process env.\n"
                )
                raise SystemExit(2)
        return self._impl

    def __getattr__(self, name: str):
        if name.startswith("_") or name in {"_impl", "_resolve"}:
            raise AttributeError(name)
        return getattr(self._resolve(), name)


settings = _SettingsProxy()  # type: ignore[assignment]
