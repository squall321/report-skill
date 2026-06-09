"""Deeper RA-vs-report-skill impact analysis.

Triggered by `scripts/watch_ra.ps1` when the cheap diff scan finds backend
Python changes. This script reads the actual diff against a baseline sha
and surfaces concrete gaps:

  1. New @router endpoints in RA — wrapped by report-skill client.py?
  2. New widget content_schema fields in RA registry.py — passed through
     by the matching adapter?
  3. New `raise HTTPException(403/409, "...")` Korean strings — detected
     by client.py _build_typed_error substring rules?

Usage:
  python scripts/check_ra_impact.py --since-sha v0.11.0
  python scripts/check_ra_impact.py --since-sha 60997a6 --ra-path d:/ReportArchive

This is the "first half" of the audit pattern that v0.4-v0.11 repeated by
hand. Run it BEFORE planning a new release so the surface gap is mechanically
surfaced, not discovered post-release.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


def run_git(args: list[str], cwd: Path) -> str:
    """Run a git command in the given repo and return stdout."""
    res = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if res.returncode != 0:
        sys.stderr.write(f"git {' '.join(args)} failed:\n{res.stderr}\n")
        return ""
    return res.stdout


def find_new_endpoints(ra_path: Path, since_sha: str) -> list[tuple[str, str, str]]:
    """Return [(file, method, path)] for @router.{get,post,patch,delete,put}
    decorators added between since_sha and HEAD."""
    diff = run_git(
        ["log", f"{since_sha}..HEAD", "-p", "--", "backend/**/routes.py"],
        ra_path,
    )
    out: list[tuple[str, str, str]] = []
    current_file = ""
    pat = re.compile(
        r'^\+\s*@(?:router|\w+_router)\.(get|post|patch|delete|put)\(\s*"([^"]*)"',
    )
    file_pat = re.compile(r"^\+\+\+ b/(.+)$")
    for line in diff.splitlines():
        m = file_pat.match(line)
        if m:
            current_file = m.group(1)
            continue
        m = pat.match(line)
        if m and current_file:
            out.append((current_file, m.group(1).upper(), m.group(2)))
    return out


def find_new_content_fields(ra_path: Path, since_sha: str) -> list[tuple[str, str]]:
    """Return [(widget_function, field_name)] for new content_schema fields
    added inside `def _<widget>_content` blocks."""
    diff = run_git(
        ["log", f"{since_sha}..HEAD", "-p", "--", "backend/app/widgets/registry.py"],
        ra_path,
    )
    out: list[tuple[str, str]] = []
    current_widget = ""
    widget_pat = re.compile(r"^@@.*\bdef _([a-z_]+)_content\b")
    field_pat = re.compile(r'^\+\s+"([a-z_]+)"\s*:')
    for line in diff.splitlines():
        m = widget_pat.search(line)
        if m:
            current_widget = m.group(1)
            continue
        m = field_pat.match(line)
        if m and current_widget:
            # Skip already-known noise (rows / items always present).
            if m.group(1) in {"rows", "items", "type"}:
                continue
            out.append((current_widget, m.group(1)))
    return out


def find_new_korean_403_strings(ra_path: Path, since_sha: str) -> list[tuple[str, str]]:
    """Return [(file, text)] for new `raise HTTPException(.., "<Korean...>")`
    strings (Korean = contains any Hangul code point)."""
    diff = run_git(
        ["log", f"{since_sha}..HEAD", "-p", "--", "backend/**/*.py"],
        ra_path,
    )
    out: list[tuple[str, str]] = []
    current_file = ""
    file_pat = re.compile(r"^\+\+\+ b/(.+)$")
    # Lines that ADD a Korean string literal — anywhere inside HTTPException
    # is too restrictive; many are continued on next line. Catch any added
    # line that contains Hangul AND looks like an error message string.
    hangul_pat = re.compile(r'^\+.*"([^"]*[가-힣][^"]*)"')
    for line in diff.splitlines():
        m = file_pat.match(line)
        if m:
            current_file = m.group(1)
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        m = hangul_pat.match(line)
        if m and current_file:
            text = m.group(1)
            # Filter noise: docstrings, comments, very short labels.
            if len(text) < 5:
                continue
            if text.startswith("#"):
                continue
            out.append((current_file, text))
    # Dedup while preserving order.
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for f, t in out:
        if t in seen:
            continue
        seen.add(t)
        deduped.append((f, t))
    return deduped


def check_endpoint_wrapped(method: str, path: str, rs_root: Path) -> bool:
    """Heuristic: any client.py / mcp_server.py reference to the path
    (with /api prefix or without)."""
    haystacks = [
        rs_root / "src" / "report_skill" / "client.py",
        rs_root / "src" / "report_skill" / "mcp_server.py",
    ]
    needle1 = path.lstrip("/")
    needle2 = "/api/" + needle1
    for hs in haystacks:
        if not hs.exists():
            continue
        body = hs.read_text(encoding="utf-8", errors="replace")
        if needle1 in body or needle2 in body:
            return True
    return False


def check_widget_field_wrapped(widget: str, field: str, rs_root: Path) -> bool:
    """Check if the adapter for the widget mentions the field name."""
    adapter = rs_root / "src" / "report_skill" / "adapters" / f"{widget}.py"
    if not adapter.exists():
        return False
    body = adapter.read_text(encoding="utf-8", errors="replace")
    return f'"{field}"' in body or f"'{field}'" in body


def check_korean_string_detected(text: str, rs_root: Path) -> bool:
    """Check if client.py _build_typed_error has a startswith / contains for
    a substring of this Korean text. Heuristic — first 10 chars."""
    client = rs_root / "src" / "report_skill" / "client.py"
    if not client.exists():
        return False
    body = client.read_text(encoding="utf-8", errors="replace")
    # Try multiple substring lengths.
    for L in (15, 10, 7):
        if len(text) < L:
            continue
        if text[:L] in body:
            return True
    return False


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ra-path", default="d:/ReportArchive")
    p.add_argument("--rs-path", default=".")
    p.add_argument("--since-sha", required=True)
    args = p.parse_args()

    ra = Path(args.ra_path).resolve()
    rs = Path(args.rs_path).resolve()

    if not (ra / ".git").exists():
        sys.stderr.write(f"Not a git repo: {ra}\n")
        return 1

    # 1. Endpoints
    endpoints = find_new_endpoints(ra, args.since_sha)
    missing_endpoints = [(f, m, p) for (f, m, p) in endpoints
                         if not check_endpoint_wrapped(m, p, rs)]

    # 2. Widget content fields
    fields = find_new_content_fields(ra, args.since_sha)
    missing_fields = [(w, fld) for (w, fld) in fields
                      if not check_widget_field_wrapped(w, fld, rs)]

    # 3. Korean error strings
    kstrings = find_new_korean_403_strings(ra, args.since_sha)
    missing_kstrings = [(f, t) for (f, t) in kstrings
                        if not check_korean_string_detected(t, rs)]

    # ---- print report ----------------------------------------------------- #
    print()
    print("=" * 78)
    print(f"  RA impact since {args.since_sha}")
    print("=" * 78)

    print()
    print(f"  Endpoints discovered    : {len(endpoints)}")
    print(f"    of which UN-WRAPPED   : {len(missing_endpoints)}")
    if missing_endpoints:
        for f, m, pth in missing_endpoints[:25]:
            print(f"      [MISSING] {m:6s} {pth}   ({Path(f).name})")
        if len(missing_endpoints) > 25:
            print(f"      ... and {len(missing_endpoints) - 25} more")

    print()
    print(f"  Widget content fields   : {len(fields)}")
    print(f"    of which UN-WRAPPED   : {len(missing_fields)}")
    if missing_fields:
        for w, fld in missing_fields[:25]:
            print(f"      [MISSING] {w:20s} . {fld}")
        if len(missing_fields) > 25:
            print(f"      ... and {len(missing_fields) - 25} more")

    print()
    print(f"  New Korean error strings : {len(kstrings)}")
    print(f"    of which UN-DETECTED   : {len(missing_kstrings)}")
    if missing_kstrings:
        for f, t in missing_kstrings[:25]:
            preview = t if len(t) < 60 else t[:57] + "..."
            print(f"      [MISSING] {preview}")
            print(f"                ({Path(f).name})")
        if len(missing_kstrings) > 25:
            print(f"      ... and {len(missing_kstrings) - 25} more")

    print()
    total_missing = len(missing_endpoints) + len(missing_fields) + len(missing_kstrings)
    if total_missing == 0:
        print("  All discovered changes appear to be already covered.")
    else:
        print(f"  TOTAL gaps surfaced: {total_missing}")
        print()
        print("  Plan a release that addresses these. Use the existing audit")
        print("  pattern (per-layer checks): client wrapper -> MCP tool + schema")
        print("  -> CLI mirror -> typed exception -> SKILL.md row -> tests.")
    print()

    return 0 if total_missing == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
