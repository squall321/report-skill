"""`report-skill examples ...` — manage hand-validated widget example files.

Subcommands:

  * `status`           — show ok/stale/missing/orphan counts and per-widget rows
  * `scaffold`         — generate skeleton example files for widgets that have none
  * `check`            — CI-friendly; exit 2 when any stale or missing widget exists
  * `mine-from-report` — overwrite expected_content from a real report's blocks

The typer app is exported as the module-level `app` symbol so the main CLI
can mount it via `app.add_typer(cli_examples.app, name="examples")`.
"""
from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from report_skill import examples, schemas
from report_skill.client import ApiError, ReportArchiveClient

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Hand-validated per-widget examples + staleness tracking.",
)

console = Console()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
_STATUS_STYLE = {
    "ok": "green",
    "stale": "yellow",
    "missing": "red",
    "orphan": "magenta",
}


def _classify(wtype: str, s: examples.ExamplesStatus) -> str:
    if wtype in s.ok:
        return "ok"
    if wtype in s.stale:
        return "stale"
    if wtype in s.missing:
        return "missing"
    if wtype in s.orphan:
        return "orphan"
    return "?"


def _load_snapshot_or_exit() -> dict:
    try:
        return schemas.load()
    except schemas.SnapshotMissing as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #
@app.command("status")
def cmd_status(
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Show one row per widget instead of just the counts.",
    ),
):
    """Print example coverage summary against the cached widget snapshot."""
    _load_snapshot_or_exit()
    s = examples.status()

    summary = Table(title="examples summary", show_lines=False)
    summary.add_column("bucket", style="bold")
    summary.add_column("count", justify="right")
    summary.add_column("widgets", style="dim")
    for bucket in ("ok", "stale", "missing", "orphan"):
        members = getattr(s, bucket)
        style = _STATUS_STYLE[bucket]
        preview = ", ".join(members[:6])
        if len(members) > 6:
            preview += f" (+{len(members) - 6})"
        summary.add_row(f"[{style}]{bucket}[/]", str(len(members)), preview)
    console.print(summary)

    if not verbose:
        return

    all_types = sorted(set(s.ok) | set(s.stale) | set(s.missing) | set(s.orphan))
    detail = Table(title=f"per-widget ({len(all_types)})", show_lines=False)
    detail.add_column("widget", style="cyan")
    detail.add_column("status", style="bold")
    detail.add_column("cache hash", style="dim")
    detail.add_column("example hash", style="dim")
    for wtype in all_types:
        bucket = _classify(wtype, s)
        hashes = s.hashes.get(wtype, {})
        style = _STATUS_STYLE.get(bucket, "white")
        detail.add_row(
            wtype,
            f"[{style}]{bucket}[/]",
            hashes.get("cache", "") or "—",
            hashes.get("example", "") or "—",
        )
    console.print(detail)


# --------------------------------------------------------------------------- #
# scaffold
# --------------------------------------------------------------------------- #
@app.command("scaffold")
def cmd_scaffold(
    overwrite_stale: bool = typer.Option(
        False, "--overwrite-stale",
        help=("Also overwrite stale example files with a fresh skeleton. "
              "Off by default — stale files usually have hand-edits worth "
              "preserving."),
    ),
):
    """Create skeleton example files for every widget that lacks one.

    By default only `missing` widgets get a file written. Pass
    `--overwrite-stale` to also rewrite stale files (destructive!).
    """
    snapshot = _load_snapshot_or_exit()
    widgets: dict[str, dict] = snapshot.get("widgets", {}) or {}
    s = examples.status()

    targets: list[str] = list(s.missing)
    if overwrite_stale:
        targets.extend(s.stale)
    targets = sorted(set(targets))

    if not targets:
        console.print("[dim]nothing to scaffold — all widgets already have examples[/dim]")
        return

    created: list[str] = []
    skipped: list[tuple[str, str]] = []
    for wtype in targets:
        entry = widgets.get(wtype)
        if entry is None:
            skipped.append((wtype, "not in cache"))
            continue
        cache_hash = entry.get("hash", "")
        if not cache_hash:
            skipped.append((wtype, "cache entry has no hash"))
            continue
        body = examples.generate_skeleton(wtype, entry)
        path = examples.write_example(wtype, body, cache_hash)
        created.append(wtype)
        console.print(f"[green]+[/green] wrote {path.relative_to(path.parents[1])}  "
                      f"[dim]hash={cache_hash}[/dim]")

    console.print(f"\n[bold]scaffold:[/bold]  created={len(created)}  skipped={len(skipped)}")
    for wtype, reason in skipped:
        console.print(f"  [yellow]skip[/yellow] {wtype}: {reason}")


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #
@app.command("check")
def cmd_check():
    """Exit non-zero when any widget is stale or missing an example.

    Exit codes:
      0 — every cached widget has an example pinned to the current hash
      1 — no snapshot (run `catalog sync` first)
      2 — at least one stale or missing widget (orphan-only is *not* fatal)
    """
    try:
        schemas.load()
    except schemas.SnapshotMissing as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    s = examples.status()
    console.print(
        f"ok={len(s.ok)}  stale={len(s.stale)}  "
        f"missing={len(s.missing)}  orphan={len(s.orphan)}"
    )

    if s.stale:
        console.print(f"[yellow]stale ({len(s.stale)}):[/yellow] "
                      + ", ".join(s.stale))
    if s.missing:
        console.print(f"[red]missing ({len(s.missing)}):[/red] "
                      + ", ".join(s.missing))
    if s.orphan:
        # Orphans are informational only — don't fail CI.
        console.print(f"[magenta]orphan ({len(s.orphan)}):[/magenta] "
                      + ", ".join(s.orphan))

    if s.has_problems:
        raise typer.Exit(2)
    console.print("[green]all examples up to date[/green]")


