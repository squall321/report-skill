"""Block-level prompt builder for the LLM driver.

Three shapes, matching the Tier policy:

  * `build_single_block_prompt`  — W tier (one block at a time)
  * `build_batch_prompt`         — M tier (a few blocks per call)
  * `build_page_prompt`          — S tier (entire page in one shot)

All builders return a list[dict] of {role, content} messages directly
consumable by `llm.LLMProvider.generate()`.

Design rules
------------
* Be terse. Weak local 8B models lose track in long prompts.
* State the role once at the top.
* For each block: widget label/description, a paraphrased data-shape
  description (so the model does not have to read raw JSON-schema), the
  exact JSON-schema constraints (the source of truth), one hand-validated
  example if available, and the user's raw notes verbatim.
* Demand JSON output that matches the content_schema. No prose.
* On retry, surface the prior bad output + the validation errors and
  ask only for a fixed version.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# Public dataclass
# --------------------------------------------------------------------------- #
@dataclass
class BlockSpec:
    block_id: str
    widget_type: str
    props: dict                # effective props (defaults + overrides)
    content_schema: dict | None
    widget_label: str = ""     # human label, e.g. "키-값"
    widget_description: str = ""   # widget catalog description


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
SYSTEM_ROLE = (
    "You are a content extractor that turns informal Korean/English notes "
    "into structured report blocks. You output ONLY valid JSON matching the "
    "schema given for each block — no prose, no markdown fences, no comments."
)


# --------------------------------------------------------------------------- #
# Per-widget input shorthand hints — appended to the block spec by
# `_render_block_spec` when the widget type benefits from extra guidance.
# Only widgets whose adapter accepts a richer syntax than raw JSON earn
# an entry; everything else is driven by the content_schema alone.
# --------------------------------------------------------------------------- #
_WIDGET_INPUT_HINTS: dict[str, str] = {
    "rich_text": (
        "Input shorthand accepted: plain prose with optional markdown emphasis "
        "(**bold**, *em*, ~~strike~~, __underline__). To cross-reference another "
        "report, a department/workspace, or a tagged entity, use the markdown-link "
        "form with the `mention://` scheme:\n"
        "  - `[표시 문구](mention://report/<int_id>?ws=<workspace_slug>)`\n"
        "  - `[표시 문구](mention://dept/<workspace_slug>)`  (the slug IS the id; no `?ws=`)\n"
        "  - `[표시 문구](mention://entity/<int_id>?axis=<type_slug>)`\n"
        "The id MUST come from a prior `reports_search` / `workspaces_list` / "
        "`entity_types_list` / `entities_list` MCP tool call — NEVER invent ids. "
        "If a reference cannot be unambiguously resolved, write the plain label "
        "without the link form.\n"
        "Examples:\n"
        "  본 보고서는 [2026-W22 백엔드 주간보고](mention://report/137?ws=backend) 의 후속이다.\n"
        "  리뷰는 [DX팀](mention://dept/dx) 과 진행했다.\n"
        "  검증 대상: [HFP-X1](mention://entity/412?axis=model_name) 모델.\n"
        "Reserve mention chips for GENUINE cross-references — linkifying every "
        "team or product name is a known AI tell (see SKILL.md style note)."
    ),
    "table": (
        "Optional dict-input fields the adapter passes through alongside `rows`:\n"
        "  - `note`: footnote shown under the table (max 1000 chars). The "
        "renderer prepends `※` automatically — do NOT include it yourself.\n"
        "  - `column_widths`: {column_key: px_int} per-column width hints.\n"
        "  - `table_width_px`: total table width in pixels (int).\n"
        "  - `merges`: list of `{r, c, rs, cs}` cell-span objects "
        "(row/col index plus row-span/col-span)."
    ),
    "image": (
        "Optional dict-input field beyond the existing files/caption/aspect_ratio:\n"
        "  - `note`: caption-line footnote (max 1000 chars). Renderer prepends "
        "`※` automatically — do NOT include it yourself."
    ),
    "comparison": (
        "Optional dict-input fields the adapter passes through alongside `rows`:\n"
        "  - `note`: footnote (max 1000 chars, no leading `※` — renderer adds it).\n"
        "  - `column_widths` / `table_width_px` / `merges`: same shape as table.\n"
        "  - `row_label_width`: pixel width of the left label column (int)."
    ),
}


# --------------------------------------------------------------------------- #
# Schema → terse English paraphrase
# --------------------------------------------------------------------------- #
def describe_schema(schema: Any, depth: int = 0, *, max_depth: int = 4) -> str:
    """Render a JSON-schema as a short bullet-style English description.

    Output stays small enough for an 8B local model to follow.
    """
    if depth > max_depth or not isinstance(schema, dict):
        return "any"

    if "const" in schema:
        return f"const {schema['const']!r}"
    enum = schema.get("enum")
    if isinstance(enum, list):
        return f"one of {enum}"

    stype = schema.get("type")
    if isinstance(stype, list):
        stype = next((t for t in stype if t != "null"), stype[0])

    if stype == "object" or (stype is None and "properties" in schema):
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        parts: list[str] = []
        for key, sub in props.items():
            req = " (required)" if key in required else ""
            sub_desc = describe_schema(sub, depth + 1, max_depth=max_depth)
            parts.append(f"  - {key}{req}: {sub_desc}")
        body = "\n".join(parts) if parts else "  (no defined properties)"
        return "object with fields:\n" + body

    if stype == "array":
        items = schema.get("items") or {}
        min_i = schema.get("minItems")
        max_i = schema.get("maxItems")
        size = ""
        if min_i is not None or max_i is not None:
            size = f" (minItems={min_i}, maxItems={max_i})"
        return f"array of [{describe_schema(items, depth + 1, max_depth=max_depth)}]{size}"

    if stype == "string":
        constraints = []
        if "minLength" in schema:
            constraints.append(f"minLen={schema['minLength']}")
        if "maxLength" in schema:
            constraints.append(f"maxLen={schema['maxLength']}")
        if "pattern" in schema:
            constraints.append(f"pattern={schema['pattern']!r}")
        return "string" + (f" ({', '.join(constraints)})" if constraints else "")

    if stype in ("integer", "number"):
        constraints = []
        if "minimum" in schema:
            constraints.append(f">={schema['minimum']}")
        if "maximum" in schema:
            constraints.append(f"<={schema['maximum']}")
        return stype + (f" ({', '.join(constraints)})" if constraints else "")

    if stype == "boolean":
        return "boolean"

    return stype or "any"


# --------------------------------------------------------------------------- #
# Per-block instruction block (reused by all 3 builders)
# --------------------------------------------------------------------------- #
def _example_content(example: Optional[dict]) -> Optional[Any]:
    if not isinstance(example, dict):
        return None
    return example.get("expected_content")


def _render_block_spec(spec: BlockSpec, example: Optional[dict] = None) -> str:
    label = spec.widget_label or spec.widget_type
    desc = spec.widget_description or ""
    schema_text = describe_schema(spec.content_schema or {})
    schema_json = json.dumps(spec.content_schema or {}, ensure_ascii=False, indent=2)
    props_json = json.dumps(spec.props or {}, ensure_ascii=False)

    parts = [
        f"### Block `{spec.block_id}` — widget `{spec.widget_type}` ({label})",
    ]
    if desc:
        parts.append(f"Purpose: {desc}")
    parts.append(f"Props in effect: {props_json}")
    parts.append("Content shape:\n" + schema_text)
    parts.append("Authoritative JSON-schema:\n" + schema_json)

    hint = _WIDGET_INPUT_HINTS.get(spec.widget_type)
    if hint:
        parts.append("Input shorthand & mention syntax:\n" + hint)

    ex_content = _example_content(example)
    if ex_content is not None:
        parts.append(
            "Example output (for shape only — DO NOT copy values):\n"
            + json.dumps(ex_content, ensure_ascii=False, indent=2)
        )
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def build_single_block_prompt(
    spec: BlockSpec,
    *,
    user_input: str,
    example: dict | None = None,
    prior_attempt: dict | None = None,
    prior_errors: list[str] | None = None,
) -> list[dict]:
    """W-tier prompt: distil notes into a single block's content JSON."""
    sections = [_render_block_spec(spec, example)]
    sections.append("### User notes (verbatim)\n" + (user_input or "").strip())

    if prior_attempt is not None or prior_errors:
        prior_blob = json.dumps(prior_attempt or {}, ensure_ascii=False, indent=2)
        errs = prior_errors or ["(no detail captured)"]
        sections.append(
            "### Previous attempt (REJECTED — fix only these issues)\n"
            f"Errors:\n- " + "\n- ".join(errs)
            + "\nPrevious output was:\n" + prior_blob
            + "\nReturn the corrected full content JSON, not a diff."
        )

    sections.append(
        "### Output\n"
        "Return ONLY the JSON object that satisfies the schema for this "
        "block. No prose, no markdown, no comments."
    )
    user_msg = "\n\n".join(sections)
    return [
        {"role": "system", "content": SYSTEM_ROLE},
        {"role": "user", "content": user_msg},
    ]


