"""VideoAdapter — references uploaded video files by file_id.

Schema allows empty `files` (minItems:0), so caption-only mode is valid.
Never invents file_ids.
"""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import truncate

_FILE_KEYS = ("files", "file_ids")
_FLAGS = ("autoplay", "loop", "muted")


def _coerce_files(value: Any) -> list[dict]:
    out: list[dict] = []
    if not isinstance(value, list):
        return out
    for entry in value:
        if isinstance(entry, str) and entry.strip():
            out.append({"file_id": entry.strip()})
        elif isinstance(entry, dict):
            fid = entry.get("file_id") or entry.get("id")
            if not (isinstance(fid, str) and fid.strip()):
                raise NormalizeError(
                    "video: file entry missing file_id; upload via POST /api/files first"
                )
            item: dict = {"file_id": fid.strip()}
            for k in ("filename", "mime_type"):
                if isinstance(entry.get(k), str):
                    item[k] = entry[k][:255 if k == "filename" else 100]
            if isinstance(entry.get("size"), int):
                item["size"] = entry["size"]
            out.append(item)
    return out


class VideoAdapter(WidgetAdapter):
    type = "video"

    def normalize(self, raw: Any, props: dict) -> dict:
        out: dict = {"files": []}
        if isinstance(raw, str):
            text = raw.strip()
            if text:
                out["caption"] = truncate(text, 200)
            return out
        if isinstance(raw, list):
            out["files"] = _coerce_files(raw)
            return out
        if isinstance(raw, dict):
            files_src = next((raw[k] for k in _FILE_KEYS if k in raw), None)
            if files_src is not None:
                out["files"] = _coerce_files(files_src)
            elif any(k in raw for k in ("url", "path", "src")) and "caption" not in raw:
                raise NormalizeError(
                    "video: paths/URLs not accepted; upload via POST /api/files first to get file_ids"
                )
            if isinstance(raw.get("caption"), str):
                out["caption"] = truncate(raw["caption"], 200)
            if isinstance(raw.get("max_count"), int):
                out["max_count"] = raw["max_count"]
            for flag in _FLAGS:
                if isinstance(raw.get(flag), bool):
                    out[flag] = raw[flag]
            return out
        raise NormalizeError(f"video: unsupported input type {type(raw).__name__}")

    def fallback_to(self) -> str | None:
        return "rich_text"
