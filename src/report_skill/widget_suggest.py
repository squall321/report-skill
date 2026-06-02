"""Detect data shapes in free text that warrant visual widgets,
emit ExtraSpec entries ready for orchestrator.normalize_report(...)
extra_blocks_input.

This module scans free-form user text with deterministic regex/keyword
patterns matched to the 33 ReportArchive widget adapters. When the
deterministic pass finds nothing and an LLM provider is configured, it
optionally consults the LLM for a final pass.

The output ExtraSpec is shaped so the caller can feed `input` straight
into the adapter's `normalize(...)` for the corresponding `widget_type`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from report_skill import llm
from report_skill.repair import coerce_iso_date, coerce_number, to_slug

_Confidence = Literal["high", "medium", "low"]
_CONF_RANK: dict[str, int] = {"high": 0, "medium": 1, "low": 2}
_SRC_RANK: dict[str, int] = {"deterministic": 0, "llm": 1}


@dataclass
class ExtraSpec:
    suggested_id: str
    widget_type: str
    props: dict
    input: Any
    confidence: _Confidence
    matched_pattern: str
    source: str = field(default="deterministic")
    # When this spec wins, every widget_type listed here is dropped from the
    # final result. Used to express "milestone owns date data — don't also
    # suggest a chart for the same dates".
    suppresses: set[str] = field(default_factory=set)


# --------------------------------------------------------------------------- #
# Regex library
# --------------------------------------------------------------------------- #

# Time series labels: 1월, Jan, Q1, 2026-01, week 1, W1, 1/15, 1주차
_MONTH_KO = r"(?:1[0-2]|[1-9])\s*월"
_MONTH_EN = (
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"(?:uary|ruary|ch|il|e|y|ust|tember|ober|ember)?"
)
_QUARTER = r"Q[1-4]"
_YM_DASH = r"20\d{2}[-/.]\d{1,2}(?:[-/.]\d{1,2})?"
_WEEK = r"(?:week|wk|W|주차)\s*\d{1,2}|\d{1,2}\s*주차?"
_TS_LABEL = (
    rf"(?:{_MONTH_KO}|{_MONTH_EN}|{_QUARTER}|{_YM_DASH}|{_WEEK})"
)
# label + optional separator + numeric value (allow commas + decimals + %)
_RE_TIMESERIES = re.compile(
    rf"({_TS_LABEL})\s*(?:[:는은이가=]|[-]\s|에는?)?\s*"
    rf"([+-]?\d[\d,]*(?:\.\d+)?)\s*(?:[%원개건명점])?",
    re.IGNORECASE,
)

# Explicit-date + label tokens (date can be ISO, KO yyyy.mm.dd, or short)
_RE_DATE_ISO = re.compile(
    r"(20\d{2}[-./]\d{1,2}[-./]\d{1,2})\s*[:\-–—]?\s*"
    r"([^\n,;。.!?]{2,40})"
)
_RE_DATE_KO_HANGEUL = re.compile(
    r"(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*[:\-–—]?\s*"
    r"([^\n,;。.!?]{2,40})"
)
_RE_DATE_EN_SHORT = re.compile(
    rf"({_MONTH_EN})\s+(\d{{1,2}})(?:,?\s*(20\d{{2}}))?\s*[:\-–—]"
    r"\s*([^\n,;。.!?]{2,40})",
    re.IGNORECASE,
)

# Percentage split (label + N%)
_RE_PCT = re.compile(
    r"([\w가-힣\- ]{1,30}?)\s*[:\-–=]?\s*(\d{1,3}(?:\.\d+)?)\s*%"
)

# Process steps
_RE_ARROW = re.compile(r"([^\n→\->]{2,40}?)\s*(?:->|→)\s*", re.UNICODE)
_RE_NUMBERED = re.compile(
    r"(?:^|\n)\s*\d+[.)]\s+([^\n]{2,80})"
)
_RE_KO_SEQUENCE = re.compile(
    r"(?:먼저|우선|첫\s*째|첫째로)\b.*?"
    r"(?:다음(?:으로|에)?|이어서|둘\s*째|둘째로)\b.*?"
    r"(?:마지막(?:으로|에)?|끝으로|셋\s*째|셋째로|최종(?:적으로)?)\b",
    re.DOTALL,
)

# Comparison cues
_RE_VS = re.compile(
    r"\b(as[\- ]is)\s*(?:vs\.?|/|→|->|대|에서)\s*(to[\- ]be)\b"
    r"|\b(이전|기존|현재|before)\s*(?:vs\.?|/|→|->|대|에서)\s*(이후|개선|after|향후)\b"
    r"|([\w가-힣]{1,12})\s*(?:vs\.?|VS)\s*([\w가-힣]{1,12})"
    r"|대안\s*1.*?대안\s*2",
    re.IGNORECASE | re.DOTALL,
)

# Hierarchy: indented bullets OR "Parent: A, B, C; OtherParent: D, E"
_RE_INDENT_BULLET = re.compile(
    r"(?m)^(\s+)[-*•·]\s+(.+)$"
)
_RE_PARENT_LIST = re.compile(
    r"([\w가-힣 ]{1,30})\s*[:：]\s*([\w가-힣 ]{1,30}(?:\s*,\s*[\w가-힣 ]{1,30}){1,})"
)

# RACI markers
_RE_RACI_CODES = re.compile(r"\b([RACI](?:\s*/\s*[RACI])*)\b")
_RE_RACI_KO_HEADERS = re.compile(
    r"(책임|자문|통보|실행|승인|협의|공유|수행)\s*[:：]"
)

# LaTeX cues
_RE_LATEX = re.compile(
    r"(\$\$[\s\S]+?\$\$)|(\\frac\b)|(\\sum\b)|(\\int\b)|(\\sqrt\b)"
    r"|(\^\{[^}]+\})|(_\{[^}]+\})|(\\begin\{[a-z]+\})"
)

# Sankey: source -> target : value
_RE_SANKEY = re.compile(
    r"([\w가-힣 _-]{1,30}?)\s*(?:->|→)\s*([\w가-힣 _-]{1,30}?)\s*"
    r"[:：=]\s*([+-]?\d[\d,]*(?:\.\d+)?)"
)

# Quadrant cues
_RE_QUADRANT_CUE = re.compile(
    r"(important.*urgent|urgent.*important|value.*effort|effort.*value"
    r"|2\s*x\s*2|quadrant|매트릭스|사분면|중요도.*긴급도|긴급도.*중요도)",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def suggest_extras(
    user_text: str,
    *,
    template_blocks: list[dict] | None = None,
    use_llm: Literal["auto", "never", "always"] = "auto",
    max_extras: int = 5,
) -> list[ExtraSpec]:
    """Scan user_text and return ranked, deduplicated ExtraSpecs."""
    if not isinstance(user_text, str) or not user_text.strip():
        return []

    blocked_types: set[str] = set()
    if template_blocks:
        for b in template_blocks:
            t = (b or {}).get("widget_type") or (b or {}).get("type")
            if isinstance(t, str):
                blocked_types.add(t)

    counter = {"i": 0}

    def _next_id(widget_type: str) -> str:
        counter["i"] += 1
        return f"auto_{widget_type}_{counter['i']}"

    found: list[ExtraSpec] = []
    for detector in _DETECTORS:
        try:
            spec = detector(user_text, _next_id)
        except Exception:
            spec = None
        if spec is None:
            continue
        if spec.widget_type in blocked_types:
            continue
        found.append(spec)

    if not found and use_llm in ("auto", "always") and llm.is_configured():
        found.extend(_llm_fallback(user_text, blocked_types, _next_id))
    elif use_llm == "always" and llm.is_configured():
        # Still let LLM contribute when explicit
        found.extend(_llm_fallback(user_text, blocked_types, _next_id))

    # Apply cross-widget suppression (eg milestone suppresses chart for
    # date-led data). Suppression respects confidence: only HIGH-conf specs
    # are allowed to suppress others — keeps the system permissive when the
    # winning spec is itself uncertain.
    suppressed: set[str] = set()
    for s in found:
        if s.confidence == "high":
            suppressed |= s.suppresses
    found = [s for s in found if s.widget_type not in suppressed]

    # Dedup by widget_type — keep highest-confidence / deterministic-first.
    found.sort(key=lambda s: (_CONF_RANK[s.confidence], _SRC_RANK.get(s.source, 9)))
    deduped: list[ExtraSpec] = []
    seen_types: set[str] = set()
    for s in found:
        if s.widget_type in seen_types:
            continue
        seen_types.add(s.widget_type)
        deduped.append(s)
        if len(deduped) >= max_extras:
            break
    return deduped


# --------------------------------------------------------------------------- #
# Detectors — each returns ExtraSpec | None
# --------------------------------------------------------------------------- #
def _detect_timeseries(text: str, next_id) -> ExtraSpec | None:
    matches = _RE_TIMESERIES.findall(text)
    cleaned: list[tuple[str, float]] = []
    seen_x: set[str] = set()
    for label, raw_num in matches:
        n = coerce_number(raw_num)
        if n is None:
            continue
        x = label.strip()
        if x in seen_x:
            continue
        seen_x.add(x)
        cleaned.append((x, n))
    if len(cleaned) < 3:
        return None
    rows = [{"x": x, "y": y} for x, y in cleaned]
    label = _smart_caption(text, ["추이", "추세", "변화", "월별", "trend", "주간", "분기"])
    return ExtraSpec(
        suggested_id=next_id("chart"),
        widget_type="chart",
        props={"label": label or "시계열 데이터"},
        input=rows,
        confidence="high",
        matched_pattern=f"{len(cleaned)} time-series points",
    )


def _detect_milestone(text: str, next_id) -> ExtraSpec | None:
    items: list[dict] = []
    seen_dates: set[str] = set()

    for date_str, lbl in _RE_DATE_ISO.findall(text):
        iso = coerce_iso_date(date_str)
        if iso and iso not in seen_dates and lbl.strip():
            items.append({"date": iso, "label": lbl.strip().rstrip(".,;")})
            seen_dates.add(iso)

    for mo, day, lbl in _RE_DATE_KO_HANGEUL.findall(text):
        try:
            from datetime import date as _date
            iso = _date(2099, int(mo), int(day))  # year placeholder
            # Use current-year heuristic to keep ISO consistent.
            from datetime import datetime as _dt
            iso_str = _dt.now().replace(month=int(mo), day=int(day)).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        if iso_str in seen_dates or not lbl.strip():
            continue
        items.append({"date": iso_str, "label": lbl.strip().rstrip(".,;")})
        seen_dates.add(iso_str)

    for mo_name, day, year, lbl in _RE_DATE_EN_SHORT.findall(text):
        from datetime import datetime as _dt
        year_int = int(year) if year else _dt.now().year
        try:
            d = _dt.strptime(f"{mo_name[:3]} {day} {year_int}", "%b %d %Y")
        except ValueError:
            continue
        iso = d.strftime("%Y-%m-%d")
        if iso in seen_dates or not lbl.strip():
            continue
        items.append({"date": iso, "label": lbl.strip().rstrip(".,;")})
        seen_dates.add(iso)

    if len(items) < 2:
        return None
    items.sort(key=lambda r: r["date"])
    label = _smart_caption(text, ["일정", "마일스톤", "로드맵", "milestone", "timeline"])
    return ExtraSpec(
        suggested_id=next_id("milestone"),
        widget_type="milestone",
        props={"label": label or "주요 일정"},
        input=items,
        confidence="high",
        matched_pattern=f"{len(items)} explicit dates",
        # Dates with labels are milestone data — don't let the loose timeseries
        # / chart detector double-report the same content as a chart.
        suppresses={"chart", "scatter"},
    )


def _detect_pie(text: str, next_id) -> ExtraSpec | None:
    matches = _RE_PCT.findall(text)
    pairs: list[tuple[str, float]] = []
    seen_labels: set[str] = set()
    for raw_label, pct in matches:
        lbl = raw_label.strip().rstrip(":-=").strip()
        if not lbl or lbl.lower() in seen_labels:
            continue
        n = coerce_number(pct)
        if n is None:
            continue
        pairs.append((lbl, n))
        seen_labels.add(lbl.lower())
    if len(pairs) < 2:
        return None
    total = sum(v for _, v in pairs)
    if not (60 <= total <= 130):
        return None
    label = _smart_caption(text, ["구성비", "비중", "점유", "share", "분포", "비율"])
    return ExtraSpec(
        suggested_id=next_id("pie"),
        widget_type="pie",
        props={"label": label or "비중 구성"},
        input={lbl: v for lbl, v in pairs},
        confidence="medium",
        matched_pattern=f"{len(pairs)} pct slices, sum={total:.0f}",
    )


def _detect_flowchart(text: str, next_id) -> ExtraSpec | None:
    # 1) Arrow form: A -> B -> C (need 3+ tokens i.e. 2+ arrows)
    arrow_segments = re.split(r"\s*(?:->|→)\s*", text)
    if len(arrow_segments) >= 3:
        # Reconstruct only when the arrow form appears in same window
        if "->" in text or "→" in text:
            steps = [s.strip() for s in arrow_segments if s.strip()]
            # take a contiguous run of short labels
            steps = [s for s in steps if 1 <= len(s) <= 60][:8]
            if len(steps) >= 3:
                return ExtraSpec(
                    suggested_id=next_id("flowchart"),
                    widget_type="flowchart",
                    props={"label": "프로세스"},
                    input=steps,
                    confidence="medium",
                    matched_pattern="A -> B -> C arrows",
                )

    # 2) Numbered list: 1) ... 2) ... 3) ...
    numbered = _RE_NUMBERED.findall(text)
    if len(numbered) >= 3:
        steps = [s.strip() for s in numbered if s.strip()][:8]
        if len(steps) >= 3:
            return ExtraSpec(
                suggested_id=next_id("flowchart"),
                widget_type="flowchart",
                props={"label": "순서"},
                input=steps,
                confidence="medium",
                matched_pattern=f"{len(numbered)} numbered items",
            )

    # 3) Korean sequence: 먼저 ... 다음 ... 마지막 ...
    if _RE_KO_SEQUENCE.search(text):
        steps = _split_ko_sequence(text)
        if len(steps) >= 3:
            return ExtraSpec(
                suggested_id=next_id("flowchart"),
                widget_type="flowchart",
                props={"label": "순서"},
                input=steps,
                confidence="medium",
                matched_pattern="먼저/다음/마지막 sequence",
            )

    return None


def _detect_comparison(text: str, next_id) -> ExtraSpec | None:
    m = _RE_VS.search(text)
    if not m:
        return None
    # Provide a placeholder rows shape — caller will supply concrete cases.
    case_a = "as_is"
    case_b = "to_be"
    if m.group(3) and m.group(4):
        case_a, case_b = m.group(3).strip(), m.group(4).strip()
    elif m.group(5) and m.group(6):
        case_a, case_b = m.group(5).strip(), m.group(6).strip()
    # Look for 2+ "metric: a / b" lines
    rows_re = re.compile(
        r"(?m)^[\s•\-*]*([\w가-힣 ]{1,30})\s*[:：]\s*"
        r"([^/\n]{1,40})\s*/\s*([^/\n]{1,40})$"
    )
    found = rows_re.findall(text)
    rows: dict[str, dict] = {}
    for metric, a, b in found:
        rows[metric.strip()] = {case_a: a.strip(), case_b: b.strip()}
    if len(rows) < 2:
        return None
    return ExtraSpec(
        suggested_id=next_id("comparison"),
        widget_type="comparison",
        props={"label": f"{case_a} vs {case_b}"},
        input=rows,
        confidence="medium",
        matched_pattern=f"{case_a} vs {case_b} with {len(rows)} rows",
    )


def _detect_tree(text: str, next_id) -> ExtraSpec | None:
    # 1) Indented bullets — need 3+ items across at least 2 indent levels
    indents = _RE_INDENT_BULLET.findall(text)
    if len(indents) >= 3:
        levels = {len(i) for i, _ in indents}
        if len(levels) >= 2:
            # Build nested rows using stack
            rows = _build_indent_tree(indents)
            if len(rows) >= 3:
                return ExtraSpec(
                    suggested_id=next_id("tree"),
                    widget_type="tree",
                    props={"label": "구조"},
                    input=rows,
                    confidence="medium",
                    matched_pattern=f"{len(rows)} indented bullets",
                )

    # 2) Parent: A, B, C ; OtherParent: D, E
    parts = _RE_PARENT_LIST.findall(text)
    if len(parts) >= 2:
        nested: dict[str, list[str]] = {}
        total_children = 0
        for parent, children in parts:
            kids = [c.strip() for c in children.split(",") if c.strip()]
            if kids:
                nested[parent.strip()] = kids
                total_children += len(kids)
        if nested and total_children >= 3:
            return ExtraSpec(
                suggested_id=next_id("tree"),
                widget_type="tree",
                props={"label": "분류"},
                input=nested,
                confidence="medium",
                matched_pattern=f"{len(nested)} parents w/ children",
            )
    return None


def _detect_raci(text: str, next_id) -> ExtraSpec | None:
    # Korean style first
    if len(_RE_RACI_KO_HEADERS.findall(text)) >= 2:
        ko_re = re.compile(
            r"(?m)^[\s•\-*]*([\w가-힣 ]{1,30})\s*[:：]\s*"
            r"(?:책임\s*([\w가-힣, ]+))?\s*"
            r"(?:자문\s*([\w가-힣, ]+))?\s*"
            r"(?:통보\s*([\w가-힣, ]+))?$"
        )
        rows: dict[str, dict] = {}
        for activity, a, c, i in ko_re.findall(text):
            assignments: dict[str, str] = {}
            if a:
                for role in a.split(","):
                    if role.strip():
                        assignments[role.strip()] = "A"
            if c:
                for role in c.split(","):
                    if role.strip():
                        assignments[role.strip()] = "C"
            if i:
                for role in i.split(","):
                    if role.strip():
                        assignments[role.strip()] = "I"
            if assignments:
                rows[activity.strip()] = assignments
        if len(rows) >= 2:
            return ExtraSpec(
                suggested_id=next_id("raci_matrix"),
                widget_type="raci_matrix",
                props={"label": "R&R"},
                input=rows,
                confidence="medium",
                matched_pattern="Korean RACI labels detected",
            )

    # Generic table-ish: lines with multiple R/A/C/I cells
    raci_lines = 0
    rows: dict[str, dict] = {}
    for line in text.splitlines():
        codes = _RE_RACI_CODES.findall(line)
        if len(codes) >= 2:
            raci_lines += 1
            head = line.split("|")[0].split("\t")[0].strip()
            head = re.sub(r"[RACI/\s|]+$", "", head).strip()
            if head:
                rows[head[:40]] = {"role_" + str(i + 1): c for i, c in enumerate(codes)}
    if raci_lines >= 2 and len(rows) >= 2:
        return ExtraSpec(
            suggested_id=next_id("raci_matrix"),
            widget_type="raci_matrix",
            props={"label": "RACI 매트릭스"},
            input=rows,
            confidence="medium",
            matched_pattern=f"{raci_lines} lines w/ RACI letters",
        )
    return None


def _detect_equation(text: str, next_id) -> ExtraSpec | None:
    m = _RE_LATEX.search(text)
    if not m:
        return None
    # Prefer the $$...$$ block when present
    block = re.search(r"\$\$[\s\S]+?\$\$", text)
    latex = block.group(0) if block else m.group(0)
    return ExtraSpec(
        suggested_id=next_id("equation"),
        widget_type="equation",
        props={"label": "수식"},
        input=latex,
        confidence="high",
        matched_pattern="LaTeX tokens detected",
    )


def _detect_sankey(text: str, next_id) -> ExtraSpec | None:
    flows: list[dict] = []
    for src, dst, val in _RE_SANKEY.findall(text):
        n = coerce_number(val)
        if n is None:
            continue
        flows.append({"source": src.strip(), "target": dst.strip(), "value": n})
    if len(flows) < 2:
        return None
    return ExtraSpec(
        suggested_id=next_id("sankey"),
        widget_type="sankey",
        props={"label": "흐름"},
        input=flows,
        confidence="medium",
        matched_pattern=f"{len(flows)} flow triples",
    )


def _detect_quadrant(text: str, next_id) -> ExtraSpec | None:
    if not _RE_QUADRANT_CUE.search(text):
        return None
    # Collect candidate items from bullets / numbered list / comma list
    items: list[str] = []
    for line in text.splitlines():
        s = line.strip().lstrip("-*•·●").strip()
        if 2 <= len(s) <= 60 and not _RE_QUADRANT_CUE.search(s):
            items.append(s)
    items = [it for it in items if it][:8]
    if len(items) < 3:
        return None
    buckets = {"q1": [], "q2": [], "q3": [], "q4": []}
    for i, it in enumerate(items):
        buckets[f"q{(i % 4) + 1}"].append(it)
    return ExtraSpec(
        suggested_id=next_id("quadrant"),
        widget_type="quadrant",
        props={"label": "우선순위 매트릭스"},
        input=buckets,
        confidence="low",
        matched_pattern="quadrant cue + items",
    )


# --------------------------------------------------------------------------- #
# Priority detector: structured data (title + axes + delimited rows)
# --------------------------------------------------------------------------- #
# A line that looks like delimited columns: `a, b, c` or `a | b | c` or tab.
# Requires at least 2 cells; rejects pure-text sentences (commas are common).
_RE_DELIM_LINE = re.compile(
    r"^[^\n,|\t]{1,80}(?:\s*[,|\t]\s*[^\n,|\t]{1,80}){1,}\s*$",
    re.MULTILINE,
)
_RE_AXIS_X = re.compile(
    r"(?im)^\s*(?:x[-_ ]?(?:axis|축)?|가로[- ]?(?:축)?)\s*[:：=]\s*(.+?)\s*$"
)
_RE_AXIS_Y = re.compile(
    r"(?im)^\s*(?:y[-_ ]?(?:axis|축)?|세로[- ]?(?:축)?)\s*[:：=]\s*(.+?)\s*$"
)


def _detect_structured_chart(text: str, next_id) -> ExtraSpec | None:
    """Top-priority detector for the most obvious chartable shape:

        <title>
        x: <axis name>      (optional)
        y: <axis name>      (optional)
        label1, value1
        label2, value2
        label3, value3

    Also accepts a header row (`month, revenue, profit`) and CSV/markdown
    table delimiters (`,` / `|` / `\\t`). When fired, suppresses the loose
    `chart`/`scatter`/`table` detectors so we don't double-report.
    """
    lines = text.splitlines()

    # Find the longest run of consecutive delimited lines (≥2).
    best_run: tuple[int, int] = (-1, -1)
    cur_start = -1
    for i, ln in enumerate(lines):
        if _RE_DELIM_LINE.match(ln):
            if cur_start == -1:
                cur_start = i
        else:
            if cur_start != -1:
                if i - cur_start > best_run[1] - best_run[0]:
                    best_run = (cur_start, i)
                cur_start = -1
    if cur_start != -1:
        if len(lines) - cur_start > best_run[1] - best_run[0]:
            best_run = (cur_start, len(lines))
    if best_run == (-1, -1) or best_run[1] - best_run[0] < 2:
        return None

    run_start, run_end = best_run
    rows_raw = [lines[i] for i in range(run_start, run_end)]
    rows = [
        [c.strip() for c in re.split(r"\s*[,|\t]\s*", ln.strip()) if c.strip()]
        for ln in rows_raw
    ]
    if not rows or any(len(r) < 2 for r in rows):
        return None

    # ---- prose false-positive filter ----
    # Real tabular data has short cells and at least some numbers. Prose with
    # commas ("A, B, C 그리고 D를 했다") trips the delimiter regex but fails
    # these signals.
    all_cells = [c for r in rows for c in r]
    if all_cells:
        avg_cell_len = sum(len(c) for c in all_cells) / len(all_cells)
        if avg_cell_len > 40:
            return None
    # Sentence-ending punctuation inside a cell is a strong prose tell.
    # NOTE: `(?<!\d)` excludes decimal points (1.5) so numeric-only rows pass.
    _PROSE_PUNCT = re.compile(r"(?<!\d)[.!?。]\s+\S")
    for r in rows:
        for c in r:
            if _PROSE_PUNCT.search(c):
                return None
    # Numeric ratio signal: when ZERO data cells are numeric AND there's no
    # header row, the input is just prose-with-commas — bail. (A pure-text
    # tabular form WITH a header is still legitimate — we route it to the
    # table widget below.)
    data_for_signal = rows[1:] if (len(rows) > 1 and
                                   all(coerce_number(c) is None for c in rows[0])) else rows
    has_header_signal = len(rows) > 1 and all(coerce_number(c) is None for c in rows[0])
    data_cells = [c for r in data_for_signal for c in r]
    if data_cells:
        n_numeric = sum(1 for c in data_cells if coerce_number(c) is not None)
        ratio = n_numeric / len(data_cells)
        # No header AND no numbers → almost certainly prose.
        if not has_header_signal and ratio == 0:
            return None
        # Has header but extremely low numeric ratio AND header looks wordy
        # (avg header cell length > 12) → likely prose with a leading line.
        if has_header_signal and ratio < 0.20:
            avg_hdr_len = sum(len(c) for c in rows[0]) / max(1, len(rows[0]))
            if avg_hdr_len > 12:
                return None

    # Decide whether the first row is a header (all cells non-numeric).
    first_is_header = all(coerce_number(c) is None for c in rows[0])
    # When no header row, but the user wrote `x: <name>` / `y: <name>` hints
    # above the data, lift those into the column labels so the chart legend
    # actually says "월 / 매출" instead of "x / y1".
    axis_x_hint: str | None = None
    axis_y_hint: str | None = None
    if not first_is_header:
        mx = _RE_AXIS_X.search(text)
        my = _RE_AXIS_Y.search(text)
        axis_x_hint = mx.group(1).strip() if mx else None
        axis_y_hint = my.group(1).strip() if my else None
    if first_is_header:
        header = rows[0]
        data_rows = rows[1:]
    else:
        header = [axis_x_hint or "x"] + [
            (axis_y_hint if i == 1 and axis_y_hint else f"y{i}")
            for i in range(1, len(rows[0]))
        ]
        data_rows = rows

    if len(data_rows) < 2:
        return None

    # Need at least one column that's numeric in every data row (the y axis).
    n_cols = max(len(r) for r in data_rows)
    numeric_cols = []
    for j in range(n_cols):
        col_vals = [r[j] if j < len(r) else "" for r in data_rows]
        if all(coerce_number(v) is not None for v in col_vals if v != ""):
            numeric_cols.append(j)

    # Widget selection:
    #   - all cells numeric, no header → scatter (pure XY points)
    #   - has ≥1 numeric column (and either header or categorical x) → chart
    #   - no numeric column but has header → table (string-only tabular data)
    all_numeric = all(coerce_number(c) is not None for r in data_rows for c in r)
    if all_numeric and not first_is_header:
        widget_type = "scatter"
    elif numeric_cols:
        widget_type = "chart"
    elif first_is_header:
        widget_type = "table"
    else:
        return None

    # Title = first non-empty, non-axis line *above* the run.
    title = ""
    for k in range(run_start - 1, -1, -1):
        s = lines[k].strip()
        if not s:
            continue
        if _RE_AXIS_X.match(s) or _RE_AXIS_Y.match(s):
            continue
        title = s
        break

    # Slugged column specs
    col_keys: list[str] = []
    columns: list[dict] = []
    for j, h in enumerate(header[:n_cols]):
        key = to_slug(h) or f"col{j}"
        # uniquify
        if key in col_keys:
            key = f"{key}_{j}"
        col_keys.append(key)
        ctype = "number" if j in numeric_cols and (j != 0 or not first_is_header) else "text"
        columns.append({"key": key, "label": h, "type": ctype})

    records: list[dict] = []
    for r in data_rows:
        rec: dict = {}
        for j, cell in enumerate(r[:n_cols]):
            key = col_keys[j]
            num = coerce_number(cell)
            if j in numeric_cols:
                rec[key] = num if num is not None else cell
            else:
                rec[key] = cell
        records.append(rec)

    props: dict = {"label": title or "data"}
    if widget_type == "chart":
        props["columns"] = columns
        props["x_column_key"] = col_keys[0]
        # Optional axis titles from x:/y: lines anywhere in `text`.
        mx = _RE_AXIS_X.search(text)
        my = _RE_AXIS_Y.search(text)
        if mx:
            props["x_axis_title"] = mx.group(1).strip()
        if my:
            props["y_axis_title"] = my.group(1).strip()
    elif widget_type == "table":
        # All-text tabular data — give the table adapter explicit columns.
        props["columns"] = columns

    return ExtraSpec(
        suggested_id=next_id(widget_type),
        widget_type=widget_type,
        props=props,
        input=records,
        confidence="high",
        matched_pattern=(
            f"structured {n_cols}-col data, "
            f"{'header+' if first_is_header else ''}{len(data_rows)} rows"
        ),
        # We own this data — silence the loose detectors.
        suppresses={"chart", "scatter", "table", "pie"} - {widget_type},
    )


_DETECTORS = [
    _detect_structured_chart,   # NEW: highest priority — obvious chartable input
    _detect_milestone,          # before timeseries: date-led data wins
    _detect_timeseries,
    _detect_pie,
    _detect_flowchart,
    _detect_comparison,
    _detect_tree,
    _detect_raci,
    _detect_equation,
    _detect_sankey,
    _detect_quadrant,
]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _smart_caption(text: str, keywords: list[str]) -> str | None:
    """Pick a short caption — first sentence containing any keyword, capped."""
    sentences = re.split(r"[.!?。\n]", text)
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        for kw in keywords:
            if kw.lower() in s.lower():
                return s[:60]
    # fall back to first sentence
    for s in sentences:
        s = s.strip()
        if s:
            return s[:60]
    return None


def _split_ko_sequence(text: str) -> list[str]:
    parts = re.split(
        r"(?:먼저|우선|첫\s*째|첫째로|다음(?:으로|에)?|이어서|둘\s*째|"
        r"둘째로|마지막(?:으로|에)?|끝으로|셋\s*째|셋째로|최종(?:적으로)?)",
        text,
    )
    return [p.strip(" ,.;:") for p in parts if p and len(p.strip()) >= 2][:6]


def _build_indent_tree(indents: list[tuple[str, str]]) -> list[dict]:
    """Build flat parent-tagged rows from a list of (indent_str, label)."""
    stack: list[tuple[int, str]] = []  # (indent_len, label)
    rows: list[dict] = []
    for ind, label in indents:
        depth = len(ind)
        while stack and stack[-1][0] >= depth:
            stack.pop()
        parent = stack[-1][1] if stack else None
        row: dict = {"label": label.strip()}
        if parent:
            row["parent"] = parent
        rows.append(row)
        stack.append((depth, label.strip()))
    return rows


# --------------------------------------------------------------------------- #
# LLM fallback
# --------------------------------------------------------------------------- #
_LLM_SYSTEM = "You suggest visual widgets for report content."
_LLM_USER_TMPL = '''The user wrote:
"""
{text}
"""

Available widgets:
- chart        — bar/line chart for time series
- scatter      — XY numeric scatter
- pie          — proportional pie chart
- milestone    — date-stamped timeline
- flowchart    — process steps
- tree         — hierarchical structure
- raci_matrix  — responsibility grid
- equation     — math formula
- comparison   — multi-case comparison table

If the text contains data that would benefit from one of these visualizations,
extract it and return JSON. Otherwise return {{"extras": []}}.

Format:
{{"extras": [{{"widget_type": "<type>", "label": "...", "data": <shape-appropriate JSON>}}]}}
Limit: 3 extras max.'''


def _llm_fallback(text: str, blocked_types: set[str], next_id) -> list[ExtraSpec]:
    try:
        provider = llm.get_provider()
        prompt = _LLM_USER_TMPL.format(text=text[:1500])
        reply = provider.generate(
            [
                {"role": "system", "content": _LLM_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            max_tokens=800,
            temperature=0.1,
            json_mode=True,
        )
        data = llm.extract_json(reply)
    except Exception:
        return []

    if not isinstance(data, dict):
        return []
    raw_extras = data.get("extras")
    if not isinstance(raw_extras, list):
        return []
    out: list[ExtraSpec] = []
    for entry in raw_extras[:3]:
        if not isinstance(entry, dict):
            continue
        wtype = entry.get("widget_type")
        if not isinstance(wtype, str) or wtype in blocked_types:
            continue
        if wtype not in {
            "chart", "scatter", "pie", "milestone", "flowchart",
            "tree", "raci_matrix", "equation", "comparison",
        }:
            continue
        out.append(ExtraSpec(
            suggested_id=next_id(wtype),
            widget_type=wtype,
            props={"label": str(entry.get("label") or wtype)[:60]},
            input=entry.get("data"),
            confidence="low",
            matched_pattern="LLM fallback",
            source="llm",
        ))
    return out
