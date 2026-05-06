from __future__ import annotations

from src.core.enums import Direction, HTFAgreement, Regime
from src.core.models import ContextSnapshot


def evaluate_hard_rejects(context: ContextSnapshot, candidate_direction: Direction, block_ranging_regime: bool) -> list[str]:
    rejects: list[str] = []

    if context.regime == Regime.EXPANDING:
        rejects.append("expanding_regime_hard_reject")

    if block_ranging_regime and context.regime == Regime.RANGING:
        rejects.append("ranging_regime_block")

    if context.htf_agreement == HTFAgreement.CONTRADICTS:
        rejects.append("h1_contradiction")

    if context.trend_h1 == Direction.NEUTRAL:
        rejects.append("h1_neutral_gate")

    if context.news_blocked:
        rejects.append("news_blocked")

    if context.stale_entry:
        rejects.append("stale_entry")

    if context.trend_h1 != Direction.NEUTRAL and context.trend_h1 != candidate_direction:
        if "h1_contradiction" not in rejects:
            rejects.append("h1_contradiction")

    return sorted(set(rejects))


def evaluate_soft_penalties(context: ContextSnapshot, candidate_direction: Direction) -> list[str]:
    penalties: list[str] = []
    if context.trend_m15 != candidate_direction:
        penalties.append("m15_trend_mismatch")
    if context.spread_atr_ratio > 0.25:
        penalties.append("spread_elevated")
    if context.micro_window:
        penalties.append("micro_window")
    return penalties
