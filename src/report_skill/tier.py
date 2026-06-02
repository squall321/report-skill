"""Adaptive AI tier — S (premium) / M (mid) / W (weak local).

The skill's orchestration changes with tier:
  S — entire page in 1-2 LLM calls
  M — sections in 3-5 blocks per call
  W — one block per call, heavy schema-constrained prompting, more aggressive
      repair attempts, deeper fallback chains

Detection
---------
Auto-detection runs a tiny probe: ask the model to emit a minimal JSON
shape ({a: 1, b: [\"x\"]}) twice — once asked plainly, once after we
inject a deliberately tricky enum constraint. Score:
  - both valid JSON, both schema-correct → S
  - first valid, second has near-miss enum → M
  - first valid, second wildly off OR JSON parse error → W

The result is cached to `.skill-cache/tier.json` for the rest of the
session. User can override with `SKILL_AI_TIER=S|M|W` in `.env`.

Right now this module is a stub: it exposes the data shape and a
`current_tier()` function that returns the override or "M" by default.
The actual LLM probe lands in P4 when the model client wires in.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from report_skill.config import CACHE_DIR, settings

Tier = Literal["S", "M", "W"]
TIER_FILE = CACHE_DIR / "tier.json"


@dataclass
class TierProfile:
    tier: Tier
    detected_at: str
    source: str             # "override" | "probe" | "default"
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TierPolicy:
    """Per-tier orchestration knobs read by orchestrator / prompt builder."""
    blocks_per_call: int    # 1=W, 3-5=M, 8+=S
    max_repair_passes: int  # how many deterministic repair iterations
    enable_llm_retry: bool  # ask LLM to fix its own output on validation failure
    max_llm_retries: int
    aggressive_enum_match: bool   # accept distant Levenshtein matches in select cols
    fallback_chain_depth: int     # 0 = no widget swap; 1 = one fallback; 2 = full chain

    @classmethod
    def for_tier(cls, t: Tier) -> "TierPolicy":
        if t == "S":
            return cls(blocks_per_call=8, max_repair_passes=1, enable_llm_retry=True,
                       max_llm_retries=1, aggressive_enum_match=False,
                       fallback_chain_depth=1)
        if t == "M":
            return cls(blocks_per_call=4, max_repair_passes=2, enable_llm_retry=True,
                       max_llm_retries=2, aggressive_enum_match=True,
                       fallback_chain_depth=2)
        # W
        return cls(blocks_per_call=1, max_repair_passes=3, enable_llm_retry=True,
                   max_llm_retries=3, aggressive_enum_match=True,
                   fallback_chain_depth=2)


def current_tier() -> TierProfile:
    """Return the active tier. Order: env override → cached probe → default."""
    if settings.skill_ai_tier:
        return TierProfile(
            tier=settings.skill_ai_tier,
            detected_at=datetime.now(timezone.utc).isoformat(),
            source="override",
            notes="set by SKILL_AI_TIER env",
        )

    cached = _load_cached()
    if cached:
        return cached

    return TierProfile(
        tier="M",
        detected_at=datetime.now(timezone.utc).isoformat(),
        source="default",
        notes="no probe run yet; using safe middle default",
    )


def policy() -> TierPolicy:
    return TierPolicy.for_tier(current_tier().tier)


def set_tier(t: Tier, *, source: str = "manual", notes: str = "") -> TierProfile:
    """Persist a tier choice to cache so subsequent CLI runs reuse it."""
    profile = TierProfile(
        tier=t,
        detected_at=datetime.now(timezone.utc).isoformat(),
        source=source,
        notes=notes,
    )
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    TIER_FILE.write_text(json.dumps(profile.to_dict(), indent=2), encoding="utf-8")
    return profile


def _load_cached() -> Optional[TierProfile]:
    if not TIER_FILE.is_file():
        return None
    try:
        data = json.loads(TIER_FILE.read_text(encoding="utf-8"))
        return TierProfile(**data)
    except Exception:
        return None
