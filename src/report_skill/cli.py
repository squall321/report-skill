"""report-skill CLI — `report-skill <command>` after `pip install -e .`."""
from __future__ import annotations

# Force UTF-8 stdout/stderr so Korean text + rich box-drawing renders on
# legacy Windows consoles (cp949 default). Must happen BEFORE rich imports.
import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    _sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, OSError):
    pass  # not all streams support reconfigure (eg piped/redirected)

import json
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from report_skill import catalog as catalog_mod
from report_skill import (
    cli_examples,
    cli_files,
    cli_llm,
    cli_templates,
    orchestrator,
    report_builder,
    report_ops,
    schemas,
    tier,
    upload_chain,
)
from report_skill.client import (
    ApiError,
    AuthorLockedError,
    BoardShareForbiddenError,
    FinalizedReadOnlyError,
    LockHeldByOtherError,
    NoEditPermissionError,
    OutOfWorkspaceScopeError,
    ReportArchiveClient,
    RevisionMismatchError,
    ShareSetupForbiddenError,
    TakedownAlreadyProcessedError,
    TakedownManagerForbiddenError,
    TakedownOwnerForbiddenError,
    TrashRestoreForbiddenError,
)
from report_skill.config import settings


# --------------------------------------------------------------------------- #
# v0.5.2 — F7: typed-exception groups for CLI write-command error handling.
# Catch these BEFORE generic ApiError so the LLM/user sees the actionable
# `reason` (author_locked) or class name (lock_held_by_other, etc) instead
# of an opaque 403/409 envelope dump.
# --------------------------------------------------------------------------- #
_TYPED_LOCK_ERRORS = (
    LockHeldByOtherError,
    RevisionMismatchError,
    FinalizedReadOnlyError,
    NoEditPermissionError,
    OutOfWorkspaceScopeError,
)

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="External skill layer over ReportArchive.")
catalog_app = typer.Typer(no_args_is_help=True, help="Widget catalog cache + diff.")
report_app = typer.Typer(no_args_is_help=True, help="Build / submit reports.")
tier_app = typer.Typer(no_args_is_help=True, help="Adaptive AI tier (S/M/W).")
bridge_app = typer.Typer(no_args_is_help=True, help="LLM bridge (Claude Code as the LLM via files).")
tools_app = typer.Typer(no_args_is_help=True,
                         help="Mention resolvers (reports / workspaces / entity-types / entities). "
                              "Same surface as the MCP tools — use for one-shot CLI lookups.")
mounts_app = typer.Typer(no_args_is_help=True,
                          help="Mount config (folder, edit policy).")
composites_app = typer.Typer(no_args_is_help=True,
                              help="Composite report body editing + submissions.")
notifications_app = typer.Typer(no_args_is_help=True,
                                 help="Notification inbox — react to events.")
shares_app = typer.Typer(no_args_is_help=True,
                          help="Unified grants — share reports / composites / folders / boards.")
app.add_typer(catalog_app, name="catalog")
app.add_typer(report_app, name="report")
app.add_typer(tier_app, name="tier")
app.add_typer(bridge_app, name="bridge")
app.add_typer(tools_app, name="tools")
app.add_typer(mounts_app, name="mounts")
app.add_typer(composites_app, name="composites")
app.add_typer(notifications_app, name="notifications")
app.add_typer(shares_app, name="shares")
app.add_typer(cli_examples.app, name="examples")
app.add_typer(cli_llm.app, name="llm")
app.add_typer(cli_files.app, name="files")
app.add_typer(cli_templates.app, name="templates")

console = Console()


