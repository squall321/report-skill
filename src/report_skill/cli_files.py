"""`report-skill files ...` — upload local files and grab file_id values.

Mirrors the typer layout used by `cli_examples` / `cli_templates`. Two
subcommands:

  * `upload`     — push a single file, print its file_id (and, optionally,
                   a ready-to-paste block-content JSON snippet).
  * `upload-dir` — push every file in a directory, print a basename→file_id
                   JSON map suitable for templating into a draft.

The typer app is exported as the module-level `app` symbol so the main CLI
can mount it via `app.add_typer(cli_files.app, name="files")`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from report_skill import files as files_mod
from report_skill.client import ApiError, ReportArchiveClient

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Upload files to ReportArchive and inspect file_id values.",
)

console = Console()


# --------------------------------------------------------------------------- #
# upload (single file)
# --------------------------------------------------------------------------- #
@app.command("upload")
def cmd_upload(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True,
                                help="local file to upload"),
    as_block: bool = typer.Option(
        False, "--as-block",
        help="print the {'file_id': ...} JSON object adapters expect, "
             "instead of just the bare id.",
    ),
    json_out: bool = typer.Option(
        False, "--json",
        help="emit the full UploadedFile as JSON (file_id, filename, size, mime_type, widget_type).",
    ),
):
    """Upload a single file. Prints the file_id ready to paste into a draft."""
    try:
        uf = files_mod.upload(path)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    except ApiError as e:
        console.print(f"[red]upload failed ({e.status_code}):[/red] {e}")
        raise typer.Exit(2)
    except Exception as e:
        console.print(f"[red]upload failed:[/red] {e}")
        raise typer.Exit(1)

    widget_type = files_mod.detect_widget_type(path)

    if json_out:
        out = {
            "file_id": uf.file_id,
            "filename": uf.filename,
            "size": uf.size,
            "mime_type": uf.mime_type,
            "widget_type": widget_type,
        }
        console.print_json(json.dumps(out, ensure_ascii=False))
        return

    if as_block:
        console.print_json(json.dumps(uf.as_block_content(), ensure_ascii=False))
        return

    # Default: human-friendly summary + the bare file_id on its own line so
    # piping (`report-skill files upload x | tail -1`) just works.
    console.print(
        f"[green]uploaded[/green]  {uf.filename}  "
        f"[dim]({uf.size} bytes, {uf.mime_type}, widget={widget_type})[/dim]"
    )
    console.print(uf.file_id)


# --------------------------------------------------------------------------- #
# upload-dir (batch)
# --------------------------------------------------------------------------- #
@app.command("upload-dir")
def cmd_upload_dir(
    dir_path: Path = typer.Argument(..., exists=True, file_okay=False, readable=True,
                                    help="directory whose files should be uploaded"),
    recursive: bool = typer.Option(
        False, "--recursive", "-r",
        help="walk subdirectories too (default: top-level only).",
    ),
    pattern: str = typer.Option(
        "*", "--pattern", "-p",
        help="glob pattern applied within the directory (default: '*').",
    ),
    json_out: bool = typer.Option(
        True, "--json/--no-json",
        help="print a JSON map of basename → file_id (default).",
    ),
):
    """Upload every file in a directory. Prints a JSON map of basename → file_id."""
    paths: list[Path]
    if recursive:
        paths = sorted(p for p in dir_path.rglob(pattern) if p.is_file())
    else:
        paths = sorted(p for p in dir_path.glob(pattern) if p.is_file())

    if not paths:
        console.print(f"[yellow]no files matched[/yellow] {dir_path}  pattern={pattern!r}  recursive={recursive}")
        raise typer.Exit(1)

    # Share one client (one login) across the batch.
    out: dict[str, str] = {}
    detail: list[files_mod.UploadedFile] = []
    failed: list[tuple[str, str]] = []
    with ReportArchiveClient() as client:
        for p in paths:
            try:
                uf = files_mod.upload(p, client=client)
            except (ApiError, FileNotFoundError) as e:
                failed.append((str(p), str(e)))
                continue
            # Use basename as the dict key; the user can re-key as they like.
            out[p.name] = uf.file_id
            detail.append(uf)

    if json_out:
        console.print_json(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        tbl = Table(title=f"uploaded ({len(detail)})", show_lines=False)
        tbl.add_column("filename", style="cyan")
        tbl.add_column("file_id", style="dim")
        tbl.add_column("widget", style="magenta")
        tbl.add_column("size", justify="right")
        for uf in detail:
            tbl.add_row(uf.filename, uf.file_id,
                        files_mod.detect_widget_type(uf.filename),
                        str(uf.size))
        console.print(tbl)

    if failed:
        console.print(f"\n[red]{len(failed)} failure(s):[/red]")
        for src, msg in failed:
            console.print(f"  [red]x[/red] {src}: {msg}")
        raise typer.Exit(2)


if __name__ == "__main__":
    app()
