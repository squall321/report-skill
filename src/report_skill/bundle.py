"""Cross-instance report bundle — pack a fetched report + its referenced
media files into a zip that can be replayed on a different ReportArchive
instance via `report import`.

The wire format:

    bundle.zip
    ├── payload.json          # ReportCreate-ready (server fields stripped)
    └── files/
        ├── manifest.json     # [{file_id, filename, mime_type, size}]
        ├── f_abc123.png      # raw bytes, named by ORIGINAL file_id
        └── ...

On import: unpack → re-upload each file (gets a NEW file_id from the
target server) → walk the payload swapping every old file_id → POST
/reports.

This lives at the report-skill layer (not the backend) so it works
across any two ReportArchive instances without coordinated changes.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Iterable

from report_skill.client import ApiError, ReportArchiveClient


# --------------------------------------------------------------------------- #
# JSON-tree walks (find / swap file_id values at any depth)
# --------------------------------------------------------------------------- #
def collect_file_ids(obj: Any) -> set[str]:
    """Recursively walk a JSON-like structure and collect every non-empty
    `file_id` string value. Handles nested objects (image content.file_id)
    AND arrays (attachment content.files[i].file_id).
    """
    found: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "file_id" and isinstance(v, str) and v:
                found.add(v)
            else:
                found |= collect_file_ids(v)
    elif isinstance(obj, list):
        for item in obj:
            found |= collect_file_ids(item)
    return found


def swap_file_ids(obj: Any, mapping: dict[str, str]) -> None:
    """In-place swap of every `file_id` string value via the mapping dict.

    Ids not present in `mapping` are left unchanged — callers can audit
    those after the walk to detect missing files.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "file_id" and isinstance(v, str) and v in mapping:
                obj[k] = mapping[v]
            else:
                swap_file_ids(v, mapping)
    elif isinstance(obj, list):
        for item in obj:
            swap_file_ids(item, mapping)


# --------------------------------------------------------------------------- #
# Strip server-managed fields so a fetched report can be POSTed elsewhere
# --------------------------------------------------------------------------- #
_TOP_KEEP = {"title", "report_date", "tags", "phase", "lifecycle",
             "template_id", "template_version"}
_PAGE_KEEP = {"template_id", "template_version", "name", "content",
              "extra_blocks", "blocks_order"}


def ready_for_recreate(report: dict) -> dict:
    """Strip server-managed fields (id, owner_id, created_at, revision, ...)
    from a fetched report so it matches the ReportCreate POST shape."""
    out: dict = {k: report[k] for k in _TOP_KEEP if k in report}
    pages_out: list[dict] = []
    for p in report.get("pages") or []:
        page_out = {k: p[k] for k in _PAGE_KEEP if k in p}
        pages_out.append(page_out)
    out["pages"] = pages_out
    return out


# --------------------------------------------------------------------------- #
# Pack — fetch report, download each referenced file, write bundle.zip
# --------------------------------------------------------------------------- #
def pack_report_bundle(
    client: ReportArchiveClient,
    report_id: int,
    out_zip: Path,
    *,
    log=lambda msg: None,
) -> dict:
    """Fetch report `report_id` from the connected server, download every
    referenced file, and write a self-contained bundle.zip to `out_zip`.

    Returns a small summary dict {report_id, pages, files, payload_size,
    bundle_size} for callers that want to report progress.
    """
    raw = client.get(f"/reports/{report_id}")
    payload = ready_for_recreate(raw)

    file_ids = sorted(collect_file_ids(payload))
    log(f"found {len(file_ids)} unique file_id reference(s)")

    manifest: list[dict] = []
    file_blobs: dict[str, tuple[bytes, str, str]] = {}
    missing: list[str] = []
    for fid in file_ids:
        try:
            data, filename, mime_type = client.download_file(fid)
        except ApiError as e:
            log(f"  ! {fid}: download failed — {e}")
            missing.append(fid)
            continue
        file_blobs[fid] = (data, filename, mime_type)
        manifest.append({
            "file_id": fid,
            "filename": filename,
            "mime_type": mime_type,
            "size": len(data),
        })
        log(f"  ok {fid}  ({filename}, {len(data)} B)")

    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("payload.json", json.dumps(payload, ensure_ascii=False, indent=2))
        zf.writestr("files/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for fid, (data, _filename, _mime) in file_blobs.items():
            # Use the file_id as the archive entry name so import can find
            # bytes by id alone (filenames may collide or contain odd chars).
            zf.writestr(f"files/{fid}", data)

    return {
        "report_id": report_id,
        "title": payload.get("title"),
        "pages": len(payload.get("pages") or []),
        "files": len(manifest),
        "missing_files": missing,
        "bundle_size": out_zip.stat().st_size,
    }


# --------------------------------------------------------------------------- #
# Unpack — re-upload each file, swap ids, POST /reports
# --------------------------------------------------------------------------- #
def import_bundle(
    client: ReportArchiveClient,
    bundle_path: Path,
    *,
    log=lambda msg: None,
) -> dict:
    """Replay a bundle on the connected server.

    Steps: extract → for each file in manifest, re-upload to get a NEW
    file_id → swap every old file_id in payload → POST /reports.

    Returns the created Report record (same shape as `client.create_report`).
    """
    import tempfile

    with tempfile.TemporaryDirectory(prefix="rs-import-") as tmp_dir:
        tmp = Path(tmp_dir)
        with zipfile.ZipFile(bundle_path, "r") as zf:
            zf.extractall(tmp)

        payload_path = tmp / "payload.json"
        if not payload_path.is_file():
            raise FileNotFoundError("bundle missing payload.json")
        payload = json.loads(payload_path.read_text(encoding="utf-8"))

        manifest_path = tmp / "files" / "manifest.json"
        manifest: list[dict] = []
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        id_map: dict[str, str] = {}
        for entry in manifest:
            old_id = entry.get("file_id")
            if not old_id:
                continue
            src = tmp / "files" / old_id
            if not src.is_file():
                log(f"  ! {old_id}: missing from bundle/files/, skipping")
                continue
            # The original filename + mime drive the multipart payload so
            # the new server stores the file as it was originally named.
            filename = entry.get("filename") or src.name
            mime_type = entry.get("mime_type") or "application/octet-stream"
            # Stage the bytes under the original filename so upload_file's
            # multipart sees the real name (it uses Path.name).
            staged = tmp / "files" / filename
            if staged != src:
                staged.write_bytes(src.read_bytes())
            meta = client.upload_file(staged, mime_type=mime_type)
            new_id = meta.get("file_id")
            if not new_id:
                log(f"  ! {old_id}: upload returned no file_id; skipping swap")
                continue
            id_map[old_id] = new_id
            log(f"  ok {old_id} → {new_id}  ({filename})")

        swap_file_ids(payload, id_map)
        created = client.create_report(payload)
    return created


def collect_unmapped(payload: Any, mapping: dict[str, str]) -> list[str]:
    """For diagnostics after a swap: list any file_id still referenced in
    the payload that did NOT appear in `mapping`. Caller can warn or fail.
    """
    return sorted(fid for fid in collect_file_ids(payload) if fid not in mapping)