def _version_callback(value: bool) -> None:
    """Print version and exit. Eager so it works before any subcommand parsing."""
    if value:
        from report_skill import __version__
        console.print(f"report-skill {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Optional[bool] = typer.Option(
        None, "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """External skill layer over ReportArchive."""
    # Root callback exists solely to host --version. No-op otherwise.
    return


@app.command()
def ping():
    """Verify the service account can log in and the API is reachable."""
    try:
        with ReportArchiveClient() as client:
            env = client.login()
        console.print(f"[green]OK[/green]  logged in as [bold]{env['email']}[/bold] "
                      f"(user_id={env['user_id']}) — token expires in {env['expires_in']}s")
        console.print(f"      api: {settings.report_api_base_url}  "
                      f"workspace: {settings.report_api_workspace_slug}")
    except ApiError as e:
        console.print(f"[red]API error[/red] ({e.status_code}): {e}")
        raise typer.Exit(2)
    except Exception as e:
        console.print(f"[red]Failed[/red]: {e}")
        raise typer.Exit(1)


@catalog_app.command("sync-templates")
def catalog_sync_templates():
    """Cache every template's full body to .skill-cache/templates/.

    Required for offline `report export --offline` flows. Idempotent —
    overwrites cached templates on each run. Pairs with `catalog sync`
    which caches the widget catalog (also required for offline normalize)."""
    try:
        with ReportArchiveClient() as client:
            cached, errors = catalog_mod.sync_templates(client)
    except ApiError as e:
        console.print(f"[red]sync-templates failed ({e.status_code}):[/red] {e}")
        raise typer.Exit(1)
    console.print(f"[green]templates cached:[/green] {cached}"
                  + (f"   [yellow]errors: {errors}[/yellow]" if errors else ""))
    console.print(f"[dim]cache dir: {catalog_mod.TEMPLATES_CACHE_DIR}[/dim]")


@catalog_app.command("sync")
def catalog_sync():
    """Fetch the live catalog, extract content_schemas, persist, show diff."""
    try:
        snapshot, diff = catalog_mod.sync()
    except Exception as e:
        console.print(f"[red]sync failed[/red]: {e}")
        raise typer.Exit(1)

    console.print(f"[green]snapshot saved[/green]  "
                  f"{len(snapshot.widgets)} widgets, "
                  f"schema_version={snapshot.schema_version}")
    _print_diff(diff)


@catalog_app.command("diff")
def catalog_diff():
    """Show diff between the cached snapshot and the last one (no fetch)."""
    current = catalog_mod.load_current()
    if current is None:
        console.print("[yellow]no snapshot yet — run `catalog sync` first[/yellow]")
        raise typer.Exit(1)
    previous = catalog_mod._load_snapshot(catalog_mod.CACHE_FILE_PREVIOUS)  # noqa
    diff = catalog_mod.diff_snapshots(previous, current)
    _print_diff(diff)


@app.command(name="import")
def import_payload(
    payload_path: Path = typer.Argument(..., help="path to a saved ReportCreate JSON payload"),
):
    """Import a previously-exported report payload (POST to /api/reports).

    Pair with `report export` — use export when offline / no API access,
    then import once you're back online. Idempotent only at the application
    level: re-running creates a new report each time.
    """
    if not payload_path.is_file():
        console.print(f"[red]payload not found:[/red] {payload_path}")
        raise typer.Exit(1)
    try:
        raw_text = payload_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        console.print(f"[red]payload file disappeared while reading:[/red] {payload_path}")
        raise typer.Exit(1)
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as e:
        typer.echo(
            f"Invalid JSON in {payload_path}: line {e.lineno}, "
            f"column {e.colno}: {e.msg}",
            err=True,
        )
        raise typer.Exit(1)
    with ReportArchiveClient() as client:
        try:
            created = client.create_report(payload)
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]POST /reports failed ({e.status_code}):[/red] {e}")
            console.print_json(json.dumps(e.payload, ensure_ascii=False))
            raise typer.Exit(3)
    rid = created.get("id")
    console.print(f"[green]imported[/green]  report id={rid}  "
                  f"title={created.get('title')!r}")
    console.print(f"  view: http://localhost:3001/reports/{rid}")


@catalog_app.command("list")
def catalog_list():
    """List all widgets currently cached, with their hash."""
    current = catalog_mod.load_current()
    if current is None:
        console.print("[yellow]no snapshot — run `catalog sync`[/yellow]")
        raise typer.Exit(1)
    tbl = Table(title=f"widgets ({len(current['widgets'])})", show_lines=False)
    tbl.add_column("type", style="cyan")
    tbl.add_column("label")
    tbl.add_column("hash", style="dim")
    tbl.add_column("content?", justify="center")
    tbl.add_column("err", style="red")
    for wtype, w in sorted(current["widgets"].items()):
        tbl.add_row(
            wtype,
            (w.get("label") or "")[:30],
            w.get("hash", ""),
            "✓" if w.get("has_content") else "·",
            "ERR" if w.get("content_schema_error") else "",
        )
    console.print(tbl)


def _print_diff(diff: catalog_mod.CatalogDiff) -> None:
    if not diff.has_changes:
        console.print("[dim]no changes vs previous snapshot[/dim]")
        return
    if diff.added:
        console.print(f"[green]+ added[/green]  ({len(diff.added)})")
        for t in diff.added:
            console.print(f"    {t}")
    if diff.removed:
        console.print(f"[red]- removed[/red]  ({len(diff.removed)})")
        for t in diff.removed:
            console.print(f"    {t}")
    if diff.modified:
        console.print(f"[yellow]~ modified[/yellow]  ({len(diff.modified)})")
        for t, oh, nh in diff.modified:
            console.print(f"    {t}  {oh} -> {nh}")
    if not diff.added and not diff.removed and not diff.modified:
        console.print("[dim]no changes[/dim]")


# --------------------------------------------------------------------------- #
# report commands
# --------------------------------------------------------------------------- #
def _load_draft(path: Path) -> dict:
    if not path.is_file():
        console.print(f"[red]draft file not found:[/red] {path}")
        raise typer.Exit(1)
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        console.print(f"[red]draft file disappeared while reading:[/red] {path}")
        raise typer.Exit(1)
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as e:
        typer.echo(
            f"Invalid JSON in {path}: line {e.lineno}, "
            f"column {e.colno}: {e.msg}",
            err=True,
        )
        raise typer.Exit(1)


def _print_block_table(result: orchestrator.NormalizeResult) -> None:
    tbl = Table(title=f"blocks  ok={result.ok_count}  failed={result.failed_count}  skipped={result.skipped_count}")
    tbl.add_column("status", style="bold")
    tbl.add_column("id", style="cyan")
    tbl.add_column("widget")
    tbl.add_column("detail", style="dim")

    color = {"ok": "green", "repaired": "yellow", "skipped": "dim",
             "failed": "red", "unsupported": "magenta"}
    for b in result.blocks:
        tbl.add_row(
            f"[{color.get(b.status, 'white')}]{b.status}[/]",
            b.block_id,
            b.widget_type,
            (b.detail or "")[:80],
        )
    console.print(tbl)


@report_app.command("draft")
def report_draft(
    template: Optional[str] = typer.Option(
        None, "--template", "-t",
        help="template_id (required for single-page drafts; ignored if 'pages' present)",
    ),
    input_path: Path = typer.Option(..., "--input", "-i", help="path to draft JSON"),
    show_payload: bool = typer.Option(False, "--show-payload", help="print the full POST payload"),
):
    """Normalize + validate a draft against template(s). No POST. Single- or multi-page."""
    draft = _load_draft(input_path)
    try:
        snapshot = schemas.load()
    except schemas.SnapshotMissing as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    pages_spec = _coerce_pages(draft, template)
    if pages_spec is None:
        console.print("[red]draft must contain either 'blocks' (with --template) or 'pages'[/red]")
        raise typer.Exit(1)

    with ReportArchiveClient() as client:
        normalized_pages = []
        for i, page in enumerate(pages_spec):
            try:
                tpl = client.fetch_template(page["template_id"])
            except ApiError as e:
                console.print(f"[red]template fetch failed ({e.status_code}):[/red] {e}")
                raise typer.Exit(1)
            result = orchestrator.normalize_report(
                tpl, page.get("blocks", {}), snapshot,
                extra_blocks_input=page.get("extra_blocks"),
            )
            console.print(f"\n[bold]page {i+1}[/bold] ({page['template_id']}"
                          f"{' — ' + page['name'] if page.get('name') else ''})")
            _print_block_table(result)
            normalized_pages.append({
                "template": tpl,
                "content": result.content,
                "name": page.get("name"),
                "extra_blocks": result.extra_blocks,
                "blocks_order": page.get("blocks_order"),
            })

    if show_payload:
        payload = report_builder.build_create_payload_multi(
            normalized_pages,
            title=draft.get("title", "(untitled)"),
            report_date=draft.get("report_date"),
            tags=draft.get("tags", []),
        )
        console.print_json(json.dumps(payload, ensure_ascii=False))


@report_app.command("create")
def report_create(
    template: Optional[str] = typer.Option(
        None, "--template", "-t",
        help="template_id (required for single-page drafts; ignored if draft has 'pages')",
    ),
    input_path: Path = typer.Option(..., "--input", "-i", help="path to draft JSON"),
    title: Optional[str] = typer.Option(None, "--title", help="override draft title"),
    allow_failures: bool = typer.Option(
        False, "--allow-failures",
        help="POST even if some blocks failed (those blocks will be empty in the report)",
    ),
    partial: bool = typer.Option(
        False, "--partial",
        help="skip pages whose template fetch fails or yields zero ok blocks (multi-page only)",
    ),
    mount_to: list[str] = typer.Option(
        None, "--mount-to",
        help="after create, mount the new report onto this workspace board "
             "(repeat for multiple). Without this, reports stay in your personal workspace only.",
    ),
):
    """Normalize, validate, then POST /reports. Single- OR multi-page drafts."""
    draft = _load_draft(input_path)
    try:
        snapshot = schemas.load()
    except schemas.SnapshotMissing as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    pages_spec = _coerce_pages(draft, template)
    if pages_spec is None:
        console.print("[red]draft must contain either 'blocks' (with --template) or 'pages'[/red]")
        raise typer.Exit(1)

    with ReportArchiveClient() as client:
        normalized_pages = []
        total_failed = 0
        skipped_pages: list[tuple[int, str]] = []
        for i, page in enumerate(pages_spec):
            try:
                tpl = client.fetch_template(page["template_id"])
            except ApiError as e:
                if partial:
                    console.print(f"[yellow]page {i+1} skipped:[/yellow] "
                                  f"template '{page['template_id']}' fetch failed ({e.status_code})")
                    skipped_pages.append((i, f"template fetch: {e}"))
                    continue
                console.print(f"[red]template fetch failed ({e.status_code}):[/red] {e}")
                raise typer.Exit(1)
            # Auto-upload local media paths before normalization.
            block_types = {b["id"]: b["type"]
                           for b in (tpl.get("schema") or {}).get("blocks") or []
                           if isinstance(b, dict)}
            blocks_input = page.get("blocks", {})
            extras_input = page.get("extra_blocks", [])
            blocks_input, extras_input, uploaded = upload_chain.preupload_for_draft(
                client=client,
                blocks_input=blocks_input,
                block_types=block_types,
                extras_input=extras_input,
                log=lambda m: console.print(f"  [magenta]upload[/] {m}"),
            )
            result = orchestrator.normalize_report(
                tpl, blocks_input, snapshot,
                extra_blocks_input=extras_input,
            )
            console.print(f"\n[bold]page {i+1}[/bold] ({page['template_id']}"
                          f"{' — ' + page['name'] if page.get('name') else ''})")
            _print_block_table(result)
            total_failed += result.failed_count
            if partial and result.ok_count == 0:
                console.print(f"  [yellow]page {i+1} skipped:[/yellow] zero ok blocks")
                skipped_pages.append((i, "no ok blocks"))
                continue
            normalized_pages.append({
                "template": tpl,
                "content": result.content,
                "name": page.get("name"),
                "extra_blocks": result.extra_blocks,
                "blocks_order": page.get("blocks_order"),
            })

        if not normalized_pages:
            console.print(f"\n[red]no pages survived normalization[/red] "
                          f"(skipped: {len(skipped_pages)})")
            raise typer.Exit(2)
        if total_failed > 0 and not allow_failures and not partial:
            console.print(f"\n[red]{total_failed} block(s) failed across pages.[/red] "
                          "Pass --allow-failures (keep partial blocks empty) or "
                          "--partial (skip dead pages) to POST anyway.")
            raise typer.Exit(2)
        if skipped_pages:
            console.print(f"\n[dim]skipped pages: {len(skipped_pages)}[/dim]")

        payload = report_builder.build_create_payload_multi(
            normalized_pages,
            title=title or draft.get("title", "(untitled)"),
            report_date=draft.get("report_date"),
            tags=draft.get("tags", []),
        )
        try:
            created = client.create_report(payload)
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]POST /reports failed ({e.status_code}):[/red] {e}")
            console.print_json(json.dumps(e.payload, ensure_ascii=False))
            raise typer.Exit(3)

    rid = created.get("id") if isinstance(created, dict) else None
    console.print(f"\n[green]created[/green]  report id={rid}  title={created.get('title')!r}  "
                  f"pages={len(normalized_pages)}")
    console.print(f"  view: http://localhost:3001/reports/{rid}")

    # Optional auto-mount to one or more org board workspaces.
    if mount_to and rid is not None:
        with ReportArchiveClient() as mount_client:
            try:
                created_mounts = report_ops.mount_report(
                    mount_client, int(rid),
                    workspace_slugs=list(mount_to),
                )
            except AuthorLockedError as e:
                console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                              f"report_id={e.report_id}")
                raise typer.Exit(4)
            except _TYPED_LOCK_ERRORS as e:
                console.print(f"[red][{type(e).__name__}][/red] {e}")
                raise typer.Exit(4)
            except ApiError as e:
                console.print(f"[red]auto-mount failed ({e.status_code}):[/red] {e}")
                raise typer.Exit(4)
        if created_mounts:
            for m in created_mounts:
                console.print(f"  [green]+ mount[/green] workspace={m.get('workspace_slug')}  "
                              f"edit_policy={m.get('edit_policy')}")
        else:
            console.print("  [yellow]mount skipped[/yellow] (already mounted on requested boards)")


def _coerce_pages(draft: dict, default_template: Optional[str]) -> Optional[list[dict]]:
    """Resolve draft into a uniform list of page specs:
        [{template_id, name?, blocks: {...}, extra_blocks?: [...]}, ...]
    """
    if isinstance(draft.get("pages"), list) and draft["pages"]:
        out = []
        for p in draft["pages"]:
            tid = p.get("template_id") or default_template
            if not tid:
                return None
            out.append({
                "template_id": tid,
                "name": p.get("name"),
                "blocks": p.get("blocks", {}),
                "extra_blocks": p.get("extra_blocks", []),
                # User-supplied explicit render order — passes through
                # build_create_payload_multi unchanged if present.
                "blocks_order": p.get("blocks_order"),
            })
        return out
    if isinstance(draft.get("blocks"), dict):
        if not default_template:
            return None
        return [{
            "template_id": default_template,
            "name": None,
            "blocks": draft["blocks"],
            "extra_blocks": draft.get("extra_blocks", []),
            "blocks_order": draft.get("blocks_order"),
        }]
    return None


def _auto_title_from_prompt(text: str, max_len: int = 80) -> str:
    """Derive a default report title from the first meaningful line of input."""
    for line in text.splitlines():
        s = line.strip().lstrip("#-*• ").strip()
        if s:
            return s[:max_len]
    return "(untitled)"


@report_app.command("adhoc")
def report_adhoc(
    prompt_text: str = typer.Argument(..., help="user's raw text — skill picks template + extras + posts"),
    title: Optional[str] = typer.Option(None, "--title"),
    dry_run: bool = typer.Option(False, "--dry-run", help="skip the POST (default: POST)"),
):
    """One-shot: auto-pick template, auto-add visual extras, validate, POST.

    Equivalent to `report from-prompt <text> --auto --with-extras --create`
    (with --create flipped to dry-run if --dry-run is passed). Use when the
    user just wants the report to exist and trusts the skill to organize it.
    """
    return report_from_prompt(
        prompt_text=prompt_text,
        template=None,
        auto_template=True,
        with_extras=True,
        title=title,
        create=not dry_run,
        max_tokens=800,
    )


@report_app.command("export")
def report_export(
    template: Optional[str] = typer.Option(
        None, "--template", "-t",
        help="template_id (required for single-page drafts; ignored if draft has 'pages')",
    ),
    input_path: Path = typer.Option(..., "--input", "-i", help="path to draft JSON"),
    out_path: Path = typer.Option(..., "--out", "-o", help="path to write the POST payload"),
    title: Optional[str] = typer.Option(None, "--title"),
    offline: bool = typer.Option(
        False, "--offline",
        help="use cached templates (.skill-cache/templates/) instead of API. "
             "Run `templates sync` once while online to populate the cache.",
    ),
    allow_failures: bool = typer.Option(False, "--allow-failures"),
):
    """Normalize a draft into a ReportCreate payload JSON file (no POST).

    Use when the ReportArchive server is unavailable (offline) — the
    payload can later be POSTed via `report-skill import <payload.json>`.
    Requires `.skill-cache/widgets.json` (run `catalog sync` once online)
    and, with `--offline`, cached templates (run `templates sync` once).
    """
    draft = _load_draft(input_path)
    try:
        snapshot = schemas.load()
    except schemas.SnapshotMissing as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    pages_spec = _coerce_pages(draft, template)
    if pages_spec is None:
        console.print("[red]draft must contain either 'blocks' (with --template) or 'pages'[/red]")
        raise typer.Exit(1)

    with ReportArchiveClient() as client:
        normalized_pages = []
        total_failed = 0
        for i, page in enumerate(pages_spec):
            try:
                tpl = client.fetch_template(page["template_id"], allow_cache=offline)
            except (ApiError, FileNotFoundError) as e:
                console.print(f"[red]template fetch failed:[/red] {e}")
                raise typer.Exit(1)
            block_types = {b["id"]: b["type"]
                           for b in (tpl.get("schema") or {}).get("blocks") or []
                           if isinstance(b, dict)}
            blocks_input = page.get("blocks", {})
            extras_input = page.get("extra_blocks", [])
            # Offline mode skips upload_chain — local files can't be uploaded
            # without API. Caller should embed file_ids manually if needed.
            if not offline:
                blocks_input, extras_input, _ = upload_chain.preupload_for_draft(
                    client=client,
                    blocks_input=blocks_input,
                    block_types=block_types,
                    extras_input=extras_input,
                    log=lambda m: console.print(f"  [magenta]upload[/] {m}"),
                )
            result = orchestrator.normalize_report(
                tpl, blocks_input, snapshot, extra_blocks_input=extras_input,
            )
            console.print(f"\n[bold]page {i+1}[/bold] ({page['template_id']})")
            _print_block_table(result)
            total_failed += result.failed_count
            normalized_pages.append({
                "template": tpl, "content": result.content,
                "name": page.get("name"), "extra_blocks": result.extra_blocks,
                "blocks_order": page.get("blocks_order"),
            })

    if total_failed > 0 and not allow_failures:
        console.print(f"\n[red]{total_failed} block(s) failed.[/red] "
                      "pass --allow-failures to export anyway.")
        raise typer.Exit(2)

    payload = report_builder.build_create_payload_multi(
        normalized_pages,
        title=title or draft.get("title", "(untitled)"),
        report_date=draft.get("report_date"),
        tags=draft.get("tags", []),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    console.print(f"\n[green]exported[/green]  payload → {out_path}  "
                  f"({len(normalized_pages)} page(s))")
    console.print(f"[dim]later, run: report-skill import {out_path}[/dim]")


@tools_app.command("reports-search")
def tools_reports_search(
    q: Optional[str] = typer.Option(None, "--q", "-q",
                                     help="search keyword (title/owner/mount, NFKC + case-insensitive)"),
    workspace_slug: Optional[str] = typer.Option(None, "--workspace-slug", "--workspace",
                                                  help="restrict to reports whose home workspace exactly matches"),
    owner_name: Optional[str] = typer.Option(None, "--owner",
                                              help="restrict to owner_name substring"),
    mount_slug: Optional[str] = typer.Option(None, "--mount-slug",
                                              help="restrict to reports mounted on this board"),
    date_from: Optional[str] = typer.Option(None, "--from", help="ISO date — report_date >="),
    date_to: Optional[str] = typer.Option(None, "--to", help="ISO date — report_date <="),
    limit: int = typer.Option(20, "--limit", min=1, max=50),
):
    """Resolve a free-text reference to report id candidates for
    mention://report/<id>?ws=<slug>. Same logic as the MCP reports_search tool."""
    from report_skill.mcp_server import _do_reports_search
    rows = _do_reports_search({
        "q": q, "workspace_slug": workspace_slug, "owner_name": owner_name,
        "mount_slug": mount_slug, "date_from": date_from, "date_to": date_to,
        "limit": limit,
    })
    console.print_json(json.dumps(rows, ensure_ascii=False))


@tools_app.command("workspaces-list")
def tools_workspaces_list(
    q: Optional[str] = typer.Option(None, "--q", "-q",
                                     help="substring over name+slug"),
    kind: str = typer.Option("org", "--kind",
                              help="all | org | personal | virtual (default org)"),
):
    """List workspace candidates for mention://dept/<slug>."""
    from report_skill.mcp_server import _do_workspaces_list
    rows = _do_workspaces_list({"q": q, "kind": kind})
    console.print_json(json.dumps(rows, ensure_ascii=False))


@tools_app.command("entity-types-list")
def tools_entity_types_list():
    """List entity-axis catalog (model_name, customer_name, …)."""
    from report_skill.mcp_server import _do_entity_types_list
    rows = _do_entity_types_list({})
    console.print_json(json.dumps(rows, ensure_ascii=False))


@tools_app.command("entities-list")
def tools_entities_list(
    q: Optional[str] = typer.Option(None, "--q", "-q",
                                     help="substring over value/code/description"),
    axis: Optional[str] = typer.Option(None, "--axis",
                                        help="entity-type slug (e.g. model_name)"),
    type_id: Optional[int] = typer.Option(None, "--type-id",
                                           help="entity_type id (overrides --axis when both given)"),
    include_deprecated: bool = typer.Option(False, "--include-deprecated"),
    limit: int = typer.Option(50, "--limit", min=1, max=200),
):
    """Resolve free-text reference to entity id candidates for
    mention://entity/<id>?axis=<slug>."""
    from report_skill.mcp_server import _do_entities_list
    rows = _do_entities_list({
        "q": q, "axis": axis, "type_id": type_id,
        "include_deprecated": include_deprecated, "limit": limit,
    })
    console.print_json(json.dumps(rows, ensure_ascii=False))


@report_app.command("dump")
def report_dump(
    report_id: int = typer.Argument(..., help="report id to dump"),
    out_path: Optional[Path] = typer.Option(
        None, "--out", "-o",
        help="path to write the bundle.zip (default: bundle-report-<id>.zip in cwd)",
    ),
):
    """Pack a report + every referenced media file into a portable bundle.zip.

    Use this to move a report between ReportArchive instances: fetch the
    report on server A, run `report dump <id> -o bundle.zip`, then on
    server B run `report import bundle.zip` — files are re-uploaded
    (getting fresh file_ids) and every reference inside the payload is
    swapped automatically.

    The bundle is self-contained: payload.json + files/<file_id> bytes +
    files/manifest.json. No coordinated backend change required between
    instances.
    """
    from report_skill import bundle as bundle_mod

    target = out_path or Path(f"bundle-report-{report_id}.zip")
    with ReportArchiveClient() as client:
        try:
            summary = bundle_mod.pack_report_bundle(
                client, report_id, target,
                log=lambda m: console.print(f"  [magenta]bundle[/] {m}"),
            )
        except ApiError as e:
            console.print(f"[red]GET /reports/{report_id} failed:[/red] {e}")
            raise typer.Exit(1)
    console.print(f"\n[green]bundled[/green]  report id={summary['report_id']}  "
                  f"title={summary.get('title')!r}  pages={summary['pages']}  "
                  f"files={summary['files']}  "
                  f"size={summary['bundle_size']} B")
    if summary["missing_files"]:
        console.print(f"[yellow]  missing on source server: {summary['missing_files']}[/yellow]")
    console.print(f"  -> [cyan]report-skill report import {target}[/cyan]")


@report_app.command("import")
def report_import(
    payload_path: Path = typer.Argument(..., help="path to a saved payload (.json) or bundle (.zip)"),
):
    """Import a previously-exported report payload OR a bundle.zip.

    Auto-detects from the file extension:
      .zip  → unpack bundle, re-upload files, swap file_ids, POST
      .json → POST the payload verbatim (legacy path; assumes file_ids
               are valid on this server)
    """
    if payload_path.suffix.lower() == ".zip":
        from report_skill import bundle as bundle_mod
        with ReportArchiveClient() as client:
            try:
                created = bundle_mod.import_bundle(
                    client, payload_path,
                    log=lambda m: console.print(f"  [magenta]bundle[/] {m}"),
                )
            except ApiError as e:
                console.print(f"[red]bundle import failed ({e.status_code}):[/red] {e}")
                raise typer.Exit(1)
            except (FileNotFoundError, json.JSONDecodeError) as e:
                console.print(f"[red]bundle malformed:[/red] {e}")
                raise typer.Exit(1)
        console.print(f"[green]imported[/green]  new id={created.get('id')}  "
                      f"title={created.get('title')!r}")
        console.print(f"  view: http://localhost:3001/reports/{created.get('id')}")
        return
    return import_payload(payload_path)


# --------------------------------------------------------------------------- #
# report from-prompt — full LLM-driven generation (skill's "solution" path B)
# --------------------------------------------------------------------------- #
@report_app.command("from-prompt")
def report_from_prompt(
    prompt_text: str = typer.Argument(..., help="raw natural-language input describing what to report"),
    template: Optional[str] = typer.Option(None, "--template", "-t", help="template_id"),
    auto_template: bool = typer.Option(
        False, "--auto",
        help="auto-pick template via `templates suggest` when --template is omitted",
    ),
    with_extras: bool = typer.Option(
        False, "--with-extras",
        help="scan input for chartable/date/hierarchy patterns and auto-add extra_blocks",
    ),
    title: Optional[str] = typer.Option(None, "--title"),
    create: bool = typer.Option(False, "--create", help="POST the report (default: dry-run only)"),
    max_tokens: int = typer.Option(800, "--max-tokens"),
):
    """End-to-end: LLM fills every block of the template from raw text, normalize, optionally POST.

    This is the "internal LLM" path (provider auto-detected from env:
    ANTHROPIC_API_KEY → OPENAI_API_KEY → OLLAMA_BASE_URL). Each block gets
    its own schema-constrained prompt — robust enough for local 8B models
    when tier W is active.
    """
    from report_skill import examples as examples_mod
    from report_skill import llm as llm_mod
    from report_skill import prompt as prompt_mod
    from report_skill import template_suggest, widget_suggest
    from report_skill.llm import LLMError

    if not llm_mod.is_configured():
        console.print(
            "[red]no LLM provider configured.[/red]\n"
            "  set ANTHROPIC_API_KEY  (claude-haiku-4-5 default)\n"
            "  or OPENAI_API_KEY      (gpt-4o-mini default)\n"
            "  or run ollama locally  (llama3.1:8b default, OLLAMA_BASE_URL=http://localhost:11434)"
        )
        raise typer.Exit(1)

    try:
        snapshot = schemas.load()
    except schemas.SnapshotMissing as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    # --auto: pick a template via the recommender when -t is omitted.
    if template is None:
        if not auto_template:
            console.print("[red]--template required (or pass --auto to auto-pick)[/red]")
            raise typer.Exit(1)
        suggestions = template_suggest.suggest_templates(prompt_text, top_k=3, use_llm="auto")
        top = next((s for s in suggestions if s.template_id), None)
        if top is None or top.confidence == "low":
            console.print("[red]auto template selection: no confident match[/red]")
            for s in suggestions[:3]:
                if s.template_id:
                    console.print(f"  candidate: {s.template_id} ({s.confidence}, score={s.score:.2f})")
            console.print("  → rerun with explicit --template <id>")
            raise typer.Exit(1)
        template = top.template_id
        console.print(f"[dim]auto-selected template: [cyan]{template}[/]  "
                      f"({top.confidence}, score={top.score:.2f})[/dim]")

    provider = llm_mod.get_provider()
    pol = tier.policy()
    console.print(f"[dim]provider={provider.name}  tier={tier.current_tier().tier}  "
                  f"blocks_per_call={pol.blocks_per_call}  max_retries={pol.max_llm_retries}[/dim]")

    with ReportArchiveClient() as client:
        try:
            tpl = client.fetch_template(template)
        except ApiError as e:
            console.print(f"[red]template fetch failed:[/red] {e}")
            raise typer.Exit(1)

        blocks_schema = tpl.get("schema", {}).get("blocks", [])
        draft_blocks: dict[str, Any] = {}

        for block in blocks_schema:
            bid, wtype = block["id"], block["type"]
            schema = schemas.content_schema(snapshot, wtype)
            props = schemas.resolved_props(block, snapshot)
            example = examples_mod.load_example(wtype)

            spec = prompt_mod.BlockSpec(
                block_id=bid, widget_type=wtype, props=props, content_schema=schema,
            )

            messages = prompt_mod.build_single_block_prompt(
                spec, user_input=prompt_text,
                example=example.get("expected_content") if example else None,
            )
            try:
                reply = provider.generate(messages, max_tokens=max_tokens, json_mode=True)
                parsed = llm_mod.extract_json(reply)
            except LLMError as e:
                console.print(f"  [red]{bid}: LLM error[/red] {e}")
                continue
            except (ValueError, KeyError) as e:
                console.print(f"  [yellow]{bid}: parse failed[/yellow] {e}")
                continue
            if parsed is None:
                console.print(f"  [yellow]{bid}: no JSON in reply[/yellow]")
                continue
            draft_blocks[bid] = parsed
            console.print(f"  [green]✓[/green] {bid} ({wtype})")

        # --with-extras: scan the raw input for visual-widget candidates and
        # add them to the orchestrator's extra_blocks pipeline.
        extras_input: list[dict] = []
        if with_extras:
            extras = widget_suggest.suggest_extras(
                prompt_text,
                template_blocks=blocks_schema,
                use_llm="auto",
                max_extras=5,
            )
            for ex in extras:
                extras_input.append({
                    "id": ex.suggested_id,
                    "type": ex.widget_type,
                    "props": ex.props,
                    "input": ex.input,
                })
                console.print(f"  [magenta]+ extra[/] {ex.widget_type} "
                              f"({ex.confidence}, {ex.matched_pattern})")

        result = orchestrator.normalize_report(
            tpl, draft_blocks, snapshot,
            extra_blocks_input=extras_input,
        )
        _print_block_table(result)

        if not create:
            console.print("\n[dim]dry-run: pass --create to POST[/dim]")
            return

        if result.failed_count > 0:
            console.print(f"[red]{result.failed_count} block(s) failed.[/red] "
                          "rerun with corrected input or use --allow-failures via `report create`.")
            raise typer.Exit(2)

        from report_skill import tags as tags_mod
        derived_title = title or _auto_title_from_prompt(prompt_text)
        derived_tags = tags_mod.infer_tags(
            title=derived_title,
            body_text=prompt_text,
            existing_tags=None,
            max_tags=5,
        )
        if derived_tags:
            console.print(f"[dim]auto-tags: {', '.join(derived_tags)}[/dim]")
        payload = report_builder.build_create_payload(
            tpl, result.content,
            title=derived_title,
            tags=derived_tags,
            extra_blocks=result.extra_blocks,
        )
        try:
            created = client.create_report(payload)
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]POST failed:[/red] {e}")
            raise typer.Exit(3)

    rid = created.get("id")
    console.print(f"[green]created[/green]  id={rid}  title={created.get('title')!r}")
    console.print(f"  view: http://localhost:3001/reports/{rid}")


