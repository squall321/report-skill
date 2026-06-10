"""Local call telemetry + VOC bundle export for report-skill (v0.14.0).

Design rules
------------
- Telemetry must NEVER break the skill: every public function wraps its
  whole body in try/except and silently no-ops on failure.
- No import of report_skill.client here (client.py imports this module —
  the dependency is strictly one-way). report_skill.config is imported
  lazily inside export_voc() only, also wrapped in try/except.
- Storage: daily JSONL files under %LOCALAPPDATA%/report-skill/logs/
  (calls-YYYYMMDD.jsonl), one JSON object per line.
- recent() approach (documented choice): the JSONL files are the source of
  truth — today's file is read, plus yesterday's when today has fewer than
  ``n`` usable records. The in-process ring buffer (deque, maxlen=300) is
  used ONLY as a fallback when the files yield nothing (unreadable dir,
  failed writes). No merge/dedup between ring and files is attempted.
- Redaction: error_message is passed through _redact_text() (masks
  password/token/secret/authorization key=value pairs) and truncated to
  500 chars. args_keys carries key NAMES only — values never reach this
  module.
"""
from __future__ import annotations

import json
import os
import platform
import re
import threading
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

_BASE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "report-skill"
LOG_DIR = _BASE_DIR / "logs"
VOC_DIR = _BASE_DIR / "voc"

_RING: deque = deque(maxlen=300)
_LOCK = threading.Lock()

_ERROR_MESSAGE_MAX = 500
_SECRET_RE = re.compile(
    r"(password|token|secret|authorization)\s*[=:]\s*\S+",
    re.IGNORECASE,
)


# ---- internals ------------------------------------------------------------ #


def _redact_text(s: Any) -> Any:
    """Mask credential-looking substrings with "***".

    The WHOLE match (key + value, e.g. "password=hunter2") is replaced by
    "***" — the simplest rule that can never leak a value.
    """
    if not isinstance(s, str):
        return s
    return _SECRET_RE.sub("***", s)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _day_file(day: datetime) -> Path:
    return LOG_DIR / f"calls-{day:%Y%m%d}.jsonl"


def _append(record: dict) -> None:
    """Ring first (so the fallback survives a failed file write), then file."""
    with _LOCK:
        _RING.append(record)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with _day_file(datetime.now()).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue  # skip torn/corrupt lines
        if isinstance(rec, dict):
            records.append(rec)
    return records


def _read_recent_records(n: int) -> list:
    """Today's file, prepending yesterday's when today is short of n records."""
    today = datetime.now()
    records = _read_jsonl(_day_file(today))
    if len(records) < n:
        records = _read_jsonl(_day_file(today - timedelta(days=1))) + records
    return records


# ---- public API (NEVER raises) --------------------------------------------- #


def record_http(method: str, path: str, status_code: int, duration_ms: float,
                error_code: Optional[str] = None,
                error_message: Optional[str] = None) -> None:
    """Append one {"kind":"http"} record. Silently no-ops on any failure."""
    try:
        rec: dict = {
            "ts": _now_iso(),
            "kind": "http",
            "method": str(method).upper(),
            "path": str(path),
            "status": int(status_code),
            "duration_ms": round(float(duration_ms), 1),
            "ok": error_code is None and 200 <= int(status_code) < 400,
        }
        if error_code is not None:
            rec["error_code"] = str(error_code)
        if error_message is not None:
            rec["error_message"] = _redact_text(str(error_message))[:_ERROR_MESSAGE_MAX]
        _append(rec)
    except Exception:
        pass


def record_tool(tool: str, ok: bool, duration_ms: float,
                error_code: Optional[str] = None,
                error_message: Optional[str] = None,
                args_keys: Optional[list] = None) -> None:
    """Append one {"kind":"tool"} record. Silently no-ops on any failure."""
    try:
        rec: dict = {
            "ts": _now_iso(),
            "kind": "tool",
            "tool": str(tool),
            "ok": bool(ok),
            "duration_ms": round(float(duration_ms), 1),
        }
        if error_code is not None:
            rec["error_code"] = str(error_code)
        if error_message is not None:
            rec["error_message"] = _redact_text(str(error_message))[:_ERROR_MESSAGE_MAX]
        if args_keys is not None:
            # Key NAMES only — never values (caller passes sorted(args.keys())).
            rec["args_keys"] = [str(k) for k in args_keys]
        _append(rec)
    except Exception:
        pass


