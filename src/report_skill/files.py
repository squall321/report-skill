"""Upload local files to ReportArchive and resolve them to `file_id` values.

The image / video / attachment / cad_3d adapters in `report_skill.adapters`
all require `{"file_id": "<uuid>"}` content — they do not accept raw paths
or URLs. This module is the bridge: take a path on disk, push it through
`POST /files`, and return a typed `UploadedFile` ready to drop into a draft.

Public API:
    upload(path, *, client=None) -> UploadedFile
    upload_many(paths, *, client=None) -> list[UploadedFile]
    is_image(path) -> bool
    is_video(path) -> bool
    is_cad(path)   -> bool
    detect_widget_type(path) -> "image" | "video" | "cad_3d" | "attachment"

Pass an existing `ReportArchiveClient` via `client=` to share auth across
many uploads in one batch; otherwise a fresh client is created per call.
"""
from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Union

from report_skill.client import ReportArchiveClient

PathLike = Union[str, Path]

# Extension classification — lowercase, leading dot. Kept aligned with the
# backend's own routing buckets in `app/modules/files/routes.py` so a file
# accepted here behaves consistently when the upload hits the server.
_IMAGE_EXT: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".heic", ".heif", ".avif",
})
_VIDEO_EXT: frozenset[str] = frozenset({
    ".mp4", ".m4v", ".webm", ".mov", ".ogg", ".ogv", ".mkv", ".avi",
})
_CAD_EXT: frozenset[str] = frozenset({
    ".glb", ".gltf",
    ".stl", ".obj", ".ply", ".fbx", ".3mf",
    ".step", ".stp", ".iges", ".igs",
})


@dataclass
class UploadedFile:
    """Successful POST /files response, narrowed to the bits skills need."""
    file_id: str
    filename: str
    size: int
    mime_type: str
    raw_response: dict = field(default_factory=dict)

    def as_block_content(self) -> dict:
        """Convenience: shape expected by image / video / attachment / cad_3d adapters."""
        return {"file_id": self.file_id}


# --------------------------------------------------------------------------- #
# extension helpers
# --------------------------------------------------------------------------- #
def _ext(path: PathLike) -> str:
    return Path(path).suffix.lower()


def is_image(path: PathLike) -> bool:
    return _ext(path) in _IMAGE_EXT


def is_video(path: PathLike) -> bool:
    return _ext(path) in _VIDEO_EXT


def is_cad(path: PathLike) -> bool:
    return _ext(path) in _CAD_EXT


def detect_widget_type(path: PathLike) -> str:
    """Map an on-disk file to the widget type whose adapter should consume it.

    Returns one of: 'image', 'video', 'cad_3d', 'attachment' (catch-all).
    """
    if is_image(path):
        return "image"
    if is_video(path):
        return "video"
    if is_cad(path):
        return "cad_3d"
    return "attachment"


def guess_mime(path: PathLike) -> str:
    """Best-effort MIME type from the extension; defaults to octet-stream."""
    return mimetypes.guess_type(Path(path).name)[0] or "application/octet-stream"


# --------------------------------------------------------------------------- #
# upload
# --------------------------------------------------------------------------- #
def _from_response(payload: dict) -> UploadedFile:
    """Coerce a /files response into UploadedFile, tolerating field aliases."""
    return UploadedFile(
        file_id=str(payload.get("file_id") or payload.get("id") or ""),
        filename=str(payload.get("filename") or payload.get("name") or ""),
        size=int(payload.get("size") or payload.get("byte_size") or 0),
        mime_type=str(payload.get("mime_type") or payload.get("content_type") or ""),
        raw_response=dict(payload),
    )


def upload(
    path: PathLike,
    *,
    client: Optional[ReportArchiveClient] = None,
) -> UploadedFile:
    """Upload a single file. Returns the parsed UploadedFile."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"file not found: {p}")

    owned = client is None
    c = client or ReportArchiveClient()
    try:
        payload = c.upload_file(p, mime_type=guess_mime(p))
    finally:
        if owned:
            c.close()

    uf = _from_response(payload)
    if not uf.file_id:
        raise RuntimeError(f"POST /files succeeded but returned no file_id: {payload!r}")
    return uf


def upload_many(
    paths: Iterable[PathLike],
    *,
    client: Optional[ReportArchiveClient] = None,
) -> list[UploadedFile]:
    """Upload every path in order, sharing one auth session.

    Stops on the first failure (raises). Caller decides retry policy.
    """
    paths = list(paths)
    if not paths:
        return []

    owned = client is None
    c = client or ReportArchiveClient()
    out: list[UploadedFile] = []
    try:
        for p in paths:
            out.append(upload(p, client=c))
    finally:
        if owned:
            c.close()
    return out