# --------------------------------------------------------------------------- #
# report edit commands (PATCH /reports/{id})
# --------------------------------------------------------------------------- #
@report_app.command("update")
def report_update(
    report_id: int = typer.Argument(..., help="report id to patch"),
    input_path: Path = typer.Option(..., "--input", "-i",
                                    help="draft JSON; only 'blocks' / 'extra_blocks' / 'title' / 'phase' / 'lifecycle' / 'tags' read"),
    page_index: int = typer.Option(0, "--page", help="which page to update (0-based)"),
    title: Optional[str] = typer.Option(None, "--title"),
    phase: Optional[str] = typer.Option(None, "--phase",
                                       help="drafting | reviewing | finalized"),
    lifecycle: Optional[str] = typer.Option(None, "--lifecycle",
                                           help="single_shot | ongoing"),
    status: Optional[str] = typer.Option(None, "--status",
                                        help="LEGACY alias for --phase (draft→drafting, in_progress→reviewing, completed→finalized)"),
):
    """Patch specific blocks of an existing report. Other blocks preserved."""
    draft = _load_draft(input_path)
    try:
        snapshot = schemas.load()
    except schemas.SnapshotMissing as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    with ReportArchiveClient() as client:
        try:
            existing = report_ops.fetch_report(client, report_id)
        except ApiError as e:
            console.print(f"[red]fetch report {report_id} failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(1)

        pages = existing.get("pages") or []
        if page_index >= len(pages):
            console.print(f"[red]report {report_id} only has {len(pages)} page(s)[/red]")
            raise typer.Exit(1)

        page_template_id = pages[page_index]["template_id"]
        try:
            tpl = client.fetch_template(page_template_id, pages[page_index]["template_version"])
        except ApiError as e:
            console.print(f"[red]template fetch failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(1)

        # Synthesize template blocks for any extra-block id present in the
        # draft's `blocks` dict — so `report update` can patch existing
        # extras (eg. an `mx_takeaway` rich_text extra) without callers
        # needing to know it's an extra vs a template block.
        existing_extras = pages[page_index].get("extra_blocks") or []
        extras_by_id = {b.get("id"): b for b in existing_extras if isinstance(b, dict)}
        tpl_blocks = (tpl.get("schema") or {}).get("blocks") or []
        tpl_block_ids = {b.get("id") for b in tpl_blocks if isinstance(b, dict)}
        draft_blocks = draft.get("blocks", {}) or {}
        synthetic_extras: list[dict] = list(draft.get("extra_blocks", []) or [])

        # CR-1 fix — capture the block ids the USER actually provided in
        # this patch BEFORE any mutation (upload_chain rewrites the lists).
        # `result.content` from normalize_report covers every template block
        # — including blocks the user omitted from the patch — and used to
        # silently wipe their existing content. We now restrict the patch
        # to only the ids the user explicitly named.
        provided_block_ids = set(draft_blocks.keys()) | {
            e.get("id") for e in synthetic_extras
            if isinstance(e, dict) and e.get("id")
        }
        for bid, raw in list(draft_blocks.items()):
            if bid in tpl_block_ids:
                continue
            if bid in extras_by_id:
                # Re-issue as an extra_blocks_input entry so orchestrator
                # normalizes it through the right adapter (and ends up in
                # the page's content dict via the extra path).
                eb = extras_by_id[bid]
                synthetic_extras.append({
                    "id": bid,
                    "type": eb.get("type"),
                    "props": eb.get("props", {}),
                    "input": raw,
                })
                draft_blocks.pop(bid)

        # Auto-upload local media paths before normalization (same as report_create).
        type_map_for_upload: dict[str, str] = {b["id"]: b["type"]
                                               for b in tpl_blocks if isinstance(b, dict)}
        for eb in existing_extras:
            if isinstance(eb, dict) and eb.get("id") and eb.get("type"):
                type_map_for_upload[eb["id"]] = eb["type"]
        draft_blocks, synthetic_extras, _uploaded = upload_chain.preupload_for_draft(
            client=client,
            blocks_input=draft_blocks,
            block_types=type_map_for_upload,
            extras_input=synthetic_extras,
            log=lambda m: console.print(f"  [magenta]upload[/] {m}"),
        )

        result = orchestrator.normalize_report(
            tpl, draft_blocks, snapshot,
            extra_blocks_input=synthetic_extras,
        )
        console.print(f"[bold]patching page {page_index} of report {report_id}[/bold]")
        _print_block_table(result)

        if result.failed_count > 0:
            console.print(f"[red]{result.failed_count} block(s) failed normalization[/red]")
            raise typer.Exit(2)

        # Don't re-add extra_blocks that already exist on the page — that
        # raises "Duplicate extra block id". Content patch handles the
        # in-place update; only TRULY-new extras need add_extra_blocks.
        existing_extra_ids = {b.get("id") for b in (pages[page_index].get("extra_blocks") or [])
                              if isinstance(b, dict) and b.get("id")}
        new_extras = [e for e in result.extra_blocks
                      if e.get("id") not in existing_extra_ids]
        # CR-1 fix — patch only blocks the user provided in this draft;
        # omit blocks they didn't mention so their existing content
        # survives. Without this filter, every template block gets
        # overwritten with the orchestrator's empty/skipped content.
        scoped_content = {bid: ctn for bid, ctn in result.content.items()
                          if bid in provided_block_ids}
        try:
            updated = report_ops.update_blocks(
                client, report_id,
                page_index=page_index,
                block_patches=scoped_content,
                add_extra_blocks=new_extras,
                # CR-11 — propagate explicit per-page blocks_order if the
                # draft set one; otherwise let update_blocks auto-merge any
                # new extra ids into the page's existing order.
                blocks_order=draft.get("blocks_order"),
                title=title or draft.get("title"),
                phase=phase or draft.get("phase"),
                lifecycle=lifecycle or draft.get("lifecycle"),
                status=status or draft.get("status"),
                tags=draft.get("tags"),
            )
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]PATCH /reports/{report_id} failed ({e.status_code}):[/red] {e}")
            console.print_json(json.dumps(e.payload, ensure_ascii=False))
            raise typer.Exit(3)

    console.print(f"[green]updated[/green]  report id={updated.get('id')}  "
                  f"title={updated.get('title')!r}")
    console.print(f"  view: http://localhost:3001/reports/{updated.get('id')}")


@report_app.command("add-page")
def report_add_page(
    report_id: int = typer.Argument(..., help="report id to append to"),
    template: str = typer.Option(..., "--template", "-t", help="template_id for the new page"),
    input_path: Path = typer.Option(..., "--input", "-i", help="draft JSON ({blocks: {...}})"),
    name: Optional[str] = typer.Option(None, "--name", help="page name (shown in the page strip)"),
):
    """Append a new page to an existing report. New template allowed."""
    draft = _load_draft(input_path)
    try:
        snapshot = schemas.load()
    except schemas.SnapshotMissing as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    with ReportArchiveClient() as client:
        try:
            tpl = client.fetch_template(template)
        except ApiError as e:
            console.print(f"[red]template fetch failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(1)

        result = orchestrator.normalize_report(
            tpl, draft.get("blocks", {}), snapshot,
            extra_blocks_input=draft.get("extra_blocks", []),
        )
        _print_block_table(result)

        if result.failed_count > 0:
            console.print(f"[red]{result.failed_count} block(s) failed.[/red]")
            raise typer.Exit(2)

        try:
            # CR-11 — pass the fetched template (so add_page can auto-compute
            # blocks_order) and any explicit draft.blocks_order override. Without
            # this, the new page's blocks_order stays empty and the backend
            # falls back to showing every template block as a blank box.
            updated = report_ops.add_page(
                client, report_id,
                template_id=tpl["template_id"],
                template_version=tpl["version"],
                name=name,
                content=result.content,
                extra_blocks=result.extra_blocks,
                template=tpl,
                blocks_order=draft.get("blocks_order"),
            )
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]PATCH /reports/{report_id} failed ({e.status_code}):[/red] {e}")
            console.print_json(json.dumps(e.payload, ensure_ascii=False))
            raise typer.Exit(3)

    pages = updated.get("pages") or []
    console.print(f"[green]page added[/green]  report id={updated.get('id')}  "
                  f"pages now: {len(pages)}")
    console.print(f"  view: http://localhost:3001/reports/{updated.get('id')}")


@report_app.command("revise")
def report_revise(
    report_id: int = typer.Argument(..., help="report id to revise"),
    instruction: str = typer.Argument(..., help="natural-language revision instruction"),
    block_ids: Optional[str] = typer.Option(
        None, "--block-ids",
        help="comma-separated block ids to revise (eg 'summary,issues'). "
             "Either --block-ids or --all is required.",
    ),
    revise_all: bool = typer.Option(
        False, "--all",
        help="revise every filled block on the page (LLM may return some unchanged "
             "— the patch only contains blocks that actually changed)",
    ),
    page_index: int = typer.Option(0, "--page", help="0-based page index"),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="print the resulting patch JSON without POSTing",
    ),
    max_tokens: int = typer.Option(800, "--max-tokens"),
):
    """LLM-driven block-level revision of an existing report.

    For each target block: fetch its current content, prompt the LLM with
    (current content + revision instruction + schema), validate the result,
    and PATCH only the blocks whose content actually changed.

    CR-1 scoped_content protection applies — blocks not listed in --block-ids
    (and not changed when --all) are left untouched on the server.

    Examples:
      report revise 42 "summary에 PostgreSQL 15 마이그레이션 결과 한 줄 추가" --block-ids summary
      report revise 42 "이슈에서 결제 API 항목 제거" --block-ids issues
      report revise 42 "phase를 reviewing으로, summary를 더 간결하게" --all --dry-run
    """
    from report_skill import examples as examples_mod
    from report_skill import llm as llm_mod
    from report_skill import prompt as prompt_mod
    from report_skill.llm import LLMError

    if not block_ids and not revise_all:
        console.print("[red]either --block-ids or --all is required[/red]")
        raise typer.Exit(1)
    if not llm_mod.is_configured():
        console.print(
            "[red]no LLM provider configured.[/red]\n"
            "  set ANTHROPIC_API_KEY / OPENAI_API_KEY / OLLAMA_BASE_URL / SKILL_LLM_PROVIDER=bridge"
        )
        raise typer.Exit(1)

    try:
        snapshot = schemas.load()
    except schemas.SnapshotMissing as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    target_ids = [s.strip() for s in (block_ids or "").split(",") if s.strip()]

    with ReportArchiveClient() as client:
        try:
            existing = report_ops.fetch_report(client, report_id)
        except ApiError as e:
            console.print(f"[red]fetch report {report_id} failed:[/red] {e}")
            raise typer.Exit(1)

        pages = existing.get("pages") or []
        if page_index >= len(pages):
            console.print(f"[red]report {report_id} only has {len(pages)} page(s)[/red]")
            raise typer.Exit(1)

        page = pages[page_index]
        try:
            tpl = client.fetch_template(page["template_id"], page["template_version"])
        except ApiError as e:
            console.print(f"[red]template fetch failed:[/red] {e}")
            raise typer.Exit(1)

        tpl_blocks = (tpl.get("schema") or {}).get("blocks") or []
        tpl_blocks_by_id = {b["id"]: b for b in tpl_blocks if isinstance(b, dict)}
        current_content: dict = page.get("content") or {}

        # Default --all to every block the user actually filled on this page
        # (skip empty template blocks — nothing to revise).
        if revise_all:
            target_ids = [bid for bid in tpl_blocks_by_id
                          if bid in current_content and current_content[bid]]
            if not target_ids:
                console.print("[yellow]no filled blocks on this page to revise[/yellow]")
                raise typer.Exit(0)

        # Validate that every target id exists on this page.
        unknown = [bid for bid in target_ids if bid not in tpl_blocks_by_id]
        if unknown:
            console.print(f"[red]unknown block id(s) on page {page_index}:[/red] {unknown}")
            console.print(f"  available: {list(tpl_blocks_by_id.keys())}")
            raise typer.Exit(1)

        provider = llm_mod.get_provider()
        console.print(f"[dim]provider={provider.name}  blocks={target_ids}[/dim]")

        patch: dict[str, Any] = {}
        unchanged_count = 0
        failed_count = 0

        for bid in target_ids:
            block_def = tpl_blocks_by_id[bid]
            wtype = block_def["type"]
            schema = schemas.content_schema(snapshot, wtype)
            props = schemas.resolved_props(block_def, snapshot)
            example = examples_mod.load_example(wtype)
            cur = current_content.get(bid) or {}

            spec = prompt_mod.BlockSpec(
                block_id=bid, widget_type=wtype, props=props, content_schema=schema,
            )
            messages = prompt_mod.build_block_revise_prompt(
                spec,
                current_content=cur,
                revision_instruction=instruction,
                example=example.get("expected_content") if example else None,
            )

            try:
                reply = provider.generate(messages, max_tokens=max_tokens, json_mode=True)
                parsed = llm_mod.extract_json(reply)
            except LLMError as e:
                console.print(f"  [red]{bid}: LLM error[/red] {e}")
                failed_count += 1
                continue
            except (ValueError, KeyError) as e:
                console.print(f"  [yellow]{bid}: parse failed[/yellow] {e}")
                failed_count += 1
                continue

            if parsed is None:
                console.print(f"  [yellow]{bid}: no JSON in reply[/yellow]")
                failed_count += 1
                continue

            # Schema validate the new content.
            adapter = orchestrator.ADAPTERS.get(wtype)
            if adapter is None:
                console.print(f"  [yellow]{bid}: no adapter for widget type '{wtype}'; skipping[/yellow]")
                failed_count += 1
                continue
            try:
                normalized = adapter.normalize(parsed, props)
            except Exception as e:  # NormalizeError or anything the adapter raises
                console.print(f"  [red]{bid}: revision failed schema validation[/red] {e}")
                failed_count += 1
                continue

            if normalized == cur:
                console.print(f"  [dim]= {bid}: unchanged (instruction did not apply)[/dim]")
                unchanged_count += 1
                continue

            patch[bid] = normalized
            console.print(f"  [green]✓[/green] {bid} revised")

        if failed_count:
            console.print(f"[yellow]{failed_count} block(s) failed[/yellow]")
        if not patch:
            console.print("[yellow]no blocks changed — nothing to patch[/yellow]")
            raise typer.Exit(0)

        if dry_run:
            console.print(f"[dim]dry-run: patch covers {len(patch)} block(s):[/dim]")
            console.print_json(json.dumps({"blocks": patch}, ensure_ascii=False))
            raise typer.Exit(0)

        # CR-1 scoped_content protection applies automatically: report_ops.update_blocks
        # only writes the block ids we pass; everything else on the page stays intact.
        try:
            updated = report_ops.update_blocks(
                client, report_id,
                page_index=page_index,
                block_patches=patch,
            )
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]PATCH /reports/{report_id} failed:[/red] {e}")
            raise typer.Exit(3)

    console.print(f"[green]revised[/green]  report id={updated.get('id')}  "
                  f"patched_blocks={list(patch.keys())}  unchanged={unchanged_count}")


