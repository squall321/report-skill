"""`report-skill llm ...` — LLM driver layer.

Three subcommands:

  * `probe`        — sanity-check the active provider with a tiny prompt.
  * `probe-tier`   — run the S/M/W calibration probe and persist the tier.
  * `generate-block <widget_type>` — generate ONE block's content from
    natural-language notes and validate it. Mostly for testing.

The typer app is exported as the module-level `app` symbol so the main
CLI can mount it via `app.add_typer(cli_llm.app, name="llm")`.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from report_skill import examples as examples_mod
from report_skill import llm, prompt, schemas, tier, validate

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="LLM driver — provider probes, tier calibration, ad-hoc block generation.",
)

console = Console()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _load_snapshot_or_exit() -> dict:
    try:
        return schemas.load()
    except schemas.SnapshotMissing as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


def _provider_label(p: llm.LLMProvider) -> str:
    return f"{p.name}/{p.model}"


def _word_count(text: str) -> int:
    return len(text.split())


# --------------------------------------------------------------------------- #
# probe
# --------------------------------------------------------------------------- #
@app.command("probe")
def cmd_probe(
    json_mode: bool = typer.Option(
        True, "--json-mode/--no-json-mode",
        help="Ask for JSON mode when the provider supports it.",
    ),
    timeout: float = typer.Option(60.0, "--timeout", help="Per-call timeout seconds."),
):
    """Hit the active provider with a tiny prompt and report latency.

    Exit codes:
      0  — got a non-empty reply
      1  — no reply / network error / parse error
    """
    provider = llm.get_provider()
    console.print(f"[bold]provider:[/bold] {_provider_label(provider)}  "
                  f"[dim]is_configured={llm.is_configured()}[/dim]")

    messages = [
        {"role": "system",
         "content": "You output ONLY valid JSON, no prose."},
        {"role": "user",
         "content": 'Return exactly this JSON: {"ok": 1}'},
    ]
    started = time.perf_counter()
    try:
        reply = provider.generate(messages, max_tokens=64, temperature=0.0,
                                   json_mode=json_mode)
    except llm.LLMError as e:
        elapsed = time.perf_counter() - started
        console.print(f"[red]LLM error after {elapsed*1000:.0f}ms:[/red] {e}")
        raise typer.Exit(1)
    elapsed = time.perf_counter() - started

    parsed: Optional[object] = None
    parse_err: Optional[str] = None
    try:
        parsed = llm.extract_json(reply)
    except llm.LLMError as e:
        parse_err = str(e)

    console.print(f"[green]reply received[/green]  "
                  f"latency={elapsed*1000:.0f}ms  "
                  f"chars={len(reply)}  words={_word_count(reply)}")
    console.print(Panel(reply, title="raw reply", border_style="dim"))
    if parsed is not None:
        console.print(f"[green]JSON parsed OK[/green]  -> {parsed!r}")
    else:
        console.print(f"[yellow]could not parse JSON:[/yellow] {parse_err}")


# --------------------------------------------------------------------------- #
# probe-tier
# --------------------------------------------------------------------------- #
_TIER_PROBE_EASY_SCHEMA = {
    "type": "object",
    "properties": {
        "a": {"type": "integer"},
        "b": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["a", "b"],
    "additionalProperties": False,
}

_TIER_PROBE_HARD_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "minLength": 1, "maxLength": 20},
        "severity": {"type": "string", "enum": ["낮음", "보통", "높음"]},
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
    },
    "required": ["id", "severity", "score"],
    "additionalProperties": False,
}

_TIER_PROBE_EASY_PROMPT = (
    'Return ONLY a JSON object exactly matching this shape: '
    '{"a": 1, "b": ["x"]}. Do not change the values.'
)

_TIER_PROBE_HARD_PROMPT = (
    "Return ONLY a JSON object with these three fields:\n"
    "  - id: a short string (max 20 chars)\n"
    '  - severity: one of "낮음", "보통", "높음" — pick "보통"\n'
    "  - score: an integer between 0 and 100 — pick 42\n"
    "No other fields are allowed."
)


def _run_tier_probe(provider: llm.LLMProvider, user: str,
                    schema: dict) -> tuple[bool, list[str], object | None, str]:
    """Run a single calibration call. Returns (valid, errors, parsed, raw)."""
    messages = [
        {"role": "system", "content": prompt.SYSTEM_ROLE},
        {"role": "user", "content": user},
    ]
    raw = provider.generate(messages, max_tokens=256, temperature=0.0,
                            json_mode=True)
    try:
        parsed = llm.extract_json(raw)
    except llm.LLMError as e:
        return (False, [f"parse: {e}"], None, raw)
    issues = validate.validate(parsed, schema)
    return (not issues, [str(i) for i in issues], parsed, raw)


@app.command("probe-tier")
def cmd_probe_tier():
    """Calibrate the AI tier (S/M/W) using two structured-output probes.

    Scoring matches `tier.py`'s docstring:
      - both valid + both schema-correct          -> S
      - easy valid, hard has near-miss enum issue -> M
      - easy valid, hard wildly off OR JSON fail  -> W
      - easy fails                                 -> W

    Exit codes:
      0 — tier persisted to .skill-cache/tier.json
      1 — provider unreachable (LLMError raised)
    """
    provider = llm.get_provider()
    console.print(f"[bold]calibrating[/bold] against {_provider_label(provider)}")
    try:
        easy_ok, easy_errs, easy_parsed, easy_raw = _run_tier_probe(
            provider, _TIER_PROBE_EASY_PROMPT, _TIER_PROBE_EASY_SCHEMA)
        hard_ok, hard_errs, hard_parsed, hard_raw = _run_tier_probe(
            provider, _TIER_PROBE_HARD_PROMPT, _TIER_PROBE_HARD_SCHEMA)
    except llm.LLMError as e:
        console.print(f"[red]LLM error during probe:[/red] {e}")
        raise typer.Exit(1)

    def _summary(label: str, ok: bool, errs: list[str], raw: str) -> None:
        flag = "[green]ok[/green]" if ok else "[red]fail[/red]"
        console.print(f"  {label}: {flag}  raw_chars={len(raw)}")
        for e in errs:
            console.print(f"    [yellow]-[/yellow] {e}")
    _summary("easy", easy_ok, easy_errs, easy_raw)
    _summary("hard", hard_ok, hard_errs, hard_raw)

    # Scoring -----------------------------------------------------------
    if not easy_ok:
        chosen, reason = "W", "easy probe failed schema or JSON parse"
    elif hard_ok:
        chosen, reason = "S", "both probes schema-correct"
    else:
        # easy ok, hard not ok — decide between M and W
        near_miss = False
        for e in hard_errs:
            # validate.validate emits messages like "severity: '...' not in enum [...]"
            if "not in enum" in e or "additionalProperties" in e:
                near_miss = True
                break
        if isinstance(hard_parsed, dict) and not near_miss:
            # Parsed JSON but with structural issues (missing field, wrong type)
            near_miss = True
        if near_miss:
            chosen, reason = "M", "easy ok; hard had a near-miss schema issue"
        else:
            chosen, reason = "W", "easy ok; hard was wildly off or unparseable"

    profile = tier.set_tier(chosen, source="probe",
                            notes=f"{_provider_label(provider)} — {reason}")
    console.print(f"\n[bold]chosen tier:[/bold] [cyan]{profile.tier}[/cyan]  "
                  f"([dim]{profile.source}[/dim]) — {profile.notes}")


# --------------------------------------------------------------------------- #
# generate-block
# --------------------------------------------------------------------------- #
@app.command("generate-block")
def cmd_generate_block(
    widget_type: str = typer.Argument(..., help="Widget type, e.g. `bulleted_list`."),
    input_path: Path = typer.Option(..., "--input", "-i",
                                    help="Path to a text file with the natural-language notes."),
    props: str = typer.Option("{}", "--props",
                              help="JSON string with the block's props (merged onto widget defaults)."),
    block_id: str = typer.Option("adhoc", "--block-id",
                                 help="Synthetic block id used in the prompt."),
    max_tokens: int = typer.Option(1024, "--max-tokens"),
    temperature: float = typer.Option(0.1, "--temperature"),
    show_prompt: bool = typer.Option(True, "--show-prompt/--no-show-prompt",
                                     help="Echo the assembled prompt before calling the LLM."),
):
    """Generate ONE block's content for ad-hoc testing.

    Walks the same path the orchestrator will use in P4:
      1. resolve widget content_schema from the cached snapshot
      2. load the hand-validated example, if any
      3. build a single-block prompt
      4. call the provider, parse JSON, validate against schema
      5. on validation failure, retry up to `policy().max_llm_retries`
         times feeding back the humanized errors

    Exit codes:
      0 — final output validates against the schema
      1 — snapshot missing / unknown widget type / unreadable input
      2 — exhausted retries; last output still fails validation
      3 — LLMError (network/auth) — surfaced from provider.generate()
    """
    snapshot = _load_snapshot_or_exit()
    widget = snapshot.get("widgets", {}).get(widget_type)
    if widget is None:
        console.print(f"[red]unknown widget_type[/red] '{widget_type}'")
        raise typer.Exit(1)

    if not input_path.is_file():
        console.print(f"[red]input file not found:[/red] {input_path}")
        raise typer.Exit(1)
    user_input = input_path.read_text(encoding="utf-8")

    try:
        props_dict = json.loads(props or "{}")
        if not isinstance(props_dict, dict):
            raise ValueError("must be a JSON object")
    except (json.JSONDecodeError, ValueError) as e:
        console.print(f"[red]--props must be JSON object:[/red] {e}")
        raise typer.Exit(1)

    effective_props = dict(widget.get("default_props") or {})
    effective_props.update(props_dict)

    spec = prompt.BlockSpec(
        block_id=block_id,
        widget_type=widget_type,
        props=effective_props,
        content_schema=widget.get("content_schema"),
        widget_label=widget.get("label", ""),
        widget_description=widget.get("description", ""),
    )

    # Load hand-validated example if present (and not stale).
    try:
        example = examples_mod.load_example(widget_type)
    except json.JSONDecodeError:
        example = None
    if isinstance(example, dict):
        ex_hash = str(example.get("for_schema_hash", ""))
        cache_hash = str(widget.get("hash", ""))
        if ex_hash != cache_hash:
            console.print(f"[yellow]warning:[/yellow] example for '{widget_type}' is "
                          f"stale (cache={cache_hash}, example={ex_hash}) — "
                          "using it anyway for shape hints only")

    provider = llm.get_provider()
    policy = tier.policy()
    max_attempts = max(1, policy.max_llm_retries + 1)
    console.print(f"[bold]provider:[/bold] {_provider_label(provider)}  "
                  f"[bold]tier:[/bold] {tier.current_tier().tier}  "
                  f"[bold]max_attempts:[/bold] {max_attempts}")

    prior_attempt: Optional[dict] = None
    prior_errors: Optional[list[str]] = None
    final_content: Optional[object] = None
    final_issues: list[validate.ValidationIssue] = []

    for attempt in range(1, max_attempts + 1):
        messages = prompt.build_single_block_prompt(
            spec,
            user_input=user_input,
            example=example,
            prior_attempt=prior_attempt,
            prior_errors=prior_errors,
        )
        if show_prompt and attempt == 1:
            console.print(Panel(messages[-1]["content"],
                                title="prompt (user msg)",
                                border_style="dim"))

        try:
            raw = provider.generate(messages, max_tokens=max_tokens,
                                     temperature=temperature, json_mode=True)
        except llm.LLMError as e:
            console.print(f"[red]LLM error (attempt {attempt}):[/red] {e}")
            raise typer.Exit(3)

        try:
            parsed = llm.extract_json(raw)
        except llm.LLMError as e:
            console.print(f"[red]attempt {attempt}: could not parse JSON:[/red] {e}")
            prior_attempt = {"_raw": raw[:500]}
            prior_errors = [f"output was not valid JSON: {e}"]
            continue

        issues = validate.validate(parsed, spec.content_schema or {})
        if not issues:
            final_content = parsed
            final_issues = []
            console.print(f"[green]attempt {attempt}: validated OK[/green]")
            break

        final_content = parsed
        final_issues = issues
        prior_attempt = parsed if isinstance(parsed, dict) else {"_value": parsed}
        prior_errors = [str(i) for i in issues]
        console.print(f"[yellow]attempt {attempt}: {len(issues)} validation issue(s)[/yellow]")
        for i in issues[:8]:
            console.print(f"  [yellow]-[/yellow] {i}")
        if attempt == max_attempts:
            console.print("[red]exhausted retries[/red]")

    payload = json.dumps(final_content, ensure_ascii=False, indent=2)
    console.print(Panel(Syntax(payload, "json", theme="ansi_dark", word_wrap=True),
                        title="final content",
                        border_style="green" if not final_issues else "red"))

    if final_issues:
        raise typer.Exit(2)


if __name__ == "__main__":
    app()
