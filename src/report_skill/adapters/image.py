"""ImageAdapter — references uploaded image files by file_id.

file_id values are assigned by the ReportArchive backend after POST /api/files.
This adapter never invents file_ids; if the caller has none it emits an empty
`files` list (schema allows minItems:0) so the widget still renders with caption.
"""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import truncate

_FILE_KEYS = ("files", "file_ids")


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
                    "image: file entry missing file_id; upload via POST /api/files first"
                )
            item: dict = {"file_id": fid.strip()}
            if isinstance(entry.get("caption"), str):
                item["caption"] = truncate(entry["caption"], 500)
            if isinstance(entry.get("alt"), str):
                item["alt"] = truncate(entry["alt"], 200)
            out.append(item)
    return out


class ImageAdapter(WidgetAdapter):
    type = "image"

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
                    "image: paths/URLs not accepted; upload via POST /api/files first to get file_ids"
                )
            if isinstance(raw.get("caption"), str):
                out["caption"] = truncate(raw["caption"], 200)
            if isinstance(raw.get("aspect_ratio"), str):
                out["aspect_ratio"] = raw["aspect_ratio"]
            if isinstance(raw.get("max_count"), int):
                out["max_count"] = raw["max_count"]
            return out
        raise NormalizeError(f"image: unsupported input type {type(raw).__name__}")

    def fallback_to(self) -> str | None:
        return "rich_text"