@report_app.command("append")
def report_append(
    report_id: int = typer.Argument(...),
    input_path: Path = typer.Option(..., "--input", "-i",
                                    help="patch JSON with 'blocks: {bid: <anything-ish>}'"),
    page_index: int = typer.Option(0, "--page"),
    max_retries: int = typer.Option(3, "--max-retries",
                                    help="revision-mismatch retries on parallel race"),
):
    """Merge new content into existing blocks (append, not replace).

    For each block_id in the input's `blocks` dict, runs the adapter then
    applies the per-widget merge strategy (see report_skill.merge). Uses
    optimistic locking — safe under parallel append calls.
    """
    draft = _load_draft(input_path)
    block_appends = draft.get("blocks") or {}
    if not block_appends:
        console.print("[red]input must contain 'blocks: {block_id: <input>}'[/red]")
        raise typer.Exit(1)

    with ReportArchiveClient() as client:
        try:
            updated = report_ops.append_to_blocks(
                client, report_id,
                block_appends=block_appends,
                page_index=page_index,
                max_retries=max_retries,
            )
        except report_ops.RevisionConflict as e:
            console.print(f"[red]revision conflict — gave up:[/red] {e}")
            raise typer.Exit(2)
        except (KeyError, ValueError) as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]PATCH failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(3)

    console.print(f"[green]appended[/green] to report {updated.get('id')}  "
                  f"revision={updated.get('revision')}")
    console.print(f"  view: http://localhost:3001/reports/{updated.get('id')}")


@report_app.command("milestone")
def report_milestone(
    action: str = typer.Argument(..., help="action — 'add' or 'remove'"),
    report_id: int = typer.Argument(...),
    date: str = typer.Option(..., "--date", "-d", help="ISO date (or Korean: '8월 1일')"),
    label: Optional[str] = typer.Option(None, "--label", "-l",
                                       help="required for 'add'; optional filter for 'remove'"),
    status: Optional[str] = typer.Option(None, "--status",
                                        help="pending | done | delayed"),
    note: Optional[str] = typer.Option(None, "--note"),
    block_id: Optional[str] = typer.Option(None, "--block-id",
                                          help="explicit milestone block id (auto-detected when omitted)"),
    page_index: int = typer.Option(0, "--page"),
):
    """Add or remove ONE milestone event. Convenience over `report append/remove`.

    Auto-detects the milestone block on the page if --block-id is omitted.
    """
    if action not in ("add", "remove"):
        console.print(f"[red]unknown action '{action}' (expected: add | remove)[/red]")
        raise typer.Exit(1)
    if action == "add" and not label:
        console.print("[red]--label is required for milestone add[/red]")
        raise typer.Exit(1)

    if action == "remove":
        with ReportArchiveClient() as client:
            target_bid = block_id
            if target_bid is None:
                try:
                    report = report_ops.fetch_report(client, report_id)
                except ApiError as e:
                    console.print(f"[red]fetch failed:[/red] {e}")
                    raise typer.Exit(1)
                target_bid = _find_block_of_type(report, "milestone", page_index)
                if target_bid is None:
                    console.print(f"[red]no milestone block found on page {page_index}.[/red]")
                    raise typer.Exit(1)
            match: dict = {"date": date}
            if label:
                match["label"] = label
            try:
                updated, n_removed = report_ops.remove_items(
                    client, report_id,
                    block_id=target_bid,
                    page_index=page_index,
                    match=match,
                )
            except AuthorLockedError as e:
                console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                              f"report_id={e.report_id}")
                raise typer.Exit(4)
            except _TYPED_LOCK_ERRORS as e:
                console.print(f"[red][{type(e).__name__}][/red] {e}")
                raise typer.Exit(4)
            except (ApiError, ValueError, KeyError, IndexError) as e:
                console.print(f"[red]remove failed:[/red] {e}")
                raise typer.Exit(2)
        if n_removed == 0:
            console.print(f"[yellow]no matching milestone removed[/yellow] "
                          f"(date={date}{', label=' + repr(label) if label else ''})")
        else:
            page = (updated.get("pages") or [None])[page_index]
            items = ((page or {}).get("content") or {}).get(target_bid, {}).get("items") or []
            console.print(f"[green]- removed[/green] {n_removed} milestone(s)  "
                          f"(block now has {len(items)} item(s), revision={updated.get('revision')})")
        return

    item: dict = {"date": date, "label": label}
    if status:
        item["status"] = status
    if note:
        item["note"] = note

    with ReportArchiveClient() as client:
        # Auto-detect block if not specified
        target_bid = block_id
        if target_bid is None:
            try:
                report = report_ops.fetch_report(client, report_id)
            except ApiError as e:
                console.print(f"[red]fetch failed:[/red] {e}")
                raise typer.Exit(1)
            target_bid = _find_block_of_type(report, "milestone", page_index)
            if target_bid is None:
                console.print(f"[red]no milestone block found on page {page_index}.[/red] "
                              "pass --block-id explicitly.")
                raise typer.Exit(1)
            console.print(f"[dim]target block: {target_bid}[/dim]")

        try:
            updated = report_ops.append_to_blocks(
                client, report_id,
                block_appends={target_bid: {"items": [item]}},
                page_index=page_index,
            )
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except (report_ops.RevisionConflict, ApiError, ValueError) as e:
            console.print(f"[red]append failed:[/red] {e}")
            raise typer.Exit(2)

    page = (updated.get("pages") or [None])[page_index]
    items = ((page or {}).get("content") or {}).get(target_bid, {}).get("items") or []
    console.print(f"[green]+ milestone[/green] {date} '{label}'  "
                  f"(block now has {len(items)} item(s), revision={updated.get('revision')})")


