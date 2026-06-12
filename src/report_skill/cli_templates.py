"""`report-skill templates ...` — list + inspect ReportArchive templates.

The `.claude/skills/report-write.md` flow references a `templates list`
subcommand for picking which template to publish into; this module
implements it on top of `ReportArchiveClient.fetch_templates`.

Two subcommands:

  * `list` — pretty table of all templates (filter by --category)
  * `show` — inspect one template's blocks (block_id, widget type, key props)

The typer app is exported as the module-level `app` symbol so the main CLI
can mount it via `app.add_typer(cli_templates.app, name="templates")`.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

from report_skill.client import ApiError, ReportArchiveClient

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="List + inspect ReportArchive templates.",
)

console = Console()


# --------------------------------------------------------------------------- #
# v0.16.0 — offline fallback (fresh-machine audit M2)
# `templates list/show` previously REQUIRED a live, configured server even
# though the wheel/exe ship a bundled template baseline. Now: try live, and
# on any config/network failure fall back to cached + bundled templates so a
# brand-new machine can explore templates before .env even exists.
# --------------------------------------------------------------------------- #
def _offline_templates() -> list[dict]:
    """Every template readable without a server: live cache first, then the
    bundled baseline (catalog.load_cached_template's own fallback order)."""
    from report_skill import catalog

    seen: dict[str, dict] = {}
    for tid in catalog.list_cached_templates():
        tpl = catalog.load_cached_template(tid)
        if tpl:
            seen[str(tpl.get("template_id") or tid)] = tpl
    bundled_dir = getattr(catalog, "BUNDLED_TEMPLATES_DIR", None)
    if bundled_dir is not None and bundled_dir.is_dir():
        for p in sorted(bundled_dir.glob("*.latest.json")):
            tid = p.stem[: -len(".latest")]
            if tid not in seen:
                try:
                    seen[tid] = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
    return list(seen.values())


def _offline_template(template_id: str, version: Optional[int]) -> Optional[dict]:
    from report_skill import catalog
    return catalog.load_cached_template(template_id, version)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _blocks_of(tpl: dict) -> list[dict]:
    """Return the template's block list (tolerant of both flat + nested shapes)."""
    schema = tpl.get("schema") or {}
    blocks = schema.get("blocks")
    if isinstance(blocks, list):
        return blocks
    # Some payloads expose `.blocks` at the top level instead — handle both.
    if isinstance(tpl.get("blocks"), list):
        return tpl["blocks"]
    return []


def _widget_types(tpl: dict) -> list[str]:
    return [str(b.get("type", "?")) for b in _blocks_of(tpl) if b.get("type")]


def _format_props(props: Any, *, max_len: int = 80) -> str:
    """Compact one-line preview of a block's props dict."""
    if not props:
        return ""
    try:
        s = json.dumps(props, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        s = str(props)
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s


# --------------------------------------------------------------------------- #
# list
# --------------------------------------------------------------------------- #
@app.command("list")
def cmd_list(
    category: Optional[str] = typer.Option(
        None, "--category", "-c",
        help="filter by template category (case-insensitive exact match).",
    ),
    json_out: bool = typer.Option(
        False, "--json",
        help="emit the raw list as JSON instead of a table.",
    ),
):
    """Pretty-print all templates. Optional --category filter.

    Works offline: when the server/config is unavailable, lists the cached +
    bundled baseline instead of failing."""
    try:
        with ReportArchiveClient() as client:
            templates = client.fetch_templates()
    except Exception as e:  # noqa: BLE001 — ApiError/ConfigError/network
        templates = _offline_templates()
        if not templates:
            console.print(f"[red]templates fetch failed:[/red] {e}")
            raise typer.Exit(2 if isinstance(e, ApiError) else 1)
        console.print(f"[yellow]server unavailable ({type(e).__name__}) — "
                      f"showing cached/bundled templates[/yellow]")

    if category:
        cat_lc = category.lower()
        templates = [t for t in templates if str(t.get("category", "")).lower() == cat_lc]

    if not templates:
        console.print("[yellow]no templates matched[/yellow]")
        raise typer.Exit(1)

    if json_out:
        console.print_json(json.dumps(templates, ensure_ascii=False, indent=2))
        return

    tbl = Table(title=f"templates ({len(templates)})", show_lines=False)
    tbl.add_column("template_id", style="cyan", no_wrap=True)
    tbl.add_column("name")
    tbl.add_column("category", style="magenta")
    tbl.add_column("blocks", justify="right")
    tbl.add_column("widget types", style="dim")

    for t in templates:
        tid = str(t.get("id") or t.get("template_id") or "")
        name = str(t.get("name") or "")
        cat = str(t.get("category") or "")
        widgets = _widget_types(t)
        preview = ", ".join(sorted(set(widgets))[:5])
        if len(set(widgets)) > 5:
            preview += f" (+{len(set(widgets)) - 5})"
        tbl.add_row(tid, name, cat, str(len(widgets)), preview)
    console.print(tbl)


# --------------------------------------------------------------------------- #
# show
# --------------------------------------------------------------------------- #
@app.command("show")
def cmd_show(
    template_id: str = typer.Argument(..., help="template id to inspect"),
    version: Optional[int] = typer.Option(
        None, "--version", "-v",
        help="specific version number (defaults to latest).",
    ),
    json_out: bool = typer.Option(
        False, "--json",
        help="emit the raw template JSON instead of pretty-printing.",
    ),
):
    """Print one template's blocks with each block's id + widget type + key props.

    Works offline: falls back to the cached/bundled copy when the server or
    config is unavailable."""
    try:
        with ReportArchiveClient() as client:
            tpl = client.fetch_template(template_id, version)
    except Exception as e:  # noqa: BLE001 — ApiError/ConfigError/network
        tpl = _offline_template(template_id, version)
        if tpl is None:
            console.print(f"[red]template fetch failed:[/red] {e}")
            raise typer.Exit(2 if isinstance(e, ApiError) else 1)
        console.print(f"[yellow]server unavailable ({type(e).__name__}) — "
                      f"showing the cached/bundled copy[/yellow]")

    if json_out:
        console.print_json(json.dumps(tpl, ensure_ascii=False, indent=2))
        return

    tid = tpl.get("id") or tpl.get("template_id") or template_id
    name = tpl.get("name") or ""
    cat = tpl.get("category") or ""
    ver = tpl.get("version")
    header = f"[bold cyan]{tid}[/]  [white]{name}[/]"
    if cat:
        header += f"  [magenta]({cat})[/]"
    if ver is not None:
        header += f"  [dim]v{ver}[/]"
    console.print(header)

    blocks = _blocks_of(tpl)
    if not blocks:
        console.print("[yellow]template has no blocks[/yellow]")
        return

    for b in blocks:
        bid = str(b.get("id", "?"))
        wtype = str(b.get("type", "?"))
        required = b.get("required")
        props = b.get("props") or {}
        bits = [f"[cyan]{bid}[/]", f"([magenta]{wtype}[/])"]
        if required is not None:
            bits.append(f"required={str(bool(required)).lower()}")
        console.print("  " + "  ".join(bits))
        props_preview = _format_props(props)
        if props_preview:
            console.print(f"    [dim]props:[/] {props_preview}")


# --------------------------------------------------------------------------- #
# suggest
# --------------------------------------------------------------------------- #
@app.command("suggest")
def cmd_suggest(
    text: str = typer.Argument(..., help="natural-language description of what the user wants to capture"),
    top_k: int = typer.Option(3, "--top-k", "-k"),
    use_llm: str = typer.Option(
        "auto", "--use-llm",
        help="auto | never | always — when to invoke the LLM tie-breaker",
    ),
    json_out: bool = typer.Option(False, "--json"),
):
    """Recommend the best matching template(s) for raw user text.

    Hybrid scoring: per-template keyword vocab (deterministic) → LLM tie-break
    only when keyword confidence is medium/low AND a provider is configured.
    """
    from report_skill import template_suggest

    try:
        suggestions = template_suggest.suggest_templates(
            text, top_k=top_k, use_llm=use_llm,
        )
    except Exception as e:
        console.print(f"[red]suggest failed:[/red] {e}")
        raise typer.Exit(1)

    if json_out:
        console.print_json(json.dumps([
            {
                "template_id": s.template_id, "template_name": s.template_name,
                "score": s.score, "confidence": s.confidence,
                "matched_keywords": s.matched_keywords,
                "llm_reasoning": s.llm_reasoning,
            } for s in suggestions
        ], ensure_ascii=False, indent=2))
        return

    tbl = Table(title="template suggestions", show_lines=False)
    tbl.add_column("rank", justify="right", style="dim")
    tbl.add_column("template_id", style="cyan")
    tbl.add_column("conf", style="bold")
    tbl.add_column("score", justify="right")
    tbl.add_column("keywords matched", style="dim")
    tbl.add_column("name / reasoning")
    conf_color = {"high": "green", "medium": "yellow", "low": "red"}
    for i, s in enumerate(suggestions, 1):
        if not s.template_id:
            continue
        kws = ", ".join(s.matched_keywords[:5])
        if len(s.matched_keywords) > 5:
            kws += f" (+{len(s.matched_keywords) - 5})"
        name_or_reason = s.llm_reasoning or s.template_name
        tbl.add_row(
            str(i), s.template_id,
            f"[{conf_color.get(s.confidence, 'white')}]{s.confidence}[/]",
            f"{s.score:.2f}", kws, name_or_reason,
        )
    console.print(tbl)


# --------------------------------------------------------------------------- #
# set-scope — manager governance
# --------------------------------------------------------------------------- #
@app.command("set-scope")
def cmd_set_scope(
    template_id: str = typer.Argument(..., help="template slug, e.g. engineering-rca"),
    workspaces: list[str] = typer.Option(
        [], "--workspace", "-w",
        help="workspace slug; repeat for multiple (e.g. -w eng -w qa).",
    ),
    global_: bool = typer.Option(
        False, "--global",
        help="clear scope — template becomes global (전사).",
    ),
):
    """PATCH /templates/{id}/scope — manager-only governance.

    Pass --global to clear all workspace scopes (전사 visible), or pass one or
    more --workspace flags to restrict visibility. The two are mutually
    exclusive in practice: --global wins if both are provided.
    """
    scope: list[str] = [] if global_ else list(workspaces)
    try:
        with ReportArchiveClient() as client:
            result = client.set_template_scope(
                template_id, owner_workspace_slugs=scope,
            )
    except ApiError as e:
        console.print(f"[red]set-scope failed ({e.status_code}):[/red] {e}")
        raise typer.Exit(2)
    except Exception as e:
        console.print(f"[red]set-scope failed:[/red] {e}")
        raise typer.Exit(1)

    console.print_json(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    app()