# --------------------------------------------------------------------------- #
# mine-from-report
# --------------------------------------------------------------------------- #
@app.command("mine-from-report")
def cmd_mine(
    report_id: int = typer.Argument(..., help="report id to mine block contents from"),
    overwrite_stale: bool = typer.Option(
        True, "--overwrite-stale/--no-overwrite-stale",
        help="replace expected_content even when example already exists with matching hash",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="just list what would change"),
):
    """Pull a live report and update each widget's example with real content.

    For every page's `content` + `extra_blocks` in the report:
      - identify the widget type (from the template block OR the extra_block entry)
      - lift the content dict
      - store as `expected_content` in `examples/<widget_type>.json`, pinning the
        current cache hash. The `input` field is left as-is (a skeleton or the
        prior value) — this command only refreshes the canonical OUTPUT shape.

    Skipped widget types:
      - already-ok examples (unless --overwrite-stale)
      - widgets that aren't in the cached snapshot (would orphan)
    """
    snapshot = _load_snapshot_or_exit()
    widgets: dict[str, dict] = snapshot.get("widgets", {}) or {}

    with ReportArchiveClient() as client:
        try:
            report = client.get(f"/reports/{report_id}")
        except ApiError as e:
            console.print(f"[red]fetch report {report_id} failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(1)
        # Also fetch each page's template to learn widget types per block id.
        templates_cache: dict[tuple[str, int], dict] = {}
        per_page: list[tuple[dict, dict]] = []  # (page, template)
        for page in report.get("pages") or []:
            tid = page["template_id"]
            tver = page["template_version"]
            tpl = templates_cache.get((tid, tver))
            if tpl is None:
                try:
                    tpl = client.fetch_template(tid, tver)
                except ApiError:
                    tpl = {"schema": {"blocks": []}}
                templates_cache[(tid, tver)] = tpl
            per_page.append((page, tpl))

    # Collect (widget_type, content_dict) pairs across all pages.
    candidates: list[tuple[str, dict, str]] = []  # (wtype, content, source)
    for page, tpl in per_page:
        block_types: dict[str, str] = {}
        for b in tpl.get("schema", {}).get("blocks", []) or []:
            block_types[b["id"]] = b["type"]
        for b in page.get("extra_blocks") or []:
            block_types[b["id"]] = b["type"]
        for bid, ctn in (page.get("content") or {}).items():
            wtype = block_types.get(bid)
            if not wtype or not isinstance(ctn, dict):
                continue
            candidates.append((wtype, ctn, f"page#{per_page.index((page, tpl))} block={bid}"))

    if not candidates:
        console.print(f"[yellow]no usable block content found in report {report_id}[/yellow]")
        raise typer.Exit(0)

    s = examples.status()

    updates: list[tuple[str, str, str]] = []  # (wtype, source, action)
    for wtype, content, source in candidates:
        entry = widgets.get(wtype)
        if entry is None:
            updates.append((wtype, source, "skip — widget not in cache"))
            continue
        if wtype in s.ok and not overwrite_stale:
            updates.append((wtype, source, "skip — example already ok"))
            continue
        current = examples.load_example(wtype) or {}
        new_body = {
            "input": current.get("input"),
            "expected_content": content,
            # Always overwrite the notes — the prior text was likely the
            # auto-scaffold's "TODO: hand-review" placeholder. Mining means
            # the content is now a real exemplar from a live report.
            "notes": f"mined from report {report_id} ({source})",
        }
        if dry_run:
            updates.append((wtype, source, "would write"))
            continue
        examples.write_example(wtype, new_body, entry["hash"])
        updates.append((wtype, source, "wrote"))

    tbl = Table(title=f"mine from report {report_id}", show_lines=False)
    tbl.add_column("widget")
    tbl.add_column("source", style="dim")
    tbl.add_column("action")
    for wtype, source, action in updates:
        style = "green" if action == "wrote" else ("yellow" if "would" in action else "dim")
        tbl.add_row(wtype, source, f"[{style}]{action}[/{style}]")
    console.print(tbl)
    if dry_run:
        console.print("[dim]dry-run — no files written[/dim]")


if __name__ == "__main__":
    app()