def _find_block_of_type(report: dict, widget_type: str, page_index: int) -> Optional[str]:
    """Scan a report's page (template blocks via templates not fetched here,
    extra_blocks directly) for the first block of the requested widget type.

    Note: template-defined blocks aren't visible without a fetch_template call.
    For convenience we just look at extra_blocks + content keys mapped to the
    page's first template fetch via the orchestrator path. Keep it simple:
    fetch the template inline.
    """
    pages = report.get("pages") or []
    if page_index >= len(pages):
        return None
    page = pages[page_index]
    for b in (page.get("extra_blocks") or []):
        if isinstance(b, dict) and b.get("type") == widget_type and b.get("id"):
            return b["id"]
    # Fall back to fetching the template to learn template-block types.
    with ReportArchiveClient() as client:
        try:
            tpl = client.fetch_template(page["template_id"], page["template_version"])
        except ApiError:
            return None
    for b in (tpl.get("schema") or {}).get("blocks") or []:
        if isinstance(b, dict) and b.get("type") == widget_type and b.get("id"):
            return b["id"]
    return None


@report_app.command("show")
def report_show(
    report_id: int = typer.Argument(...),
    json_out: bool = typer.Option(False, "--json", help="dump full JSON instead of pretty table"),
    page_index: Optional[int] = typer.Option(None, "--page", help="show only this page"),
):
    """Pretty-print a report's metadata + per-block summary without opening the browser."""
    with ReportArchiveClient() as client:
        try:
            r = report_ops.fetch_report(client, report_id)
        except ApiError as e:
            console.print(f"[red]fetch failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(1)

    if json_out:
        console.print_json(json.dumps(r, ensure_ascii=False, indent=2))
        return

    console.print(f"[bold cyan]#{r.get('id')}[/]  [white]{r.get('title')}[/]")
    meta = (
        f"  phase={r.get('phase') or r.get('status')}   "
        f"lifecycle={r.get('lifecycle') or '-'}   "
        f"revision={r.get('revision')}   "
        f"report_date={r.get('report_date')}   tags={r.get('tags') or []}"
    )
    console.print(f"[dim]{meta}[/dim]")

    pages = r.get("pages") or []
    targets = [page_index] if page_index is not None else list(range(len(pages)))
    for i in targets:
        if i < 0 or i >= len(pages):
            console.print(f"[red]page {i} out of range[/red]")
            continue
        p = pages[i]
        console.print(f"\n[bold]page {i}[/]  template={p.get('template_id')} "
                      f"v{p.get('template_version')}  name={p.get('name') or '-'}")
        content = p.get("content") or {}
        extras = p.get("extra_blocks") or []
        extras_by_id = {b.get("id"): b for b in extras if isinstance(b, dict)}

        tbl = Table(show_header=True, show_lines=False)
        tbl.add_column("block_id", style="cyan")
        tbl.add_column("origin", style="dim")
        tbl.add_column("size", justify="right")
        tbl.add_column("preview")

        for bid in sorted(content.keys()):
            v = content[bid]
            origin = "extra" if bid in extras_by_id else "template"
            size, preview = _summarize_block_content(v)
            tbl.add_row(bid, origin, size, preview)
        # Show extras that have no content too
        for bid in sorted(extras_by_id.keys()):
            if bid in content:
                continue
            tbl.add_row(bid, "extra", "-", "(no content)")
        if tbl.row_count == 0:
            console.print("  [dim](empty)[/dim]")
        else:
            console.print(tbl)


def _summarize_block_content(v: Any) -> tuple[str, str]:
    """Return (size-string, single-line preview) for one block's content."""
    if not isinstance(v, dict):
        return ("?", str(v)[:60])
    if "items" in v and isinstance(v["items"], list):
        items = v["items"]
        if items and isinstance(items[0], dict):
            first = items[0]
            preview = f"{first}"[:60]
        elif items:
            preview = str(items[0])[:60]
        else:
            preview = "(empty)"
        return (f"{len(items)} items", preview)
    if "rows" in v and isinstance(v["rows"], list):
        rows = v["rows"]
        preview = str(rows[0])[:60] if rows else "(empty)"
        return (f"{len(rows)} rows", preview)
    if "markdown" in v and isinstance(v["markdown"], str):
        md = v["markdown"]
        return (f"{len(md)} chars", md[:60].replace("\n", " "))
    if "text" in v and isinstance(v["text"], str):
        return (f"{len(v['text'])} chars", v["text"][:60])
    if "latex" in v:
        return ("equation", str(v["latex"])[:60])
    return (f"{len(v)} keys", str(list(v.keys()))[:60])


@report_app.command("lock-status")
def report_lock_status(
    report_id: int = typer.Argument(..., help="report id to inspect"),
):
    """Show author-lock state (author_lock_enabled / reason / set_at)."""
    with ReportArchiveClient() as client:
        try:
            row = client.fetch_report_lock_status(report_id)
        except ApiError as e:
            console.print(f"[red]lock-status failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


@report_app.command("activities")
def report_activities(
    report_id: int = typer.Argument(..., help="report id whose timeline to show"),
    limit: int = typer.Option(20, "--limit", min=1, max=200,
                              help="page size (max 200)"),
    before_id: Optional[int] = typer.Option(
        None, "--before-id",
        help="cursor — pass the smallest id from the previous page",
    ),
):
    """GET /reports/{id}/activities — newest-first activity timeline.

    Use to verify side-effects after a write: did the publish notify the
    mounted-board members? Did the author-lock toggle fire? Public-only
    viewers get an empty list per backend policy.
    """
    with ReportArchiveClient() as client:
        try:
            body = client.fetch_report_activities(
                report_id, limit=limit, before_id=before_id,
            )
        except ApiError as e:
            console.print(f"[red]activities failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(body, ensure_ascii=False))


@report_app.command("mount")
def report_mount(
    report_id: int = typer.Argument(...),
    workspace: list[str] = typer.Option(..., "--workspace", "--workspace-slug", "-w",
                                       help="workspace slug(s) to mount onto (repeat for multiple)"),
    edit_policy: str = typer.Option(
        "default", "--edit-policy",
        help="default | owner_only | coauthor",
    ),
    note: str = typer.Option("", "--note", help="optional mount note"),
    folder_id: Optional[int] = typer.Option(None, "--folder-id",
                                           help="org folder id within the target workspace"),
):
    """Publish (mount) a report to one or more org board workspaces.

    After `report create`, new reports live in your personal workspace
    only. To make them visible on a team board (eg. `dx`), mount them
    here — that's the deliberate publish step.
    """
    with ReportArchiveClient() as client:
        try:
            created = report_ops.mount_report(
                client, report_id,
                workspace_slugs=workspace,
                edit_policy=edit_policy,
                note=note,
                folder_id=folder_id,
            )
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]mount failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    if created:
        for m in created:
            console.print(f"[green]+ mount[/green]  workspace={m.get('workspace_slug')}  "
                          f"edit_policy={m.get('edit_policy')}")
    else:
        console.print("[yellow]no new mounts created[/yellow] "
                      "(all requested workspaces were already mounted)")


@report_app.command("unmount")
def report_unmount(
    report_id: int = typer.Argument(...),
    workspace: str = typer.Option(..., "--workspace", "--workspace-slug", "-w",
                                  help="workspace slug to remove the mount from"),
):
    """Remove a report's mount from one workspace board."""
    with ReportArchiveClient() as client:
        try:
            report_ops.unmount_report(client, report_id, workspace)
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]unmount failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print(f"[green]- unmounted[/green]  report={report_id}  workspace={workspace}")


@report_app.command("mounts")
def report_mounts(report_id: int = typer.Argument(...)):
    """List all workspaces this report is mounted on."""
    with ReportArchiveClient() as client:
        try:
            mounts = report_ops.list_mounts(client, report_id)
        except ApiError as e:
            console.print(f"[red]list mounts failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(1)
    if not mounts:
        console.print(f"[dim]report {report_id} is not mounted on any board[/dim]")
        return
    tbl = Table(title=f"mounts for report {report_id} ({len(mounts)})")
    tbl.add_column("workspace", style="cyan")
    tbl.add_column("edit_policy")
    tbl.add_column("folder_id", justify="right")
    tbl.add_column("mounted_at", style="dim")
    for m in mounts:
        tbl.add_row(
            str(m.get("workspace_slug")),
            str(m.get("edit_policy")),
            str(m.get("folder_id") or "-"),
            str(m.get("mounted_at", ""))[:19],
        )
    console.print(tbl)


@report_app.command("add-link")
def report_add_link(
    report_id: int = typer.Argument(..., help="source report id"),
    to_report_id: int = typer.Option(..., "--to",
                                     help="other report id to link to"),
    direction: str = typer.Option(
        "outgoing", "--direction",
        help="outgoing (default — this report → other) | incoming (other → this report)",
    ),
    kind: str = typer.Option("related", "--kind",
                             help="link kind (e.g. related / blocks / follows / supersedes)"),
    label: Optional[str] = typer.Option(None, "--label",
                                        help="optional human-readable link label"),
):
    """POST /reports/{id}/links — link this report to another.

    `--direction outgoing` (default) creates a link FROM this report TO the
    other. `--direction incoming` flips it: the server swaps from/to so the
    link points the OTHER way (other report → this report). Use incoming
    when this report is being CITED by another and you want the citation to
    show up as a back-reference.
    """
    if direction not in ("outgoing", "incoming"):
        console.print(f"[red]invalid --direction '{direction}' "
                      "(expected: outgoing | incoming)[/red]")
        raise typer.Exit(1)
    with ReportArchiveClient() as client:
        try:
            link = client.add_report_link(
                report_id,
                to_report_id=to_report_id,
                kind=kind,
                label=label,
                direction=direction,
            )
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]add-link failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(link, ensure_ascii=False))


@report_app.command("delete")
def report_delete(
    report_id: int = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y", help="skip confirmation"),
):
    """DELETE /reports/{id}. Asks for confirmation unless --yes."""
    if not yes:
        confirm = typer.confirm(f"delete report {report_id}?", default=False)
        if not confirm:
            raise typer.Exit(0)
    with ReportArchiveClient() as client:
        try:
            report_ops.delete_report(client, report_id)
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]DELETE /reports/{report_id} failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(3)
    console.print(f"[green]deleted[/green]  report {report_id}")


# --------------------------------------------------------------------------- #
# tier commands
# --------------------------------------------------------------------------- #
@tier_app.command("show")
def tier_show():
    """Display the active AI tier and the policy knobs it implies."""
    profile = tier.current_tier()
    pol = tier.policy()
    console.print(f"[bold]tier:[/bold] {profile.tier}  ([dim]{profile.source}[/dim])  "
                  f"{profile.notes}")
    console.print(f"  blocks_per_call:      {pol.blocks_per_call}")
    console.print(f"  max_repair_passes:    {pol.max_repair_passes}")
    console.print(f"  enable_llm_retry:     {pol.enable_llm_retry}")
    console.print(f"  max_llm_retries:      {pol.max_llm_retries}")
    console.print(f"  aggressive_enum:      {pol.aggressive_enum_match}")
    console.print(f"  fallback_chain_depth: {pol.fallback_chain_depth}")


# --------------------------------------------------------------------------- #
# bridge commands — LLM bridge inspection
# --------------------------------------------------------------------------- #
@bridge_app.command("status")
def bridge_status():
    """List pending bridge requests (used by the human-in-loop LLM provider)."""
    from report_skill.config import CACHE_DIR
    bridge_dir = CACHE_DIR / "bridge"
    if not bridge_dir.is_dir():
        console.print("[dim]bridge directory does not exist yet (no requests posted)[/dim]")
        return
    pending = sorted(bridge_dir.glob("*.req.json"))
    if not pending:
        console.print("[green]bridge queue: empty[/green]")
        return
    from datetime import datetime as _dt
    tbl = Table(title=f"bridge queue ({len(pending)} pending)", show_lines=False)
    tbl.add_column("request id", style="cyan")
    tbl.add_column("age", justify="right")
    tbl.add_column("size", justify="right")
    tbl.add_column("response exists?", justify="center")
    for p in pending:
        rid = p.stem.replace(".req", "")
        age = _dt.now().timestamp() - p.stat().st_mtime
        resp = bridge_dir / f"{rid}.resp.txt"
        tbl.add_row(rid, f"{int(age)}s", f"{p.stat().st_size}B",
                    "✓" if resp.exists() else "·")
    console.print(tbl)


