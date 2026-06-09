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
        "team or product name is a known AI tell (see SKILL.md style note).\n"
        "List-shape input: when emitting structured `items: [{depth, text, html, relation}]`, "
        "the per-item `relation` field is a slug (1-32 chars) that the runtime resolves "
        "against the active relations resolver (v0.6.0). Omit `relation` unless a slug is "
        "known — the adapter preserves it verbatim and does NOT auto-derive one from prose."
    ),
    "table": (
        "Optional dict-input fields the adapter passes through alongside `rows`:\n"
        "  - `note`: footnote shown under the table (max 1000 chars). The "
        "renderer prepends `※` automatically — do NOT include it yourself.\n"
        "  - `column_widths`: {column_key: px_int} per-column width hints (40-1200).\n"
        "  - `table_width_px`: total table width in pixels (int, 120-4000).\n"
        "  - `cell_styles` / `cell_html` (v0.9.0+ / v0.10.0): side-tables keyed "
        "by `\"rowKey::columnKey\"`; cell_styles values = {bg, fg} color tokens, "
        "cell_html values = sanitized HTML for per-char color / format. Plain "
        "`rows` are kept; cell_html is read-mode preferred when present.\n"
        "  - `merges`: list of `{r, c, rs, cs}` cell-span objects "
        "(row/col index plus row-span/col-span).\n"
        "  - `columns`: list of column descriptors `{key, label, type, meta}` "
        "for per-report column override (v0.5.2)."
    ),
    "image": (
        "Optional dict-input fields beyond the existing files/caption/aspect_ratio/max_count:\n"
        "  - `note`: caption-line footnote (max 1000 chars). Renderer prepends "
        "`※` automatically — do NOT include it yourself.\n"
        "  - `annotations`: list of 2-D overlay annotations (point/arrow/box etc) "
        "per the _ANNOTATIONS_FIELD schema."
    ),
    "comparison": (
        "Optional dict-input fields the adapter passes through alongside `rows`:\n"
        "  - `note`: footnote (max 1000 chars, no leading `※` — renderer adds it).\n"
        "  - `column_widths` / `table_width_px` / `merges`: same shape as table.\n"
        "  - `row_label_width`: pixel width of the left label column (int, 60-1200).\n"
        "  - `cases`: list of `{key, label}` defining the comparison columns.\n"
        "  - `cell_styles` / `cell_html` (v0.9.0+ / v0.10.0): side-tables keyed "
        "by `\"rowKey::caseKey\"`; cell_styles values = {bg, fg} color tokens, "
        "cell_html values = sanitized HTML for per-char color / format.\n"
        "  - `horizontal_scroll`: bool toggling horizontal overflow scroll.\n"
        "  - `max_cases`: int (2-30) capping how many cases render.\n"
        "  - `image_max_height_px`: int (80-600) clamp on image-row height."
    ),
    "pie": (
        "Rows are `[{label, value, color}]`. The widget has no `variant`, no "
        "`group_top_n`, no `value_format` — use `chart_type` (\"pie\"|\"donut\"), "
        "`hole` (0-0.9, only meaningful for donut), and `text_info` "
        "(label|label+percent|label+value|label+value+percent|percent|value|none) "
        "to control what each slice displays. `sort` (bool) toggles descending sort; "
        "`show_legend` toggles the legend. Optional `unit` (<=32 chars) appears in tooltips."
    ),
    "treemap": (
        "Rows are `[{label, parent, value, color}]` — `parent` is the label of the "
        "containing rectangle (empty string or omitted for root nodes). "
        "`branchvalues` is \"remainder\" (children sum to parent's remaining area) or "
        "\"total\" (parent's value = sum of children). `text_info` enum controls per-cell "
        "labels (label|label+value|label+value+percent_parent|label+value+percent_root|"
        "label+percent_root|value|none). No `group_top_n` — pre-aggregate upstream."
    ),
    "packing": (
        "Circle-packing layout. Rows are `[{label, parent, value, color}]` like treemap. "
        "`padding` is int 0-20 (gap between circles in px). Use `text_info` "
        "(label|label+value|label+value+percent|value|none) to control cell text; there "
        "is no separate `show_value` toggle."
    ),
    "waffle": (
        "Rows are `[{label, value, color}]`. Grid sized by `cols` (1-50) x "
        "`grid_rows` (1-50). `shape` is \"square\"|\"circle\" (this is the visual marker, "
        "not a variant). `fill_direction` is \"row\"|\"column\". `show_legend` and "
        "`show_value_per_cell` are independent booleans."
    ),
    "heading": (
        "Simple heading block. Required: `text` (1-200 chars, plain text — "
        "used as TOC / export title). Optional: `level` (1|2|3), `text_style` "
        "object (color/weight/etc), `margin_bottom_px` (int 0-200), "
        "`text_html` (v0.10.0 — sanitized HTML for per-char color / format "
        "ON TOP of the plain `text`; both fields are kept in sync). "
        "There is NO `tag` field."
    ),
    "progress_bar": (
        "Multi-row progress widget. Top-level: `default_max` (number > 0), "
        "`unit` (<=8 chars). Each row is an item: "
        "`items: [{label, value, max?, note?, status?}]` where `status` is one of "
        "pending|in_progress|done|blocked. Per-item `max` overrides `default_max`. "
        "Note that `value`/`max`/`label` are per-item — not widget-level."
    ),
    "cad_3d": (
        "3-D CAD viewer. Required: `file_id` (uploaded STEP/glb/etc). Optional: "
        "`loaded_filename` (<=255), `view_state` `{position[3], target[3], zoom, "
        "show_grid, show_axes, sidebar_open}`, `hidden_parts: [str]`, "
        "`wireframe_parts: [str]`, and `annotations` of two kinds:\n"
        "  - `{id, type: \"distance_3d\", p1: {x,y,z}, p2: {x,y,z}, label, color}`\n"
        "  - `{id, type: \"point_3d\", p1: {x,y,z}, label, color}`\n"
        "`color` is a `#rrggbb` hex string. Use `file_id` (not `model`)."
    ),
    "scatter3d": (
        "3-D scatter / surface. `mode` is fixed \"scatter3d\". `series` is a list of "
        "`{label, kind, x_key, y_key, z_key, color_key, color}` where `kind` is "
        "\"scatter3d\" or \"surface\" — the adapter PRESERVES caller-provided series "
        "verbatim (does not synthesize them). `colorscale` enum picks the gradient. "
        "`columns` + `rows` carry the underlying numeric table. "
        "Note: scatter3d has NO `annotations` field (2-D pixel overlays are meaningless "
        "under free rotation)."
    ),
    "bulleted_list": (
        "Simplest list widget. Top-level `items` is an array of plain strings "
        "(each minLength 1). No markdown bullets in the strings; the renderer adds "
        "the bullet marker. Optional `caption` / `caption_skip_autofill` only."
    ),
    "html_embed": (
        "Embed an uploaded HTML bundle. Required: `file_id` (entry HTML), "
        "`bundle_id` (1-32 chars, the assets bundle group). Optional: "
        "`entry_path` (1-512, relative path of the entry file), `filename`, "
        "`height_px` (60-4000), `display` (\"card\"|\"inline\"), `title` (<=200), "
        "`description` (<=1000), `cover_file_id` (poster image shown before load). "
        "There is no `url` field — uploaded bundles only."
    ),

    # v0.12.0 — hint coverage extended from 12 to 33 widgets so every widget
    # type the LLM might touch has a 1-3-line authoring guide alongside the
    # JSON schema dump. Each hint is best-effort short: required fields +
    # key optional fields + any common author pitfalls.

    "chart": (
        "Line / bar / area chart. Required: `series` list — each item "
        "`{name, data: [{x, y}, ...]}`. Optional: `x_axis_title`, "
        "`y_axis_title`, `x_min` / `x_max` / `y_min` / `y_max`, "
        "`annotations`. `caption_html` (RA defcb74) carries per-char color."
    ),
    "scatter": (
        "X-Y scatter. Required: `rows` of `{x, y, label?, group?}`. "
        "Optional: axis min/max, `annotations`. group enables color "
        "legend; label shows on hover."
    ),
    "scatter3d": (
        "3D scatter / surface. Required: `rows` of `{x, y, z, label?}` "
        "for points OR explicit `series` for surface plots. Surface mode: "
        "set `series.kind=\"surface\"` and the adapter preserves it — DO "
        "NOT pass rows when authoring a surface."
    ),
    "box": (
        "Box-and-whisker. Required: `rows` of `{group, values: [..]}` OR "
        "`{values: [..]}` for single. Optional: `y_min` / `y_max`, "
        "`box_points` (\"outliers\"|\"all\"|\"none\"), `box_mean` (bool), "
        "`jitter` (0-1)."
    ),
    "density": (
        "Distribution density plot. Required: `rows` of `{group, values: "
        "[..]}` OR `{values: [..]}`. Optional: `bandwidth_mode` "
        "(\"auto\"|\"manual\"), `bandwidth` (when manual), `samples` "
        "(curve resolution), `fill` / `show_dots` / `dot_opacity`."
    ),
    "contour": (
        "Contour plot. Required: `z` matrix (list-of-lists) or `rows` of "
        "`{x, y, z}`. Optional: `colorscale`, `reverse_scale`, `z_min` / "
        "`z_max`, `ncontours`, `contours_coloring` (\"fill\"|\"lines\"), "
        "`show_lines` / `show_labels` / `connect_gaps`."
    ),
    "heatmap": (
        "Heatmap. Required: `z` matrix (list-of-lists numeric) OR `rows` "
        "of `{x, y, z}`. Optional: `x_axis_title`, `y_axis_title`, "
        "`colorscale`, `reverse_scale`, `z_min` / `z_max`, `show_values`."
    ),
    "sankey": (
        "Sankey flow. Required: `nodes` (list of `{id, label}`) + `links` "
        "(list of `{source, target, value}` where source/target reference "
        "node ids). Optional: `arrangement` (\"snap\"|\"perpendicular\"|"
        "\"freeform\"|\"fixed\"), `node_pad`, `node_thickness`, `unit`."
    ),
    "network": (
        "Graph / network. Required: `nodes` (`{id, label?, group?, value?, "
        "color?, x?, y?, fixed?}`) + `edges` (`{source, target, weight?, "
        "label?, color?}`). Optional: `directed`, `layout` (\"force\"|"
        "\"circular\"|\"grid\"), `node_shape`, `show_labels`, "
        "`color_by_group`, `node_size_by_value`."
    ),
    "mind_map": (
        "Mind map / hierarchical tree. Required: `rows` of `{label, "
        "parent?, color?}` — empty parent = root. Optional: `layout` "
        "(\"radial\"|\"horizontal\"|\"vertical\"), `branch_style`, "
        "`color_by_group`, `show_root_emphasis`."
    ),
    "tree": (
        "Org / decision tree. Required: `rows` of `{label, parent?, "
        "subtitle?, color?}` — empty parent = root. Optional: "
        "`orientation` (\"vertical\"|\"horizontal\"), `node_shape`, "
        "`edge_style`, `color_by_group`, `node_padding_x` / `_y`."
    ),
    "radar": (
        "Radar / spider chart. Required: `rows` of `{axis, value, "
        "series?}`. Multi-series = pass a `series` key per row. "
        "Optional: `value_min` / `value_max`, `fill_opacity` (0-1), "
        "`show_legend`."
    ),
    "milestone": (
        "Timeline of milestones. Required: `items` of `{date, label, "
        "kind?, color?}` (YYYY-MM-DD). Optional top-level: `start_date` "
        "/ `end_date` (window), `annotations`. Common pitfall: use the "
        "`report_milestone_add` tool for a single-item shortcut rather "
        "than authoring this content directly."
    ),
    "flowchart": (
        "Flow diagram. Required: `items` (or `steps` / `nodes`) of "
        "`{id, label, kind?, edges: [{to, label?}]?}`. Optional: "
        "`orientation` (\"vertical\"|\"horizontal\"|\"top-to-bottom\"|"
        "\"left-to-right\"). String input is parsed as a numbered list."
    ),
    "equation": (
        "LaTeX equation. Required: `latex` string (the formula source, "
        "max 5000 chars). Optional: `caption` (max 200), `display_mode` "
        "(\"block\"|\"inline\"), `number` (label like \"식 3\")."
    ),
    "video": (
        "Embedded video. Required: at least one uploaded `file_id` "
        "(upload via POST /api/files first — paths / URLs are not "
        "accepted). Optional: `caption` (max 200), `max_count` (limit "
        "for the player's file picker), and boolean playback flags "
        "(autoplay / muted / loop) as the schema allows. `caption_html` "
        "carries per-char color."
    ),
    "attachment": (
        "File attachment list. Required: at least one `file_id` from a "
        "prior POST /api/files upload (URLs / paths are rejected). "
        "Optional: `caption` (max 200), `max_count`. To attach images / "
        "videos prefer the dedicated `image` / `video` widgets."
    ),
    "cad_3d": (
        "Embedded CAD viewer. Required: `model` (`{file_id, format?}`) "
        "OR `model_file_id`. Optional: `caption`, `annotations` — each "
        "annotation is `{kind: \"distance_3d\"|\"point_3d\", points: "
        "[[x,y,z], ...], label?}`. `caption_html` carries per-char color."
    ),
    "raci_matrix": (
        "RACI table. Required: `rows` of `{activity, assignments: "
        "{role_key: \"R\"|\"A\"|\"C\"|\"I\"}}`. The adapter ALSO "
        "accepts a flat dict `{activity: {role: code}}` and rewrites it. "
        "Optional: `roles` (list of `{key, label}` — derived from the "
        "rows if omitted)."
    ),
    "key_value": (
        "Key-value pair block. Required: `items` of `{key, value}` OR "
        "pass a flat dict at the top level (`{foo: \"bar\", baz: 42}`) "
        "and the adapter packs it into items. Korean / non-ASCII keys "
        "are preserved literally."
    ),
    "quadrant": (
        "2x2 / 4-quadrant chart. Two modes: (1) PLOT — list of `{x, y, "
        "label?}` for scatter-style plotting; (2) BUCKET — pre-binned "
        "`bucket_items` keyed by quadrant. Optional: axis labels + "
        "quadrant labels. Adapter rejects list input unless x/y is "
        "present on every item."
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