def build_block_revise_prompt(
    spec: BlockSpec,
    *,
    current_content: Any,
    revision_instruction: str,
    example: Optional[dict] = None,
    prior_attempt: Optional[dict] = None,
    prior_errors: Optional[list[str]] = None,
) -> list[dict]:
    """Revise-tier prompt: take a block's current state and apply the user's
    natural-language revision, preserving anything not mentioned.

    Returns FULL new content (not a diff) so the result can be validated +
    PATCHed through the same scoped_content path as `report update`.
    """
    sections = [_render_block_spec(spec, example)]
    current_json = json.dumps(current_content, ensure_ascii=False, indent=2)
    sections.append(
        "### Current block content (what is in the report right now)\n"
        + current_json
    )
    sections.append(
        "### User revision request (verbatim)\n"
        + (revision_instruction or "").strip()
    )
    sections.append(
        "### Output\n"
        "Return the FULL NEW JSON for this block (not a diff, not a delta).\n"
        "Rules:\n"
        "- Apply the user's revision exactly as requested.\n"
        "- Preserve every field the user did NOT ask to change.\n"
        "- Preserve every existing mention link (`[label](mention://...)`)\n"
        "  verbatim unless the user's instruction explicitly asks to change,\n"
        "  remove, or re-target that mention.\n"
        "- If the user's request does not apply to THIS block, return the\n"
        "  current content unchanged (verbatim copy).\n"
        "- Output MUST satisfy the schema above.\n"
        "- No prose, no markdown fences, no comments."
    )

    if prior_attempt is not None or prior_errors:
        prior_blob = json.dumps(prior_attempt or {}, ensure_ascii=False, indent=2)
        errs = prior_errors or ["(no detail captured)"]
        sections.append(
            "### Previous attempt (REJECTED — fix only these issues)\n"
            f"Errors:\n- " + "\n- ".join(errs)
            + "\nPrevious output was:\n" + prior_blob
            + "\nReturn the corrected full content JSON, not a diff."
        )

    return [
        {"role": "system", "content": SYSTEM_ROLE},
        {"role": "user", "content": "\n\n".join(sections)},
    ]