@bridge_app.command("clear")
def bridge_clear(
    yes: bool = typer.Option(False, "--yes", "-y", help="skip confirmation"),
):
    """Wipe all pending bridge requests + responses."""
    from report_skill.config import CACHE_DIR
    bridge_dir = CACHE_DIR / "bridge"
    if not bridge_dir.is_dir():
        console.print("[dim]nothing to clear[/dim]")
        return
    files = list(bridge_dir.glob("*"))
    if not files:
        console.print("[dim]bridge dir empty[/dim]")
        return
    if not yes:
        if not typer.confirm(f"delete {len(files)} bridge file(s)?", default=False):
            raise typer.Exit(0)
    for f in files:
        try:
            f.unlink()
        except OSError as e:
            console.print(f"[yellow]could not delete {f}:[/yellow] {e}")
    console.print(f"[green]cleared {len(files)} file(s) from bridge dir[/green]")


@tier_app.command("set")
def tier_set(level: str = typer.Argument(..., help="S / M / W")):
    """Persist a tier override to .skill-cache/tier.json."""
    level = level.upper()
    if level not in ("S", "M", "W"):
        console.print(f"[red]invalid tier '{level}' — must be S, M, or W[/red]")
        raise typer.Exit(1)
    profile = tier.set_tier(level, source="manual", notes="set via CLI")
    console.print(f"[green]tier set:[/green] {profile.tier}  (persisted)")


# --------------------------------------------------------------------------- #
# v0.5.0 — report copy / publish / unpublish / new-from-preset
# --------------------------------------------------------------------------- #
@report_app.command("copy")
def report_copy(
    report_id: int = typer.Argument(..., help="source report id to copy"),
    title: str = typer.Option(..., "--title", help="title for the new copy"),
    mode: str = typer.Option("full", "--mode", help="content | full"),
    folder_id: Optional[int] = typer.Option(None, "--folder-id",
                                            help="personal folder to drop the copy into"),
):
    """POST /reports/{id}/copy — duplicate a report into your personal workspace."""
    if mode not in ("content", "full"):
        console.print(f"[red]invalid mode '{mode}' (expected: content | full)[/red]")
        raise typer.Exit(1)
    with ReportArchiveClient() as client:
        try:
            created = client.copy_report(
                report_id,
                title=title,
                mode=mode,
                folder_id=folder_id,
            )
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]copy failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    rid = created.get("id") if isinstance(created, dict) else None
    console.print(f"[green]copied[/green]  new report id={rid}  "
                  f"title={created.get('title')!r}  mode={mode}")
    console.print(f"  view: http://localhost:3001/reports/{rid}")


@report_app.command("publish")
def report_publish(
    report_id: int = typer.Argument(..., help="report id to publish (phase → finalized)"),
):
    """POST /reports/{id}/publish — mark report as finalized + fan out notifications."""
    with ReportArchiveClient() as client:
        try:
            updated = client.publish_report(report_id)
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]publish failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print(f"[green]published[/green]  report id={updated.get('id')}  "
                  f"phase={updated.get('phase')}")


@report_app.command("unpublish")
def report_unpublish(
    report_id: int = typer.Argument(..., help="report id to unpublish (phase → drafting)"),
):
    """POST /reports/{id}/unpublish — revert finalized report back to drafting."""
    with ReportArchiveClient() as client:
        try:
            updated = client.unpublish_report(report_id)
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]unpublish failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print(f"[green]unpublished[/green]  report id={updated.get('id')}  "
                  f"phase={updated.get('phase')}")


@report_app.command("new-from-preset")
def report_new_from_preset(
    preset_id: str = typer.Argument(..., help="preset id to instantiate"),
    title: Optional[str] = typer.Option(None, "--title",
                                        help="override the new report's title (defaults to preset name)"),
    folder_id: Optional[int] = typer.Option(None, "--folder-id",
                                            help="personal folder to drop the new report into"),
):
    """POST /presets/{id}/new-report — create a new report from a preset."""
    with ReportArchiveClient() as client:
        try:
            created = client.new_report_from_preset(
                preset_id,
                title=title,
                folder_id=folder_id,
            )
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]new-from-preset failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    rid = created.get("id") if isinstance(created, dict) else None
    console.print(f"[green]created[/green]  report id={rid}  "
                  f"workspace={created.get('workspace_slug')}")
    console.print(f"  view: http://localhost:3001/reports/{rid}")


# --------------------------------------------------------------------------- #
# v0.5.0 — tools sub-app: report-types / folders / presets / composites lookups
# --------------------------------------------------------------------------- #
@tools_app.command("report-types-list")
def tools_report_types_list():
    """List all report types (id, name, status). GET /report-types."""
    from report_skill.mcp_server import _do_report_types_list
    rows = _do_report_types_list({})
    console.print_json(json.dumps(rows, ensure_ascii=False))


@tools_app.command("folders-list")
def tools_folders_list(
    workspace: str = typer.Option(..., "--workspace", "--workspace-slug", help="workspace slug"),
):
    """List folders inside the given workspace board. GET /folders?workspace_slug=."""
    from report_skill.mcp_server import _do_folders_list
    rows = _do_folders_list({"workspace_slug": workspace})
    console.print_json(json.dumps(rows, ensure_ascii=False))


@tools_app.command("presets-list")
def tools_presets_list(
    template_id: Optional[int] = typer.Option(None, "--template-id",
                                              help="optional template id filter"),
):
    """List presets visible to the actor. GET /presets[?template_id=]."""
    from report_skill.mcp_server import _do_presets_list
    rows = _do_presets_list({"template_id": template_id})
    console.print_json(json.dumps(rows, ensure_ascii=False))


@tools_app.command("composites-submittable-for")
def tools_composites_submittable_for(
    report_id: int = typer.Option(..., "--report-id",
                                  help="report id to find submittable composites for"),
):
    """List composites this report can be submitted into. GET /composites/submittable-for/{id}."""
    from report_skill.mcp_server import _do_composites_submittable_for
    rows = _do_composites_submittable_for({"report_id": report_id})
    console.print_json(json.dumps(rows, ensure_ascii=False))


@tools_app.command("composites-requests-list")
def tools_composites_requests_list(
    composite_id: int = typer.Option(..., "--composite-id",
                                     help="composite id whose requests to list"),
    status: Optional[str] = typer.Option(
        None, "--status",
        help="status_filter (e.g. pending/accepted/rejected/withdrawn)",
    ),
):
    """List submission requests for a composite. GET /composites/{id}/requests."""
    from report_skill.mcp_server import _do_composites_requests_list
    args: dict[str, Any] = {"composite_id": composite_id}
    if status is not None:
        args["status_filter"] = status
    rows = _do_composites_requests_list(args)
    console.print_json(json.dumps(rows, ensure_ascii=False))


@tools_app.command("composites-submit")
def tools_composites_submit(
    composite_id: int = typer.Option(..., "--composite-id"),
    report_id: int = typer.Option(..., "--report-id"),
    note: Optional[str] = typer.Option(None, "--note",
                                       help="optional submission note (max 1000 chars)"),
):
    """Submit a report to a composite. POST /composites/{id}/requests."""
    from report_skill.mcp_server import _do_composites_submit
    args: dict[str, Any] = {"composite_id": composite_id, "report_id": report_id}
    if note is not None:
        args["note"] = note
    try:
        row = _do_composites_submit(args)
    except AuthorLockedError as e:
        console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                      f"report_id={e.report_id}")
        raise typer.Exit(4)
    except _TYPED_LOCK_ERRORS as e:
        console.print(f"[red][{type(e).__name__}][/red] {e}")
        raise typer.Exit(4)
    except ApiError as e:
        console.print(f"[red]submit failed ({e.status_code}):[/red] {e}")
        raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# v0.5.2 — tools sub-app: preset-create / preset-delete / widgets-* mirrors
# --------------------------------------------------------------------------- #
@tools_app.command("preset-create")
def tools_preset_create(
    from_report: int = typer.Option(..., "--from-report",
                                    help="source report id to snapshot into a preset"),
    name: str = typer.Option(..., "--name",
                             help="preset name (shown in the new-from-preset picker)"),
    description: Optional[str] = typer.Option(
        None, "--description",
        help="optional human-readable description (max 1000 chars)",
    ),
    workspace: Optional[list[str]] = typer.Option(
        None, "--workspace", "--workspace-slug",
        help="workspace slug(s) that own the preset (repeat for multiple); "
             "omit to use the report's home workspace",
    ),
):
    """POST /presets — snapshot a report into a reusable preset.

    Wrapper around the `preset_create` MCP tool. Slug arg `--workspace`
    is plural — repeat the flag for each owner workspace.
    """
    args: dict[str, Any] = {"report_id": from_report, "name": name}
    if description is not None:
        args["description"] = description
    if workspace:
        args["owner_workspace_slugs"] = list(workspace)
    from report_skill.mcp_server import _do_preset_create
    try:
        row = _do_preset_create(args)
    except AuthorLockedError as e:
        console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                      f"report_id={e.report_id}")
        raise typer.Exit(4)
    except _TYPED_LOCK_ERRORS as e:
        console.print(f"[red][{type(e).__name__}][/red] {e}")
        raise typer.Exit(4)
    except ApiError as e:
        console.print(f"[red]preset-create failed ({e.status_code}):[/red] {e}")
        raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


@tools_app.command("preset-delete")
def tools_preset_delete(
    preset_id: int = typer.Argument(..., help="preset id to delete"),
):
    """DELETE /presets/{id} — remove a preset. Wrapper around `preset_delete`."""
    from report_skill.mcp_server import _do_preset_delete
    try:
        row = _do_preset_delete({"preset_id": preset_id})
    except AuthorLockedError as e:
        console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                      f"report_id={e.report_id}")
        raise typer.Exit(4)
    except _TYPED_LOCK_ERRORS as e:
        console.print(f"[red][{type(e).__name__}][/red] {e}")
        raise typer.Exit(4)
    except ApiError as e:
        console.print(f"[red]preset-delete failed ({e.status_code}):[/red] {e}")
        raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


@tools_app.command("widgets-suggest-extras")
def tools_widgets_suggest_extras(
    topic: str = typer.Option(..., "--topic",
                              help="raw user text — scanned for chartable/temporal/hierarchy patterns"),
    max_extras: int = typer.Option(5, "--max-extras", min=0, max=20,
                                   help="cap on suggestion count"),
    use_llm: str = typer.Option("auto", "--use-llm",
                                help="auto | yes | no — whether to call the LLM for ranking"),
):
    """Suggest extra (visual) blocks for a given user-text topic.

    Wrapper around the `widgets_suggest_extras` MCP tool — output is a
    JSON list of {suggested_id, widget_type, props, input, confidence,
    matched_pattern, source}.
    """
    from report_skill.mcp_server import _do_widgets_suggest_extras
    rows = _do_widgets_suggest_extras({
        "text": topic, "max_extras": max_extras, "use_llm": use_llm,
    })
    console.print_json(json.dumps(rows, ensure_ascii=False))


@tools_app.command("widgets-catalog")
def tools_widgets_catalog(
    widget_type: Optional[str] = typer.Option(
        None, "--type",
        help="single widget slug (e.g. table) — returns that widget's full "
             "catalog entry. Omit to get a summary across the cached catalog.",
    ),
):
    """Show the cached widget catalog (or one widget's entry).

    Wrapper around the `widgets_catalog` MCP tool. The catalog is read from
    .skill-cache/widgets.json — run `catalog sync` if it's missing.
    """
    from report_skill.mcp_server import _do_widgets_catalog
    args: dict[str, Any] = {}
    if widget_type is not None:
        args["widget_type"] = widget_type
    try:
        row = _do_widgets_catalog(args)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    console.print_json(json.dumps(row, ensure_ascii=False))


@tools_app.command("widget-relations-list")
def tools_widget_relations_list():
    """List widget-relation slugs (rich_text mention chip targets). GET /api/widget-relations."""
    with ReportArchiveClient() as c:
        console.print_json(json.dumps(c.list_widget_relations(), ensure_ascii=False))


