from __future__ import annotations

from dataclasses import dataclass

from src.core.enums import ConfidenceTier, Direction, SetupClass, StructureType, Timeframe
from src.core.models import ConfluenceResult, ContextSnapshot, DetectedStructure
from src.decision.contradictions import evaluate_hard_rejects, evaluate_soft_penalties
from src.decision.setup_rules import SetupCandidate


STRUCTURAL_LABELS: dict[StructureType, str] = {
    StructureType.ORDER_BLOCK: "order_block_alignment",
    StructureType.BREAK_OF_STRUCTURE: "bos_confirmation",
    StructureType.ENGULFING: "engulfing_confirmation",
    StructureType.FAIR_VALUE_GAP: "fvg_confirmation",
    StructureType.REJECTION: "rejection_confirmation",
    StructureType.LIQUIDITY_SWEEP: "liquidity_sweep_confirmation",
}


@dataclass(frozen=True)
class ConfluenceConfig:
    tier_a_min_confirmations: int
    tier_b_min_confirmations: int
    tier_c_min_confirmations: int
    tier_c_tradable: bool
    triple_penalty_quality_floor: float
    block_ranging_regime: bool



def _minor_confluences(context: ContextSnapshot, confirmations: list[DetectedStructure]) -> list[str]:
    tags: list[str] = []
    if context.trend_m15 == context.trend_h1 and context.trend_h1 != Direction.NEUTRAL:
        tags.append("trend_alignment")
    if context.spread_atr_ratio <= 0.15:
        tags.append("spread_ok")
    if any(item.timeframe == Timeframe.H1 for item in confirmations):
        tags.append("h1_participation")
    return tags


def _label_structures(items: list[DetectedStructure]) -> list[str]:
    return [STRUCTURAL_LABELS.get(item.structure_type, "structure_confirmation") for item in items]


def _tier_for_count(count: int, cfg: ConfluenceConfig) -> ConfidenceTier:
    if count >= cfg.tier_a_min_confirmations:
        return ConfidenceTier.A
    if count >= cfg.tier_b_min_confirmations:
        return ConfidenceTier.B
    return ConfidenceTier.C


def _tier_reason(tier: ConfidenceTier) -> str:
    if tier == ConfidenceTier.A:
        return "tier_a_confirmations"
    if tier == ConfidenceTier.B:
        return "tier_b_confirmations"
    return "tier_c_confirmations"


def evaluate_confluence(
    candidate: SetupCandidate,
    context: ContextSnapshot,
    config: ConfluenceConfig,
) -> ConfluenceResult:
    confirmations = [candidate.primary_trigger, *candidate.structural_confirmations]
    structural_labels = _label_structures(confirmations)
    minor = _minor_confluences(context, confirmations)

    hard_rejects = evaluate_hard_rejects(
        context=context,
        candidate_direction=candidate.direction,
        block_ranging_regime=config.block_ranging_regime,
    )
    soft_penalties = evaluate_soft_penalties(context=context, candidate_direction=candidate.direction)
    if context.atr_percentile < 0.25:
        soft_penalties.append("low_volatility")

    quality_penalty = min(0.15 * len(soft_penalties), 0.50)
    base_quality = min(max(candidate.primary_trigger.quality, 0.0), 1.0)
    effective_quality = round(max(base_quality - quality_penalty, 0.0), 3)

    structural_count = len(confirmations)
    minor_count = len(minor)

    tier = _tier_for_count(structural_count, config)
    tier_reason = _tier_reason(tier)

    if tier == ConfidenceTier.C and not config.tier_c_tradable:
        hard_rejects.append("tier_c_blocked")

    h1_participation = any(item.timeframe == Timeframe.H1 for item in confirmations)
    if tier == ConfidenceTier.B and not h1_participation:
        hard_rejects.append("tier_b_requires_h1_participation")

    if len(soft_penalties) >= 3 and effective_quality < config.triple_penalty_quality_floor:
        hard_rejects.append("triple_penalty_quality_floor")

    confluence_pass = len(hard_rejects) == 0

    return ConfluenceResult(
        setup_class=candidate.setup_class,
        direction=candidate.direction,
        primary_trigger=candidate.primary_trigger,
        structural_confirmations=candidate.structural_confirmations,
        structural_labels=structural_labels,
        minor_confluences=minor,
        hard_rejects=sorted(set(hard_rejects)),
        soft_penalties=soft_penalties,
        structural_count=structural_count,
        minor_count=minor_count,
        quality_penalty=quality_penalty,
        effective_quality=effective_quality,
        confluence_pass=confluence_pass,
        confidence_tier=tier,
        tier_reason=tier_reason,
    )
