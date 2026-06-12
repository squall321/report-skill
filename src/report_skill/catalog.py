"""Widget catalog cache + change tracking.

`sync()`  → fetch /api/widgets, run bridge to get content_schemas, compute
            per-widget hashes, write snapshot to .skill-cache/.
`diff()`  → compare the new snapshot against the previous one to produce a
            human-readable change report (added / removed / modified).

The hash is computed over a canonical-JSON dump of {props_schema,
content_schema}. So any backend-side change to either field bumps the
hash, which is exactly the signal the example-staleness logic depends on.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from report_skill.client import ReportArchiveClient
from report_skill.config import CACHE_DIR, settings

CACHE_FILE_CURRENT = CACHE_DIR / "widgets.json"
CACHE_FILE_PREVIOUS = CACHE_DIR / "widgets.previous.json"
TEMPLATES_CACHE_DIR = CACHE_DIR / "templates"

# Bundled snapshot — shipped inside the `report_skill` package so the
# skill works with zero `catalog sync` / `templates sync` calls. Pip
# install carries these along. Refreshed by re-running sync against a
# live backend; until then, this baseline is what loaders see.
_PACKAGE_DIR = Path(__file__).resolve().parent
BUNDLED_WIDGETS = _PACKAGE_DIR / "data" / "widgets.snapshot.json"
BUNDLED_TEMPLATES_DIR = _PACKAGE_DIR / "data" / "templates"
BRIDGE_SCRIPT = Path(__file__).resolve().parents[2] / "bridge" / "extract_content_schemas.py"


def _canonical_hash(obj: Any) -> str:
    """Stable 16-char SHA256 of a JSON-able structure."""
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _backend_python() -> Path:
    """Resolve the python interpreter inside the ReportArchive backend venv."""
    backend = settings.report_backend_path
    # v0.16.0 — consumer guard (fresh-machine audit M4): catalog sync is a
    # DEV-MAINTAINER operation. On receiver installs REPORT_BACKEND_PATH is
    # blank (coercing to Path('.')), which used to produce a cwd-dependent
    # "not a valid backend root: <random dir>" error.
    if str(backend) in ("", "."):
        raise RuntimeError(
            "catalog sync is a dev-maintainer operation: it needs the "
            "ReportArchive BACKEND SOURCE on this machine "
            "(REPORT_BACKEND_PATH=<repo>/backend). Receiver installs ship a "
            "bundled widget snapshot that is already active — no sync needed."
        )
    candidates = [
        backend / "venv" / "Scripts" / "python.exe",  # Windows
        backend / "venv" / "bin" / "python",           # Posix
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError(
        f"Could not find backend python in {backend}/venv. "
        f"Tried: {[str(c) for c in candidates]}"
    )


def _run_bridge() -> dict:
    """Execute the bridge script using the backend venv. Returns parsed JSON."""
    py = _backend_python()
    backend = settings.report_backend_path
    proc = subprocess.run(
        [str(py), str(BRIDGE_SCRIPT), "--backend-root", str(backend)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"bridge failed (exit {proc.returncode}):\n"
            f"--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"bridge output not valid JSON: {e}\n{proc.stdout[:500]}")


# --------------------------------------------------------------------------- #
# Snapshot building
# --------------------------------------------------------------------------- #
@dataclass
class WidgetEntry:
    type: str
    label: Optional[str]
    description: Optional[str]
    has_content: bool
    props_schema: dict
    default_props: dict
    content_schema: Optional[dict]
    content_schema_error: Optional[str]
    hash: str

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "label": self.label,
            "description": self.description,
            "has_content": self.has_content,
            "props_schema": self.props_schema,
            "default_props": self.default_props,
            "content_schema": self.content_schema,
            "content_schema_error": self.content_schema_error,
            "hash": self.hash,
        }


@dataclass
class CatalogSnapshot:
    fetched_at: str
    api_base_url: str
    schema_version: str
    widgets: dict[str, WidgetEntry] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "fetched_at": self.fetched_at,
            "api_base_url": self.api_base_url,
            "schema_version": self.schema_version,
            "widgets": {k: v.to_dict() for k, v in self.widgets.items()},
        }


def build_snapshot(client: ReportArchiveClient, bridge: dict) -> CatalogSnapshot:
    """Merge HTTP catalog and bridge output into a single hashed snapshot."""
    http = client.fetch_widgets()
    http_by_type: dict[str, dict] = {w["type"]: w for w in http.get("widgets", [])}
    bridge_widgets: dict[str, dict] = bridge.get("widgets", {})

    all_types = sorted(set(http_by_type) | set(bridge_widgets))
    entries: dict[str, WidgetEntry] = {}

    for wtype in all_types:
        h = http_by_type.get(wtype, {})
        b = bridge_widgets.get(wtype, {})
        props_schema = b.get("props_schema") or h.get("props_schema") or {}
        content_schema = b.get("content_schema")
        hash_input = {
            "props_schema": props_schema,
            "content_schema": content_schema,
        }
        entries[wtype] = WidgetEntry(
            type=wtype,
            label=h.get("label") or b.get("label"),
            description=h.get("description") or b.get("description"),
            has_content=bool(h.get("has_content", b.get("has_content", False))),
            props_schema=props_schema,
            default_props=h.get("default_props") or b.get("default_props") or {},
            content_schema=content_schema,
            content_schema_error=b.get("content_schema_error"),
            hash=_canonical_hash(hash_input),
        )

    return CatalogSnapshot(
        fetched_at=datetime.now(timezone.utc).isoformat(),
        api_base_url=settings.report_api_base_url,
        schema_version=http.get("schema_version", "widget-v1"),
        widgets=entries,
    )


# --------------------------------------------------------------------------- #
# Persistence + diff
# --------------------------------------------------------------------------- #
def _load_snapshot(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _save_snapshot(snapshot: CatalogSnapshot) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Rotate current -> previous before overwriting.
    if CACHE_FILE_CURRENT.is_file():
        CACHE_FILE_PREVIOUS.write_text(
            CACHE_FILE_CURRENT.read_text(encoding="utf-8"), encoding="utf-8"
        )
    with CACHE_FILE_CURRENT.open("w", encoding="utf-8") as fh:
        json.dump(snapshot.to_dict(), fh, ensure_ascii=False, indent=2)
    return CACHE_FILE_CURRENT


@dataclass
class CatalogDiff:
    added: list[str]
    removed: list[str]
    modified: list[tuple[str, str, str]]  # (type, old_hash, new_hash)
    unchanged: list[str]

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.modified)


def diff_snapshots(old: Optional[dict], new: dict) -> CatalogDiff:
    new_widgets: dict[str, dict] = new.get("widgets", {})
    old_widgets: dict[str, dict] = (old or {}).get("widgets", {})

    new_types = set(new_widgets)
    old_types = set(old_widgets)

    added = sorted(new_types - old_types)
    removed = sorted(old_types - new_types)

    modified: list[tuple[str, str, str]] = []
    unchanged: list[str] = []
    for t in sorted(new_types & old_types):
        oh = old_widgets[t].get("hash", "?")
        nh = new_widgets[t].get("hash", "?")
        if oh != nh:
            modified.append((t, oh, nh))
        else:
            unchanged.append(t)

    return CatalogDiff(added=added, removed=removed, modified=modified, unchanged=unchanged)


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #
def sync() -> tuple[CatalogSnapshot, CatalogDiff]:
    """Fetch fresh catalog, run bridge, persist, return (snapshot, diff)."""
    old = _load_snapshot(CACHE_FILE_CURRENT)
    bridge = _run_bridge()
    with ReportArchiveClient() as client:
        snapshot = build_snapshot(client, bridge)
    _save_snapshot(snapshot)
    diff = diff_snapshots(old, snapshot.to_dict())
    return snapshot, diff


def load_current() -> Optional[dict]:
    """Read the most recent snapshot. Falls back to the bundled baseline at
    `data/widgets.snapshot.json` when no live cache exists — this lets the
    skill operate against a remote ReportArchive with zero local `catalog
    sync` calls.

    Returns None only when neither the cache nor the bundled file exists
    (which would mean a corrupt install)."""
    cached = _load_snapshot(CACHE_FILE_CURRENT)
    if cached is not None:
        return cached
    if BUNDLED_WIDGETS.is_file():
        with BUNDLED_WIDGETS.open(encoding="utf-8") as fh:
            return json.load(fh)
    return None


# --------------------------------------------------------------------------- #
# Template cache — for offline export/import flows
# --------------------------------------------------------------------------- #
def template_cache_path(template_id: str, version: Optional[int] = None) -> Path:
    """Where a template is cached. version=None → latest.json filename."""
    safe_id = template_id.replace("/", "_")
    suffix = f"v{version}" if version is not None else "latest"
    return TEMPLATES_CACHE_DIR / f"{safe_id}.{suffix}.json"


def save_template(tpl: dict) -> Path:
    """Persist a fetched template under both its versioned + latest filename."""
    TEMPLATES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tid = tpl["template_id"]
    ver = tpl["version"]
    paths = [template_cache_path(tid, ver), template_cache_path(tid, None)]
    body = json.dumps(tpl, ensure_ascii=False, indent=2)
    for p in paths:
        p.write_text(body, encoding="utf-8")
    return paths[0]


def load_cached_template(template_id: str, version: Optional[int] = None) -> Optional[dict]:
    """Read a cached template. version=None → latest cached version.

    Falls back to the bundled baseline at `data/templates/` when no live
    cache hit — same minimal-setup principle as `load_current()`.
    """
    path = template_cache_path(template_id, version)
    if path.is_file():
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    # Bundled fallback
    safe_id = template_id.replace("/", "_")
    suffix = f"v{version}" if version is not None else "latest"
    bundled = BUNDLED_TEMPLATES_DIR / f"{safe_id}.{suffix}.json"
    if bundled.is_file():
        with bundled.open(encoding="utf-8") as fh:
            return json.load(fh)
    return None


def list_cached_templates() -> list[str]:
    """All cached template_ids (deduped from filenames)."""
    if not TEMPLATES_CACHE_DIR.is_dir():
        return []
    seen: set[str] = set()
    for p in TEMPLATES_CACHE_DIR.glob("*.json"):
        # filename = "<id>.latest.json" or "<id>.v<N>.json"
        stem = p.stem
        # Strip trailing .latest or .v<N>
        if stem.endswith(".latest"):
            seen.add(stem[:-len(".latest")])
        elif "." in stem:
            base = stem.rsplit(".", 1)[0]
            seen.add(base)
    return sorted(seen)


def sync_templates(client) -> tuple[int, int]:
    """Fetch every template (latest version) and cache it.

    Returns (templates_cached, errors). Safe to call repeatedly — each
    template overwrites its `latest.json` and writes a fresh versioned
    copy if the version is new.
    """
    items = client.fetch_templates()
    cached = 0
    errors = 0
    for t in items:
        tid = t.get("template_id") or t.get("id")
        if not tid:
            errors += 1
            continue
        try:
            full = client.fetch_template(tid)
            save_template(full)
            cached += 1
        except Exception:
            errors += 1
    return cached, errors