@report_app.command("trash")
def report_trash_cmd(report_id: int = typer.Argument(..., help="report id")):
    """POST /reports/{id}/trash — move report to the trash (soft delete).
    Blocked while the report is mounted to any board (v0.10.0+, RA ff64778)."""
    with ReportArchiveClient() as c:
        try:
            row = c.trash_report(report_id)
        except TrashRestoreForbiddenError as e:
            console.print(f"[red][trash_restore_forbidden][/red] {e}  "
                          "(only the report owner / sys admin may trash this report)")
            raise typer.Exit(3)
        except ApiError as e:
            console.print(f"[red]trash failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


@report_app.command("restore")
def report_restore_cmd(report_id: int = typer.Argument(..., help="report id")):
    """POST /reports/{id}/restore — recover a report from the trash."""
    with ReportArchiveClient() as c:
        try:
            row = c.restore_report(report_id)
        except TrashRestoreForbiddenError as e:
            console.print(f"[red][trash_restore_forbidden][/red] {e}  "
                          "(only the report owner / sys admin may restore this report)")
            raise typer.Exit(3)
        except ApiError as e:
            console.print(f"[red]restore failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


@report_app.command("takedown-request")
def report_takedown_request_cmd(
    report_id: int = typer.Argument(..., help="report id"),
    workspace: str = typer.Option(..., "--workspace", "--workspace-slug",
                                   help="target board slug to take down from"),
    reason: Optional[str] = typer.Option(None, "--reason",
                                          help="why the takedown is requested"),
):
    """POST /reports/{id}/takedown-requests — ask the board manager to unmount.

    Use this when the owner cannot unmount directly (manager edit-policy).
    The board manager (or sys admin) then approves or rejects via the
    takedowns sub-app."""
    with ReportArchiveClient() as c:
        try:
            row = c.request_report_takedown(
                report_id, workspace_slug=workspace, reason=reason,
            )
        except TakedownOwnerForbiddenError as e:
            console.print(f"[red][takedown_owner_forbidden][/red] {e}  "
                          "(only the report owner may submit a takedown request)")
            raise typer.Exit(3)
        except ApiError as e:
            console.print(f"[red]takedown-request failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


@tools_app.command("takedowns-list")
def tools_takedowns_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "--workspace-slug",
                                             help="filter by board slug"),
    status: Optional[str] = typer.Option(None, "--status",
                                          help="pending | approved | rejected"),
):
    """GET /takedown-requests — manager / sys admin view of the queue."""
    with ReportArchiveClient() as c:
        try:
            rows = c.list_takedown_requests(workspace_slug=workspace, status=status)
        except ApiError as e:
            console.print(f"[red]takedowns-list failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(rows, ensure_ascii=False))


@tools_app.command("takedown-approve")
def tools_takedown_approve(request_id: int = typer.Argument(..., help="takedown request id")):
    """POST /takedown-requests/{id}/approve — unmount and close the request."""
    with ReportArchiveClient() as c:
        try:
            row = c.approve_takedown_request(request_id)
        except TakedownAlreadyProcessedError as e:
            console.print(f"[red][takedown_already_processed][/red] {e}  "
                          "(this request was already approved / rejected — do not retry)")
            raise typer.Exit(3)
        except TakedownManagerForbiddenError as e:
            console.print(f"[red][takedown_manager_forbidden][/red] {e}  "
                          "(only the target board's manager / sys admin may approve)")
            raise typer.Exit(3)
        except ApiError as e:
            console.print(f"[red]takedown-approve failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


@tools_app.command("takedown-reject")
def tools_takedown_reject(
    request_id: int = typer.Argument(..., help="takedown request id"),
    reason: Optional[str] = typer.Option(None, "--reason",
                                          help="explanation surfaced to the requester"),
):
    """POST /takedown-requests/{id}/reject — leave the report mounted."""
    with ReportArchiveClient() as c:
        try:
            row = c.reject_takedown_request(request_id, reason=reason)
        except TakedownAlreadyProcessedError as e:
            console.print(f"[red][takedown_already_processed][/red] {e}  "
                          "(this request was already approved / rejected — do not retry)")
            raise typer.Exit(3)
        except TakedownManagerForbiddenError as e:
            console.print(f"[red][takedown_manager_forbidden][/red] {e}  "
                          "(only the target board's manager / sys admin may reject)")
            raise typer.Exit(3)
        except ApiError as e:
            console.print(f"[red]takedown-reject failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


@tools_app.command("widget-ref-categories-list")
def tools_widget_ref_categories_list():
    """List #widget cross-reference categories (그림 / 표 / 비교표 / 수식 / 목록 ...).

    v0.9.0+ (RA 074233d). Projection of GET /api/widgets → ref_categories.
    The rich_text body uses these for #widget refs, numbered live at render time
    per (page, id) reading order — categories drive the human prefix (그림 N, 표 N)."""
    with ReportArchiveClient() as c:
        console.print_json(json.dumps(c.list_ref_categories(), ensure_ascii=False))


# v0.11.0 — content-aware read commands
@report_app.command("outline")
def report_outline_cmd(report_id: int = typer.Argument(..., help="report id")):
    """Tree outline of a report — every page + block id + widget type + short
    preview. NO content bodies. Use this first to navigate before fetching
    specific blocks."""
    from report_skill.mcp_server import _do_report_outline
    console.print_json(json.dumps(
        _do_report_outline({"report_id": report_id}), ensure_ascii=False))


@report_app.command("page-show")
def report_page_show_cmd(
    report_id: int = typer.Argument(..., help="report id"),
    page_index: int = typer.Argument(..., help="0-based page index"),
    full: bool = typer.Option(False, "--full",
                              help="dump raw content (no truncation; may be large)"),
):
    """Dump one page completely — every block on the page with current content."""
    from report_skill.mcp_server import _do_page_show_content
    console.print_json(json.dumps(_do_page_show_content({
        "report_id": report_id, "page_index": page_index, "truncate": not full,
    }), ensure_ascii=False))


@report_app.command("block-show")
def report_block_show_cmd(
    report_id: int = typer.Argument(..., help="report id"),
    page_index: int = typer.Argument(..., help="0-based page index"),
    block_id: str = typer.Argument(..., help="block id (e.g. 'risks_table')"),
):
    """Pin-point fetch of one block — content + widget type + props + schema."""
    from report_skill.mcp_server import _do_block_show
    console.print_json(json.dumps(_do_block_show({
        "report_id": report_id, "page_index": page_index, "block_id": block_id,
    }), ensure_ascii=False))


@report_app.command("block-preview")
def report_block_preview_cmd(
    report_id: int = typer.Argument(..., help="report id"),
    page_index: int = typer.Argument(..., help="0-based page index"),
    block_id: str = typer.Argument(..., help="block id"),
):
    """Markdown / plain-text preview of a block for visual inspection."""
    from report_skill.mcp_server import _do_block_preview
    result = _do_block_preview({
        "report_id": report_id, "page_index": page_index, "block_id": block_id,
    })
    console.print(f"[bold]block_id:[/bold] {result['block_id']}  "
                  f"[bold]widget:[/bold] {result['widget_type']}\n")
    console.print(result["preview_markdown"])


# --------------------------------------------------------------------------- #
# v0.5.0 — composites sub-app: accept / reject / withdraw
# v0.5.2 — composites get
# --------------------------------------------------------------------------- #
@composites_app.command("get")
def composites_get(
    composite_id: int = typer.Argument(..., help="composite report id"),
):
    """GET /composites/{id} — fetch a composite report's full state."""
    with ReportArchiveClient() as client:
        try:
            row = client.get_composite(composite_id)
        except ApiError as e:
            console.print(f"[red]get composite failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(1)
    console.print_json(json.dumps(row, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# v0.6.0 — composites body editing
# --------------------------------------------------------------------------- #
@composites_app.command("create")
def composites_create(
    title: str = typer.Option(..., "--title", help="composite title"),
    kind: str = typer.Option(..., "--kind",
                              help="CompositeKind enum value (recurring | theme | ...)"),
    view_mode: str = typer.Option("single", "--view-mode",
                                   help="single | two_col | list"),
    period_date: Optional[str] = typer.Option(
        None, "--period-date",
        help="ISO YYYY-MM-DD (recurring composites)",
    ),
    workspace: Optional[str] = typer.Option(
        None, "--workspace", "--workspace-slug",
        help="owner workspace slug; defaults to active workspace",
    ),
    description: str = typer.Option("", "--description",
                                     help="initial description"),
    two_col_view: bool = typer.Option(
        False, "--two-col-view",
        help="enable two-column view layout (composites_create body flag)",
    ),
    items_file: Optional[Path] = typer.Option(
        None, "--items-file", "-i",
        help="optional JSON file with items[] array to seed at creation time",
    ),
    summary_widgets_file: Optional[Path] = typer.Option(
        None, "--summary-widgets-file",
        help="optional JSON file with a summary_widgets[] list to seed at "
             "creation time",
    ),
):
    """POST /composites — create a new composite report.

    Pass `--items-file` to seed agenda items at creation; otherwise the
    composite starts empty and you edit items via `composites items-set`.
    """
    items: Optional[list[dict]] = None
    if items_file is not None:
        try:
            text = items_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            console.print(f"[red]--items-file not found:[/red] {items_file}")
            raise typer.Exit(1)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as e:
            typer.echo(
                f"Invalid JSON in --items-file ({items_file}): line {e.lineno}, "
                f"column {e.colno}: {e.msg}",
                err=True,
            )
            raise typer.Exit(1)
        if isinstance(payload, dict) and "items" in payload:
            items = list(payload.get("items") or [])
        elif isinstance(payload, list):
            items = list(payload)
        else:
            console.print("[red]--items-file must contain a JSON list or "
                          "{items: [...]} object[/red]")
            raise typer.Exit(1)

    summary_widgets: Optional[list[dict]] = None
    if summary_widgets_file is not None:
        try:
            text = summary_widgets_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            console.print(
                f"[red]--summary-widgets-file not found:[/red] {summary_widgets_file}"
            )
            raise typer.Exit(1)
        try:
            sw_payload = json.loads(text)
        except json.JSONDecodeError as e:
            typer.echo(
                f"Invalid JSON in --summary-widgets-file "
                f"({summary_widgets_file}): line {e.lineno}, "
                f"column {e.colno}: {e.msg}",
                err=True,
            )
            raise typer.Exit(1)
        if isinstance(sw_payload, list):
            summary_widgets = list(sw_payload)
        elif isinstance(sw_payload, dict) and "summary_widgets" in sw_payload:
            summary_widgets = list(sw_payload.get("summary_widgets") or [])
        else:
            console.print("[red]--summary-widgets-file must contain a JSON "
                          "list or {summary_widgets: [...]} object[/red]")
            raise typer.Exit(1)

    with ReportArchiveClient() as client:
        try:
            row = client.create_composite(
                title=title, kind=kind, view_mode=view_mode,
                period_date=period_date, workspace_slug=workspace,
                description=description, items=items,
                two_col_view=two_col_view,
                summary_widgets=summary_widgets,
            )
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]composite create failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


@composites_app.command("update")
def composites_update(
    composite_id: int = typer.Argument(..., help="composite id to update"),
    title: Optional[str] = typer.Option(None, "--title"),
    view_mode: Optional[str] = typer.Option(None, "--view-mode",
                                             help="single | two_col | list"),
    description: Optional[str] = typer.Option(None, "--description"),
    two_col_view: Optional[bool] = typer.Option(None, "--two-col-view"),
    period_date: Optional[str] = typer.Option(
        None, "--period-date",
        help="ISO YYYY-MM-DD; pass an empty string to clear",
    ),
    summary_widgets_file: Optional[Path] = typer.Option(
        None, "--summary-widgets-file",
        help="optional JSON file with a summary_widgets[] list; replaces the "
             "composite's summary widget panel",
    ),
    expected_revision: Optional[int] = typer.Option(
        None, "--expected-revision",
        help="optimistic concurrency guard (409 on mismatch)",
    ),
):
    """PATCH /composites/{id} — update top-level fields only.

    Only the fields you pass on the CLI are sent. To replace items, use
    `composites items-set` instead.
    """
    kwargs: dict[str, Any] = {}
    if title is not None:
        kwargs["title"] = title
    if view_mode is not None:
        kwargs["view_mode"] = view_mode
    if description is not None:
        kwargs["description"] = description
    if two_col_view is not None:
        kwargs["two_col_view"] = two_col_view
    if period_date is not None:
        # Empty string is the CLI signal for "clear" (None reaches the body).
        kwargs["period_date"] = period_date if period_date else None
    if summary_widgets_file is not None:
        try:
            text = summary_widgets_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            console.print(
                f"[red]--summary-widgets-file not found:[/red] {summary_widgets_file}"
            )
            raise typer.Exit(1)
        try:
            sw_payload = json.loads(text)
        except json.JSONDecodeError as e:
            typer.echo(
                f"Invalid JSON in --summary-widgets-file "
                f"({summary_widgets_file}): line {e.lineno}, "
                f"column {e.colno}: {e.msg}",
                err=True,
            )
            raise typer.Exit(1)
        if isinstance(sw_payload, list):
            kwargs["summary_widgets"] = list(sw_payload)
        elif isinstance(sw_payload, dict) and "summary_widgets" in sw_payload:
            kwargs["summary_widgets"] = list(
                sw_payload.get("summary_widgets") or []
            )
        else:
            console.print("[red]--summary-widgets-file must contain a JSON "
                          "list or {summary_widgets: [...]} object[/red]")
            raise typer.Exit(1)
    if expected_revision is not None:
        kwargs["expected_revision"] = expected_revision

    with ReportArchiveClient() as client:
        try:
            row = client.update_composite(composite_id, **kwargs)
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]composite update failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


@composites_app.command("items-set")
def composites_items_set(
    composite_id: int = typer.Argument(..., help="composite id"),
    items_file: Path = typer.Option(
        ..., "--items-file", "-i",
        help="JSON file with the replacement items[] array (or "
             "{items: [...]} object)",
    ),
    expected_revision: Optional[int] = typer.Option(
        None, "--expected-revision",
        help="optimistic concurrency guard (409 on mismatch)",
    ),
):
    """PATCH /composites/{id} with full items[] replacement.

    Each item supplies exactly one of `ref_report_id` / `ref_composite_id`,
    plus optional `note` / `display_column` / `group_name`. Order matters
    (position is taken from list index).
    """
    try:
        text = items_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        console.print(f"[red]--items-file not found:[/red] {items_file}")
        raise typer.Exit(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        typer.echo(
            f"Invalid JSON in --items-file ({items_file}): line {e.lineno}, "
            f"column {e.colno}: {e.msg}",
            err=True,
        )
        raise typer.Exit(1)
    if isinstance(payload, dict) and "items" in payload:
        items = list(payload.get("items") or [])
    elif isinstance(payload, list):
        items = list(payload)
    else:
        console.print("[red]--items-file must be a JSON list or {items: [...]}"
                      "[/red]")
        raise typer.Exit(1)
    with ReportArchiveClient() as client:
        try:
            row = client.update_composite(
                composite_id, items=items, expected_revision=expected_revision,
            )
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]composite items-set failed ({e.status_code}):"
                          f"[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


@composites_app.command("delete")
def composites_delete(
    composite_id: int = typer.Argument(..., help="composite id to delete"),
    confirm: bool = typer.Option(False, "--yes", "-y",
                                  help="confirm destructive operation"),
):
    """DELETE /composites/{id} — owner / sys admin only."""
    if not confirm:
        console.print("[red]Pass --yes to confirm deletion[/red]")
        raise typer.Exit(1)
    with ReportArchiveClient() as client:
        try:
            client.delete_composite(composite_id)
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]composite delete failed ({e.status_code}):"
                          f"[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps({"deleted": True, "id": composite_id},
                                  ensure_ascii=False))


@composites_app.command("publish")
def composites_publish(
    composite_id: int = typer.Argument(..., help="composite id to publish"),
):
    """POST /composites/{id}/publish — owner only. Freezes recurring
    composite items into snapshots. Idempotent."""
    with ReportArchiveClient() as client:
        try:
            row = client.publish_composite(composite_id)
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]composite publish failed ({e.status_code}):"
                          f"[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


@composites_app.command("unpublish")
def composites_unpublish(
    composite_id: int = typer.Argument(..., help="composite id to unpublish"),
):
    """POST /composites/{id}/unpublish — owner only. Clears snapshots,
    returns composite to live + editable mode. Idempotent."""
    with ReportArchiveClient() as client:
        try:
            row = client.unpublish_composite(composite_id)
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]composite unpublish failed ({e.status_code}):"
                          f"[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


