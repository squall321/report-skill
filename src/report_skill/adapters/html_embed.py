"""HTML embed adapter — wraps an uploaded HTML file (file_id).

The schema does NOT permit raw HTML strings in `content` — embeds must
reference a server-uploaded file. Raw-string input is therefore rejected
with NormalizeError so the orchestrator's fallback chain can route it to
rich_text instead of producing schema-invalid content.
"""
from __future__ import annotations

from typing import Any

from report_skill.adapters.base import NormalizeError, WidgetAdapter
from report_skill.repair import truncate

_PASSTHROUGH = (
    "caption_skip_autofill", "bundle_id", "entry_path", "display",
    "title", "description", "cover_file_id",
    # v0.9.1 — RA defcb74 caption color tokens
    "caption_color", "caption_html",
)


class HtmlEmbedAdapter(WidgetAdapter):
    type = "html_embed"

    def normalize(self, raw: Any, props: dict) -> dict:
        if isinstance(raw, str):
            raise NormalizeError(
                "html_embed needs an uploaded file (POST /api/files); "
                "raw HTML strings aren't allowed in content. "
                "Pass {file_id, filename, height_px?, caption?} instead."
            )

        if isinstance(raw, dict):
            file_id = raw.get("file_id")
            if not file_id:
                if raw.get("html") or raw.get("body") or raw.get("content"):
                    raise NormalizeError(
                        "html_embed cannot accept raw HTML; upload as a file first "
                        "via POST /api/files and pass file_id."
                    )
                if raw.get("url") or raw.get("path") or raw.get("src"):
                    raise NormalizeError(
                        "html_embed cannot fetch URLs/paths; upload the HTML "
                        "via POST /api/files and pass file_id."
                    )
                # caption-only (or bundle metadata only) — schema allows it (file_id not required)
                if raw.get("caption") or any(k in raw for k in _PASSTHROUGH):
                    out: dict = {}
                    if raw.get("caption"):
                        out["caption"] = truncate(str(raw["caption"]), 200)
                    for k in _PASSTHROUGH:
                        if k in raw:
                            out[k] = raw[k]
                    return out
                raise NormalizeError("html_embed: missing file_id (and no caption)")

            out: dict = {"file_id": str(file_id)}
            if raw.get("filename"):
                out["filename"] = truncate(str(raw["filename"]), 255)
            if isinstance(raw.get("height_px"), int):
                out["height_px"] = max(60, min(4000, raw["height_px"]))
            if raw.get("caption"):
                out["caption"] = truncate(str(raw["caption"]), 200)
            for k in _PASSTHROUGH:
                if k in raw:
                    out[k] = raw[k]
            return out

        raise NormalizeError(f"html_embed: unsupported input type {type(raw).__name__}")

    def fallback_to(self) -> str:
        return "rich_text"