def recent(n: int = 50, errors_only: bool = False,
           kind: Optional[str] = None) -> list:
    """Last ``n`` matching records, NEWEST FIRST.

    Source of truth is the JSONL files (today + yesterday when today is
    short); the in-process ring buffer is used only when the files yield
    nothing. Returns [] on any failure.
    """
    try:
        limit = max(int(n), 0)
        try:
            records = _read_recent_records(limit or 1)
        except Exception:
            records = []
        if not records:
            with _LOCK:
                records = list(_RING)
        out = []
        for rec in reversed(records):  # newest first
            if kind is not None and rec.get("kind") != kind:
                continue
            if errors_only and rec.get("ok", True):
                continue
            out.append(rec)
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


def export_voc(note: Optional[str] = None) -> dict:
    """Write a VOC bundle (human .md + machine .json) under
    %LOCALAPPDATA%/report-skill/voc/ and return
    {"path", "json_path", "summary": {"version", "error_count", "call_count"}}.

    Never raises — on failure returns the same shape with empty paths.
    """
    try:
        try:
            from importlib.metadata import version as _pkg_version
            version = _pkg_version("report-skill")
        except Exception:
            version = "unknown"
        python_version = platform.python_version()
        os_name = platform.platform()
        # Lazy config import — never let a broken config kill the export,
        # and NEVER include email/password (base URL only).
        try:
            from report_skill.config import settings
            base_url = str(settings.report_api_base_url)
        except Exception:
            base_url = "unknown"

        errors = recent(20, errors_only=True)      # 시간 역순 (newest first)
        calls = recent(50)
        safe_note = _redact_text(note) if note else None

        now = datetime.now()
        stamp = f"{now:%Y%m%d-%H%M%S}"
        VOC_DIR.mkdir(parents=True, exist_ok=True)
        md_path = VOC_DIR / f"voc-{stamp}.md"
        json_path = VOC_DIR / f"voc-{stamp}.json"

        def _name(rec: dict) -> str:
            return str(rec.get("path") or rec.get("tool") or "?")

        def _status(rec: dict) -> str:
            if "status" in rec:
                return str(rec.get("status"))
            return "ok" if rec.get("ok") else "error"

        lines = [
            "# report-skill VOC 리포트",
            "",
            f"- 생성 시각: {now.astimezone().isoformat(timespec='seconds')}",
            f"- report-skill 버전: {version}",
            f"- Python: {python_version}",
            f"- OS: {os_name}",
            f"- 백엔드 URL: {base_url}",
            "",
            "## 사용자 메모",
            safe_note or "(없음)",
            "",
            f"## 최근 에러 (최대 20건, 최신순) — {len(errors)}건",
        ]
        if not errors:
            lines.append("(없음)")
        for rec in errors:
            lines.append(
                f"- {rec.get('ts', '?')} [{rec.get('kind', '?')}] {_name(rec)} "
                f"status={_status(rec)} code={rec.get('error_code', '-')} "
                f"msg={rec.get('error_message', '-')}"
            )
        lines += ["", f"## 최근 호출 요약 (최대 50건, 최신순) — {len(calls)}건"]
        if not calls:
            lines.append("(없음)")
        for rec in calls:
            lines.append(
                f"- {rec.get('ts', '?')} {rec.get('kind', '?')} {_name(rec)} "
                f"{_status(rec)} {rec.get('duration_ms', '?')}ms"
            )
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        payload = {
            "version": version,
            "python": python_version,
            "platform": os_name,
            "base_url": base_url,
            "note": safe_note,
            "errors": errors,
            "calls": calls,
        }
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        return {
            "path": str(md_path),
            "json_path": str(json_path),
            "summary": {
                "version": version,
                "error_count": len(errors),
                "call_count": len(calls),
            },
        }
    except Exception:
        return {
            "path": "",
            "json_path": "",
            "summary": {"version": "unknown", "error_count": 0, "call_count": 0},
        }