@composites_app.command("accept")
def composites_accept(
    composite_id: int = typer.Option(..., "--composite-id"),
    request_id: int = typer.Option(..., "--request-id"),
):
    """Accept a composite submission request. POST /composites/{id}/requests/{rid}/accept."""
    with ReportArchiveClient() as client:
        try:
            row = client.accept_composite_request(composite_id, request_id)
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]accept failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


@composites_app.command("reject")
def composites_reject(
    composite_id: int = typer.Option(..., "--composite-id"),
    request_id: int = typer.Option(..., "--request-id"),
    reason: Optional[str] = typer.Option(None, "--reason",
                                         help="optional reject reason (backend may ignore)"),
):
    """Reject a composite submission request. POST /composites/{id}/requests/{rid}/reject."""
    with ReportArchiveClient() as client:
        try:
            row = client.reject_composite_request(composite_id, request_id, reason=reason)
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]reject failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


@composites_app.command("withdraw")
def composites_withdraw(
    composite_id: int = typer.Option(..., "--composite-id"),
    request_id: int = typer.Option(..., "--request-id"),
):
    """Withdraw a composite submission request. POST /composites/{id}/requests/{rid}/withdraw."""
    with ReportArchiveClient() as client:
        try:
            row = client.withdraw_composite_request(composite_id, request_id)
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]withdraw failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# v0.5.0 — mounts sub-app: set-folder / set-edit-policy
# --------------------------------------------------------------------------- #
@mounts_app.command("set-folder")
def mounts_set_folder(
    report_id: int = typer.Option(..., "--report-id"),
    workspace: str = typer.Option(..., "--workspace", "--workspace-slug", help="workspace slug of the mount"),
    folder_id: Optional[int] = typer.Option(None, "--folder-id",
                                            help="target folder id; omit to clear the folder"),
):
    """PUT /mounts/{rid}/{slug}/folder — move the mount into a folder (or clear it)."""
    with ReportArchiveClient() as client:
        try:
            row = client.set_mount_folder(report_id, workspace, folder_id=folder_id)
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]set-folder failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


@mounts_app.command("set-edit-policy")
def mounts_set_edit_policy(
    report_id: int = typer.Option(..., "--report-id"),
    workspace: str = typer.Option(..., "--workspace", "--workspace-slug", help="workspace slug of the mount"),
    policy: str = typer.Option(..., "--policy",
                               help="default | owner_only | coauthor | manager"),
):
    """PUT /mounts/{rid}/{slug}/edit-policy — change the mount's edit policy.

    Policy values (RA p27 adds `manager`):
      default     — 작성자 + 보직장.
      owner_only  — 작성자만.
      coauthor    — 게시판 멤버 전원 편집.
      manager     — 작성자 + 게시판 매니저 (auto-syncs workspace_manager grant)."""
    if policy not in ("default", "owner_only", "coauthor", "manager"):
        console.print(f"[red]invalid policy '{policy}' "
                      "(expected: default | owner_only | coauthor | manager)[/red]")
        raise typer.Exit(1)
    with ReportArchiveClient() as client:
        try:
            row = client.set_mount_edit_policy(report_id, workspace, edit_policy=policy)
        except AuthorLockedError as e:
            console.print(f"[red][author_locked][/red] reason: {e.reason}  "
                          f"report_id={e.report_id}")
            raise typer.Exit(4)
        except _TYPED_LOCK_ERRORS as e:
            console.print(f"[red][{type(e).__name__}][/red] {e}")
            raise typer.Exit(4)
        except ApiError as e:
            console.print(f"[red]set-edit-policy failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# v0.6.0 — notifications sub-app: list / unread-count / mark-read / mark-all-read
# --------------------------------------------------------------------------- #
@notifications_app.command("list")
def notifications_list_cmd(
    unread_only: bool = typer.Option(False, "--unread-only",
                                      help="filter to unread items only"),
    limit: int = typer.Option(50, "--limit", min=1, max=200,
                              help="page size (max 200)"),
    before_id: Optional[int] = typer.Option(
        None, "--before-id",
        help="cursor — pass the smallest id from the previous page",
    ),
):
    """GET /notifications — list the caller's inbox.

    Returns `{items, unread_count}` so reactive agents can render badge +
    list in one shot.
    """
    with ReportArchiveClient() as client:
        try:
            body = client.list_notifications(
                unread_only=unread_only, limit=limit, before_id=before_id,
            )
        except ApiError as e:
            console.print(f"[red]notifications list failed ({e.status_code}):"
                          f"[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(body, ensure_ascii=False))


@notifications_app.command("unread-count")
def notifications_unread_count_cmd():
    """GET /notifications/unread-count — single integer badge count."""
    with ReportArchiveClient() as client:
        try:
            n = client.unread_notification_count()
        except ApiError as e:
            console.print(f"[red]unread-count failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps({"unread_count": int(n)}, ensure_ascii=False))


@notifications_app.command("mark-read")
def notifications_mark_read_cmd(
    notification_id: int = typer.Argument(..., help="notification id to mark read"),
):
    """PATCH /notifications/{id}/read — mark a single notification read.
    Idempotent."""
    with ReportArchiveClient() as client:
        try:
            row = client.mark_notification_read(notification_id)
        except ApiError as e:
            console.print(f"[red]mark-read failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


@notifications_app.command("mark-all-read")
def notifications_mark_all_read_cmd():
    """POST /notifications/mark-all-read — flip every unread row to read.
    Returns the count of rows affected."""
    with ReportArchiveClient() as client:
        try:
            n = client.mark_all_notifications_read()
        except ApiError as e:
            console.print(f"[red]mark-all-read failed ({e.status_code}):"
                          f"[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps({"marked_read": int(n)}, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# v0.8.0 — unified grants / sharing CLI
# --------------------------------------------------------------------------- #
@shares_app.command("content-list")
def shares_content_list(
    content_type: str = typer.Argument(..., help="reports | composites"),
    content_id: int = typer.Argument(..., help="report or composite id"),
):
    """GET /api/{content_type}/{id}/shares — list grants."""
    with ReportArchiveClient() as client:
        try:
            rows = client.list_content_shares(content_type, content_id)
        except ApiError as e:
            console.print(f"[red]content-list failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(rows, ensure_ascii=False))


@shares_app.command("content-add")
def shares_content_add(
    content_type: str = typer.Argument(..., help="reports | composites"),
    content_id: int = typer.Argument(..., help="report or composite id"),
    principal_type: str = typer.Option(..., "--principal-type",
        help="workspace | workspace_manager | all_org | user"),
    principal_ref: Optional[str] = typer.Option(None, "--principal-ref",
        help="workspace slug, user id, or omit for all_org"),
    level: str = typer.Option("view", "--level", help="view | edit"),
):
    """POST /api/{content_type}/{id}/shares — owner / sys admin only."""
    with ReportArchiveClient() as client:
        try:
            row = client.add_content_share(content_type, content_id,
                principal_type=principal_type, principal_ref=principal_ref, level=level)
        except ShareSetupForbiddenError as e:
            console.print(f"[red][share_setup_forbidden][/red] {e}  "
                          "(only the content owner / sys admin may add/remove shares)")
            raise typer.Exit(3)
        except ApiError as e:
            console.print(f"[red]content-add failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


@shares_app.command("content-remove")
def shares_content_remove(
    content_type: str = typer.Argument(..., help="reports | composites"),
    content_id: int = typer.Argument(..., help="report or composite id"),
    grant_id: int = typer.Argument(..., help="grant id to delete"),
):
    """DELETE /api/{content_type}/{id}/shares/{grant_id}."""
    with ReportArchiveClient() as client:
        try:
            client.remove_content_share(content_type, content_id, grant_id)
        except ShareSetupForbiddenError as e:
            console.print(f"[red][share_setup_forbidden][/red] {e}  "
                          "(only the content owner / sys admin may add/remove shares)")
            raise typer.Exit(3)
        except ApiError as e:
            console.print(f"[red]content-remove failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps({"ok": True}, ensure_ascii=False))


@shares_app.command("folder-list")
def shares_folder_list(folder_id: int = typer.Argument(..., help="org folder id")):
    """GET /api/folders/{id}/shares — list grants."""
    with ReportArchiveClient() as client:
        try:
            rows = client.list_folder_shares(folder_id)
        except ApiError as e:
            console.print(f"[red]folder-list failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(rows, ensure_ascii=False))


@shares_app.command("folder-add")
def shares_folder_add(
    folder_id: int = typer.Argument(..., help="org folder id"),
    principal_type: str = typer.Option(..., "--principal-type"),
    principal_ref: Optional[str] = typer.Option(None, "--principal-ref"),
    level: str = typer.Option("view", "--level"),
):
    """POST /api/folders/{id}/shares — board manager / sys admin only."""
    with ReportArchiveClient() as client:
        try:
            row = client.add_folder_share(folder_id,
                principal_type=principal_type, principal_ref=principal_ref, level=level)
        except BoardShareForbiddenError as e:
            console.print(f"[red][board_share_forbidden][/red] {e}  "
                          "(only the target board's manager / sys admin may add/remove folder grants)")
            raise typer.Exit(3)
        except ApiError as e:
            console.print(f"[red]folder-add failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


@shares_app.command("folder-remove")
def shares_folder_remove(
    folder_id: int = typer.Argument(..., help="org folder id"),
    grant_id: int = typer.Argument(..., help="grant id"),
):
    """DELETE /api/folders/{id}/shares/{grant_id}."""
    with ReportArchiveClient() as client:
        try:
            client.remove_folder_share(folder_id, grant_id)
        except BoardShareForbiddenError as e:
            console.print(f"[red][board_share_forbidden][/red] {e}  "
                          "(only the target board's manager / sys admin may add/remove folder grants)")
            raise typer.Exit(3)
        except ApiError as e:
            console.print(f"[red]folder-remove failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps({"ok": True}, ensure_ascii=False))


@shares_app.command("board-list")
def shares_board_list(workspace_slug: str = typer.Argument(..., help="board workspace slug")):
    """GET /api/workspaces/{slug}/shares — list grants."""
    with ReportArchiveClient() as client:
        try:
            rows = client.list_board_shares(workspace_slug)
        except ApiError as e:
            console.print(f"[red]board-list failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(rows, ensure_ascii=False))


@shares_app.command("board-add")
def shares_board_add(
    workspace_slug: str = typer.Argument(..., help="board slug"),
    principal_type: str = typer.Option(..., "--principal-type"),
    principal_ref: Optional[str] = typer.Option(None, "--principal-ref"),
    level: str = typer.Option("view", "--level"),
):
    """POST /api/workspaces/{slug}/shares — board manager / sys admin only."""
    with ReportArchiveClient() as client:
        try:
            row = client.add_board_share(workspace_slug,
                principal_type=principal_type, principal_ref=principal_ref, level=level)
        except BoardShareForbiddenError as e:
            console.print(f"[red][board_share_forbidden][/red] {e}  "
                          "(only this board's manager / sys admin may add/remove board grants)")
            raise typer.Exit(3)
        except ApiError as e:
            console.print(f"[red]board-add failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps(row, ensure_ascii=False))


@shares_app.command("board-remove")
def shares_board_remove(
    workspace_slug: str = typer.Argument(..., help="board slug"),
    grant_id: int = typer.Argument(..., help="grant id"),
):
    """DELETE /api/workspaces/{slug}/shares/{grant_id}."""
    with ReportArchiveClient() as client:
        try:
            client.remove_board_share(workspace_slug, grant_id)
        except BoardShareForbiddenError as e:
            console.print(f"[red][board_share_forbidden][/red] {e}  "
                          "(only this board's manager / sys admin may add/remove board grants)")
            raise typer.Exit(3)
        except ApiError as e:
            console.print(f"[red]board-remove failed ({e.status_code}):[/red] {e}")
            raise typer.Exit(2)
    console.print_json(json.dumps({"ok": True}, ensure_ascii=False))


if __name__ == "__main__":
    app()
