"""Template recommender — keyword scoring + optional LLM tie-break.

Given a free-form user note (Korean or English), rank the available report
templates by relevance. Pure stdlib + existing ``llm`` / ``client`` imports.

Strategy:
  1. Per-template canned vocab (``CANNED_VOCAB``) — count hits in user text.
     Templates outside the vocab fall back to ``name``+``description`` tokens.
  2. Small bonus for raw substring hits of name/description in the user text.
  3. Normalise to ``[0, 1]``; derive a 3-step confidence from top-1 score
     and the margin against top-2.
  4. If confidence < "high" and the caller permits, ask the LLM to break
     the tie. Any LLM failure falls back to the keyword-only ranking.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal, Optional

from report_skill import llm

Confidence = Literal["high", "medium", "low"]


@dataclass
class TemplateSuggestion:
    template_id: str
    template_name: str
    score: float                    # 0..1
    matched_keywords: list[str]     # the keywords that hit
    llm_reasoning: Optional[str]    # populated when LLM is used
    confidence: Confidence


# --------------------------------------------------------------------------- #
# Canned vocab — covers the 8 seeded templates
# --------------------------------------------------------------------------- #
CANNED_VOCAB: dict[str, list[str]] = {
    "weekly-dev": [
        "주간", "이번 주", "weekly", "sprint", "스프린트",
        "주간보고", "주간 보고", "개발", "dev",
    ],
    "weekly-biz": [
        "주간", "weekly", "영업", "sales", "pipeline",
        "파이프라인", "매출", "수주",
    ],
    "monthly-summary": [
        "월간", "monthly", "이번 달", "분기", "월말", "달 정리",
    ],
    "rfc": [
        "rfc", "request for comments", "기술 검토", "설계서",
        "제안서", "설계", "design doc", "spec", "스펙",
    ],
    "adr": [
        "adr", "architecture decision", "결정 기록",
        "decision record", "선택 근거",
    ],
    "incident": [
        "인시던트", "장애", "사고", "incident", "outage",
        "postmortem", "포스트모템", "post-mortem",
    ],
    "release-note": [
        "릴리스", "release", "배포", "deploy", "버전",
        "version", "changelog", "변경사항",
    ],
    "meeting-decision": [
        "회의", "meeting", "회의록", "결정", "decision",
        "논의", "minutes", "ad hoc",
    ],
}

# Tuning knobs
_AMBIGUOUS_MARGIN = 0.20    # top1-top2 gap under this triggers LLM in "auto"
_HIGH_SCORE_FLOOR = 0.40    # top1 must clear this to qualify for "high"
_LOW_MARGIN = 0.05          # under this margin we fall to "low"
_LOW_SCORE_FLOOR = 0.10     # very weak top score → "low" regardless of margin
_NAME_DESC_BONUS = 0.15     # added per matching name/description substring (pre-clip)

_TOKEN_RE = re.compile(r"[\w가-힣\-]+", re.UNICODE)


# --------------------------------------------------------------------------- #
# Scoring helpers
# --------------------------------------------------------------------------- #
def _count_keyword_hits(text_lower: str, keywords: Iterable[str]) -> list[str]:
    hits: list[str] = []
    seen: set[str] = set()
    for kw in keywords:
        k = kw.lower().strip()
        if not k or k in seen:
            continue
        if k in text_lower:
            hits.append(kw)
            seen.add(k)
    return hits


def _fallback_tokens(name: str, description: str) -> list[str]:
    """Build a keyword list from name+description for templates not in CANNED_VOCAB."""
    blob = f"{name} {description}".lower()
    toks = [t for t in _TOKEN_RE.findall(blob) if len(t) >= 2 and not t.isdigit()]
    seen: set[str] = set()
    out: list[str] = []
    for t in toks:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _score_template(text_lower: str, template: dict) -> tuple[float, list[str]]:
    tid = str(template.get("template_id") or template.get("id") or "")
    name = str(template.get("name") or "")
    desc = str(template.get("description") or "")

    vocab = CANNED_VOCAB.get(tid)
    if vocab:
        matched = _count_keyword_hits(text_lower, vocab)
        base = len(matched) / max(len(vocab), 1)
    else:
        tokens = _fallback_tokens(name, desc)
        if not tokens:
            return 0.0, []
        matched = _count_keyword_hits(text_lower, tokens)
        base = len(matched) / max(len(tokens), 1)

    bonus = 0.0
    for needle in (name, desc):
        n = needle.lower().strip()
        if n and len(n) >= 3 and n in text_lower:
            bonus += _NAME_DESC_BONUS

    return min(1.0, base + bonus), matched


def _confidence_for(top_score: float, margin: float) -> Confidence:
    if top_score < _LOW_SCORE_FLOOR or margin < _LOW_MARGIN:
        return "low"
    if top_score >= _HIGH_SCORE_FLOOR and margin >= _AMBIGUOUS_MARGIN:
        return "high"
    return "medium"


# --------------------------------------------------------------------------- #
# LLM tie-break
# --------------------------------------------------------------------------- #
def _build_llm_messages(user_text: str, candidates: list[TemplateSuggestion],
                        templates_by_id: dict[str, dict]) -> list[dict]:
    snippet = user_text.strip()[:800]
    lines = []
    for c in candidates:
        tpl = templates_by_id.get(c.template_id, {})
        desc = str(tpl.get("description") or "").strip().replace("\n", " ")
        lines.append(f"- {c.template_id}: {c.template_name} - {desc}")
    user_msg = (
        'User wrote:\n"""\n' + snippet + '\n"""\n\n'
        "Candidate templates:\n" + "\n".join(lines) + "\n\n"
        "Pick the single best match. Reply with exactly one JSON object:\n"
        '{"template_id": "<id>", "reason": "<one short sentence>"}\n\n'
        "If none fit well, reply:\n"
        '{"template_id": null, "reason": "<one short sentence>"}'
    )
    return [
        {"role": "system", "content": "You match user notes to the best report template."},
        {"role": "user",   "content": user_msg},
    ]


def _llm_tie_break(user_text: str, top: list[TemplateSuggestion],
                   templates_by_id: dict[str, dict]) -> Optional[tuple[str, str]]:
    """Return (template_id, reason) chosen by the LLM, or None on any failure."""
    try:
        provider = llm.get_provider()
        raw = provider.generate(
            _build_llm_messages(user_text, top, templates_by_id),
            json_mode=True,
            max_tokens=200,
        )
        data = llm.extract_json(raw)
    except llm.LLMError:
        return None
    except Exception:  # noqa: BLE001 - provider may raise anything; treat as soft-fail
        return None
    if not isinstance(data, dict):
        return None
    tid = data.get("template_id")
    if not tid or not isinstance(tid, str):
        return None
    return tid, str(data.get("reason") or "").strip()


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def suggest_templates(
    user_text: str,
    *,
    templates: Optional[list[dict]] = None,
    top_k: int = 3,
    use_llm: Literal["auto", "never", "always"] = "auto",
) -> list[TemplateSuggestion]:
    """Return top-K templates ranked by relevance to ``user_text``.

    Always returns exactly ``top_k`` entries (padded with empty score-0
    placeholders if the catalog is smaller).
    """
    top_k = max(1, int(top_k))
    text_lower = (user_text or "").lower()

    if templates is None:
        try:
            from report_skill.client import ReportArchiveClient as _Client
            with _Client() as cli:
                templates = cli.fetch_templates()
        except Exception:  # noqa: BLE001 - any client/network failure → empty catalog
            templates = []
    templates = list(templates or [])

    scored: list[TemplateSuggestion] = []
    for tpl in templates:
        tid = str(tpl.get("template_id") or tpl.get("id") or "")
        if not tid:
            continue
        name = str(tpl.get("name") or tid)
        score, matched = _score_template(text_lower, tpl) if text_lower else (0.0, [])
        scored.append(TemplateSuggestion(
            template_id=tid,
            template_name=name,
            score=round(score, 4),
            matched_keywords=matched,
            llm_reasoning=None,
            confidence="low",
        ))

    # Sort by score desc, then id for deterministic ordering on ties.
    scored.sort(key=lambda s: (-s.score, s.template_id))
    top = scored[:top_k]

    if top:
        top1 = top[0].score
        top2 = top[1].score if len(top) > 1 else 0.0
        margin = top1 - top2
        top[0].confidence = _confidence_for(top1, margin)
    else:
        margin = 0.0

    should_call_llm = (
        use_llm == "always"
        or (use_llm == "auto" and len(top) >= 2 and top[0].confidence != "high"
            and margin < _AMBIGUOUS_MARGIN)
    )
    if should_call_llm and top and text_lower:
        by_id = {
            str(t.get("template_id") or t.get("id") or ""): t
            for t in templates
        }
        result = _llm_tie_break(user_text, top, by_id)
        if result is not None:
            picked_id, reason = result
            picked_idx = next((i for i, s in enumerate(top) if s.template_id == picked_id), None)
            if picked_idx is not None and picked_idx != 0:
                promoted = top.pop(picked_idx)
                promoted.llm_reasoning = reason or "LLM selected this template."
                promoted.confidence = "medium"
                top.insert(0, promoted)
            elif picked_idx == 0:
                top[0].llm_reasoning = reason or "LLM confirmed keyword ranking."
                if top[0].confidence == "low":
                    top[0].confidence = "medium"

    while len(top) < top_k:
        top.append(TemplateSuggestion(
            template_id="",
            template_name="",
            score=0.0,
            matched_keywords=[],
            llm_reasoning=None,
            confidence="low",
        ))
    return top
