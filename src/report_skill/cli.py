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
from report_skill.client import ApiError, ReportArchiveClient
from report_skill.config import settings

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="External skill layer over ReportArchive.")
catalog_app = typer.Typer(no_args_is_help=True, help="Widget catalog cache + diff.")
report_app = typer.Typer(no_args_is_help=True, help="Build / submit reports.")
tier_app = typer.Typer(no_args_is_help=True, help="Adaptive AI tier (S/M/W).")
bridge_app = typer.Typer(no_args_is_help=True, help="LLM bridge (Claude Code as the LLM via files).")
app.add_typer(catalog_app, name="catalog")
app.add_typer(report_app, name="report")
app.add_typer(tier_app, name="tier")
app.add_typer(bridge_app, name="bridge")
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
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    with ReportArchiveClient() as client:
        try:
            created = client.create_report(payload)
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
    return json.loads(path.read_text(encoding="utf-8"))


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


@report_app.command("import")
def report_import(
    payload_path: Path = typer.Argument(..., help="path to a saved ReportCreate JSON payload"),
):
    """Import a previously-exported report payload (POST to /api/reports)."""
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
                title=title or draft.get("title"),
                phase=phase or draft.get("phase"),
                lifecycle=lifecycle or draft.get("lifecycle"),
                status=status or draft.get("status"),
                tags=draft.get("tags"),
            )
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
            updated = report_ops.add_page(
                client, report_id,
                template_id=tpl["template_id"],
                template_version=tpl["version"],
                name=name,
                content=result.content,
                extra_blocks=result.extra_blocks,
            )
        except ApiError as e:
            console.print(f"[red]PATCH /reports/{report_id} failed ({e.status_code}):[/red] {e}")
            console.print_json(json.dumps(e.payload, ensure_ascii=False))
            raise typer.Exit(3)

    pages = updated.get("pages") or []
    console.print(f"[green]page added[/green]  report id={updated.get('id')}  "
                  f"pages now: {len(pages)}")
    console.print(f"  view: http://localhost:3001/reports/{updated.get('id')}")


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


@report_app.command("mount")
def report_mount(
    report_id: int = typer.Argument(...),
    workspace: list[str] = typer.Option(..., "--workspace", "-w",
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
    workspace: str = typer.Option(..., "--workspace", "-w",
                                  help="workspace slug to remove the mount from"),
):
    """Remove a report's mount from one workspace board."""
    with ReportArchiveClient() as client:
        try:
            report_ops.unmount_report(client, report_id, workspace)
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


if __name__ == "__main__":
    app()