def build_batch_prompt(
    specs: list[BlockSpec],
    *,
    user_input: str,
    examples: dict[str, dict] | None = None,
) -> list[dict]:
    """M-tier prompt: distil notes into a small batch of blocks at once.

    Returns one JSON object keyed by block_id whose values each satisfy
    their block's schema.
    """
    examples = examples or {}
    sections = [
        f"You will fill {len(specs)} blocks from a single set of notes.",
        "Each block has its own widget type and schema. Treat them as "
        "independent: a value used in one block's content should be "
        "echoed elsewhere only if the notes warrant it.",
    ]
    for spec in specs:
        sections.append(_render_block_spec(spec, examples.get(spec.widget_type)))

    sections.append("### User notes (verbatim)\n" + (user_input or "").strip())
    block_keys = ", ".join(f'"{s.block_id}"' for s in specs)
    sections.append(
        "### Output\n"
        "Return ONE JSON object whose top-level keys are exactly: "
        f"{block_keys}. Each value MUST satisfy that block's schema."
    )
    return [
        {"role": "system", "content": SYSTEM_ROLE},
        {"role": "user", "content": "\n\n".join(sections)},
    ]


def build_page_prompt(
    template: dict,
    *,
    user_input: str,
    snapshot: dict,
) -> list[dict]:
    """S-tier prompt: fill the entire page in one call.

    Pulls each block's content_schema out of the snapshot and builds
    one big batch prompt. Mirrors `build_batch_prompt`'s output shape.
    """
    blocks_schema = (template.get("schema") or {}).get("blocks") or []
    widgets = snapshot.get("widgets") or {}

    specs: list[BlockSpec] = []
    for b in blocks_schema:
        wtype = b.get("type", "")
        w = widgets.get(wtype, {}) or {}
        specs.append(
            BlockSpec(
                block_id=b.get("id", ""),
                widget_type=wtype,
                props=b.get("props") or {},
                content_schema=w.get("content_schema"),
                widget_label=w.get("label", ""),
                widget_description=w.get("description", ""),
            )
        )

    # Reuse the batch builder for the body, but prepend page metadata.
    base = build_batch_prompt(specs, user_input=user_input)
    page_header = (
        f"Page template: {template.get('id') or template.get('slug') or '(unknown)'}"
        f"  name: {template.get('name') or template.get('title') or ''}\n"
        f"It has {len(specs)} blocks."
    )
    # Inject the header at the top of the user message.
    base[1]["content"] = page_header + "\n\n" + base[1]["content"]
    return base
