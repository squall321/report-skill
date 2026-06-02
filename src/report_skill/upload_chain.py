"""Pre-processor: turn media-block inputs with local file paths into
fully-realized inputs carrying server-side `file_id`s.

The skill's media adapters (image / video / attachment / cad_3d) reject
raw `local_path` / `path` / `url` inputs because the backend's content
schema requires `file_id` strings issued by `POST /api/files`. This
module bridges that gap: it walks a draft's blocks (template + extras),
detects media-block inputs that name a local file, uploads it, and
rewrites the input in place with the resulting `file_id`.

Designed to run inside the CLI just before `orchestrator.normalize_report`.
Pure auxiliary — no orchestrator coupling.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

MEDIA_TYPES = frozenset({"image", "video", "attachment", "cad_3d", "html_embed"})


def is_local_path_value(value: Any) -> Optional[Path]:
    """Heuristic — return a Path if value looks like a local file reference.

    Recognized forms:
      - `"d:/some/file.png"` / `"./local.mp4"` / `"~/Downloads/x.glb"`
      - `{"local_path": "..."}` / `{"path": "..."}` / `{"src": "..."}`
    """
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.startswith(("http://", "https://", "file://")):
            return None
        p = Path(s).expanduser()
        if p.is_file():
            return p
    if isinstance(value, dict):
        for k in ("local_path", "path", "src", "file_path"):
            if k in value:
                candidate = is_local_path_value(value[k])
                if candidate:
                    return candidate
    return None


def _upload_one(path: Path, *, client) -> dict:
    """Returns {file_id, filename, size, mime_type} from POST /files."""
    meta = client.upload_file(str(path))
    # backend's FileMeta exposes `id`; older payloads might use `file_id`
    file_id = meta.get("file_id") or meta.get("id")
    return {
        "file_id": file_id,
        "filename": meta.get("filename") or path.name,
        "size": meta.get("size"),
        "mime_type": meta.get("mime_type"),
    }


def _rewrite_to_files_shape(
    raw: Any, uploaded: list[dict], original_value: Any,
    *, widget_type: str = ""
) -> Any:
    """Build a media-adapter-friendly input from the uploaded file metas.

    For a dict input (which preserves caption/alt/etc.), splice in file_ids
    while preserving other keys. For a bare string path, return a minimal
    `{file_ids: [...]}` dict.

    html_embed is single-file — emits `{file_id, filename, ...}` flat,
    NOT a `{file_ids: [...]}` list, because its content schema only takes
    one file. Other media types are multi-file.
    """
    if widget_type == "html_embed":
        first = uploaded[0] if uploaded else {}
        if isinstance(raw, dict):
            out = dict(raw)
            for k in ("local_path", "path", "src", "file_path"):
                out.pop(k, None)
            out.update({k: v for k, v in first.items() if v is not None})
            return out
        return {k: v for k, v in first.items() if v is not None}

    if isinstance(raw, dict):
        out = dict(raw)
        # Drop the local-only keys; replace with file_ids
        for k in ("local_path", "path", "src", "file_path"):
            out.pop(k, None)
        # If caller already had files/file_ids, append; else create.
        existing = out.get("file_ids") or out.get("files") or []
        out["file_ids"] = list(existing) + uploaded
        return out
    return {"file_ids": uploaded}


def resolve_block_input(
    block_type: str, raw: Any, *, client, log=None
) -> tuple[Any, list[str]]:
    """If `raw` is a media-block input referencing local files, upload and
    return rewritten input + list of uploaded filenames (for logging).

    Idempotent — if no local path is detected, returns `(raw, [])` unchanged.
    Only fires for widget types in MEDIA_TYPES.
    """
    if block_type not in MEDIA_TYPES:
        return raw, []

    # Single path/dict
    p = is_local_path_value(raw)
    if p is not None:
        uploaded = [_upload_one(p, client=client)]
        if log:
            log(f"uploaded {p.name} → file_id={uploaded[0]['file_id']}")
        return _rewrite_to_files_shape(raw, uploaded, raw, widget_type=block_type), [p.name]

    # List of paths
    if isinstance(raw, list):
        rewritten: list[Any] = []
        any_uploaded = False
        names: list[str] = []
        for item in raw:
            p = is_local_path_value(item)
            if p is None:
                rewritten.append(item)
                continue
            meta = _upload_one(p, client=client)
            if isinstance(item, dict):
                merged = {k: v for k, v in item.items()
                          if k not in ("local_path", "path", "src", "file_path")}
                merged.update(meta)
                rewritten.append(merged)
            else:
                rewritten.append(meta)
            any_uploaded = True
            names.append(p.name)
            if log:
                log(f"uploaded {p.name} → file_id={meta['file_id']}")
        if any_uploaded:
            return rewritten, names
        return raw, []

    # Dict with nested `file_ids` array containing paths
    if isinstance(raw, dict):
        for key in ("file_ids", "files"):
            entries = raw.get(key)
            if not isinstance(entries, list):
                continue
            new_entries: list[Any] = []
            any_uploaded = False
            names: list[str] = []
            for entry in entries:
                p = is_local_path_value(entry)
                if p is None:
                    new_entries.append(entry)
                    continue
                meta = _upload_one(p, client=client)
                if isinstance(entry, dict):
                    merged = {k: v for k, v in entry.items()
                              if k not in ("local_path", "path", "src", "file_path")}
                    merged.update(meta)
                    new_entries.append(merged)
                else:
                    new_entries.append(meta)
                any_uploaded = True
                names.append(p.name)
                if log:
                    log(f"uploaded {p.name} → file_id={meta['file_id']}")
            if any_uploaded:
                out = dict(raw)
                out[key] = new_entries
                return out, names

    return raw, []


def preupload_for_draft(
    *,
    client,
    blocks_input: dict[str, Any],
    block_types: dict[str, str],
    extras_input: Optional[list[dict]] = None,
    log=None,
) -> tuple[dict[str, Any], list[dict], list[str]]:
    """Walk a draft's blocks + extras, upload any local-file media references.

    Returns (rewritten_blocks_input, rewritten_extras_input, uploaded_filenames).
    Leaves non-media + non-local-path entries untouched.
    """
    out_blocks: dict[str, Any] = dict(blocks_input)
    uploaded_files: list[str] = []
    for bid, raw in list(out_blocks.items()):
        wtype = block_types.get(bid)
        if not wtype:
            continue
        new_raw, names = resolve_block_input(wtype, raw, client=client, log=log)
        out_blocks[bid] = new_raw
        uploaded_files.extend(names)

    out_extras: list[dict] = []
    for extra in (extras_input or []):
        wtype = extra.get("type")
        raw = extra.get("input") or extra.get("content")
        if wtype in MEDIA_TYPES and raw is not None:
            new_raw, names = resolve_block_input(wtype, raw, client=client, log=log)
            uploaded_files.extend(names)
            new_extra = dict(extra)
            if "input" in extra:
                new_extra["input"] = new_raw
            else:
                new_extra["content"] = new_raw
            out_extras.append(new_extra)
        else:
            out_extras.append(extra)

    return out_blocks, out_extras, uploaded_files
