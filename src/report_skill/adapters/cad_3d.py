"""Cad3dAdapter — references a single uploaded 3D model file by file_id.

Content schema uses a single `file_id` string (not a list). file_id is not
required at content level, so caption-only mode is valid. If multiple are
supplied (file_ids list), take the first silently. Never invents file_ids.
"""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import truncate


def _first_file_id(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        for entry in value:
            if isinstance(entry, str) and entry.strip():
                return entry.strip()
            if isinstance(entry, dict):
                fid = entry.get("file_id") or entry.get("id")
                if isinstance(fid, str) and fid.strip():
                    return fid.strip()
    if isinstance(value, dict):
        fid = value.get("file_id") or value.get("id")
        if isinstance(fid, str) and fid.strip():
            return fid.strip()
    return None


class Cad3dAdapter(WidgetAdapter):
    type = "cad_3d"

    def normalize(self, raw: Any, props: dict) -> dict:
        out: dict = {}
        if isinstance(raw, str):
            text = raw.strip()
            if text:
                out["caption"] = truncate(text, 200)
            return out
        if isinstance(raw, list):
            fid = _first_file_id(raw)
            if fid:
                out["file_id"] = fid
            return out
        if isinstance(raw, dict):
            fid = _first_file_id(raw.get("file_id")) or _first_file_id(
                raw.get("file_ids") or raw.get("files")
            )
            if fid:
                out["file_id"] = fid
            elif any(k in raw for k in ("url", "path", "src")) and "caption" not in raw:
                raise NormalizeError(
                    "cad_3d: paths/URLs not accepted; upload via POST /api/files first to get a file_id"
                )
            if isinstance(raw.get("caption"), str):
                out["caption"] = truncate(raw["caption"], 200)
            if isinstance(raw.get("loaded_filename"), str):
                out["loaded_filename"] = raw["loaded_filename"][:255]
            for k in ("view_state", "hidden_parts", "wireframe_parts"):
                if k in raw:
                    out[k] = raw[k]
            return out
        raise NormalizeError(f"cad_3d: unsupported input type {type(raw).__name__}")

    def fallback_to(self) -> str | None:
        return "rich_text"
