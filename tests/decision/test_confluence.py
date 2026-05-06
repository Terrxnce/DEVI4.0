from __future__ import annotations

from src.core.enums import ConfidenceTier, Direction, HTFAgreement, Regime, SetupClass, StructureType, Timeframe
from src.decision.confluence import ConfluenceConfig, evaluate_confluence
from src.decision.setup_rules import SetupCandidate



def _cfg(default_config: dict) -> ConfluenceConfig:
    c = default_config["confluence"]
    return ConfluenceConfig(
        tier_a_min_confirmations=int(c["tier_a_min_confirmations"]),
        tier_b_min_confirmations=int(c["tier_b_min_confirmations"]),
        tier_c_min_confirmations=int(c["tier_c_min_confirmations"]),
        tier_c_tradable=bool(c["tier_c_tradable"]),
        triple_penalty_quality_floor=float(c["triple_penalty_quality_floor"]),
        block_ranging_regime=bool(c["block_ranging_regime"]),
    )



def test_tier_a_classification(make_structure_fn, make_context_fn, default_config) -> None:
    primary = make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, quality=0.9)
    confirmations = [
        make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BULLISH, quality=0.8, bar_index=11),
        make_structure_fn(StructureType.FAIR_VALUE_GAP, Direction.BULLISH, quality=0.75, bar_index=12),
    ]
    cand = SetupCandidate(SetupClass.OB_WITH_BOS, Direction.BULLISH, primary, confirmations)
    context = make_context_fn()

    result = evaluate_confluence(cand, context, _cfg(default_config))

    assert result.confidence_tier == ConfidenceTier.A


def test_tier_b_classification(make_structure_fn, make_context_fn, default_config) -> None:
    primary = make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, quality=0.9)
    confirmations = [
        make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BULLISH, quality=0.8, timeframe=Timeframe.H1),
    ]
    cand = SetupCandidate(SetupClass.OB_WITH_BOS, Direction.BULLISH, primary, confirmations)
    context = make_context_fn()

    result = evaluate_confluence(cand, context, _cfg(default_config))

    assert result.confidence_tier == ConfidenceTier.B
    assert "tier_b_requires_h1_participation" not in result.hard_rejects


def test_tier_c_blocked(make_structure_fn, make_context_fn, default_config) -> None:
    primary = make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, quality=0.9)
    cand = SetupCandidate(SetupClass.OB_WITH_BOS, Direction.BULLISH, primary, [])
    context = make_context_fn()

    result = evaluate_confluence(cand, context, _cfg(default_config))

    assert result.confidence_tier == ConfidenceTier.C
    assert "tier_c_blocked" in result.hard_rejects


def test_h1_contradiction_hard_reject(make_structure_fn, make_context_fn, default_config) -> None:
    primary = make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, quality=0.9)
    confirmations = [make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BULLISH, quality=0.8, timeframe=Timeframe.H1)]
    cand = SetupCandidate(SetupClass.OB_WITH_BOS, Direction.BULLISH, primary, confirmations)
    context = make_context_fn(htf_agreement=HTFAgreement.CONTRADICTS)

    result = evaluate_confluence(cand, context, _cfg(default_config))

    assert "h1_contradiction" in result.hard_rejects


def test_ranging_hard_block(make_structure_fn, make_context_fn, default_config) -> None:
    primary = make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, quality=0.9)
    confirmations = [make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BULLISH, quality=0.8, timeframe=Timeframe.H1)]
    cand = SetupCandidate(SetupClass.OB_WITH_BOS, Direction.BULLISH, primary, confirmations)
    context = make_context_fn(regime=Regime.RANGING)

    result = evaluate_confluence(cand, context, _cfg(default_config))

    assert "ranging_regime_block" in result.hard_rejects


def test_expanding_hard_reject(make_structure_fn, make_context_fn, default_config) -> None:
    primary = make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, quality=0.9)
    confirmations = [make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BULLISH, quality=0.8, timeframe=Timeframe.H1)]
    cand = SetupCandidate(SetupClass.OB_WITH_BOS, Direction.BULLISH, primary, confirmations)
    context = make_context_fn(regime=Regime.EXPANDING)

    result = evaluate_confluence(cand, context, _cfg(default_config))

    assert "expanding_regime_hard_reject" in result.hard_rejects


def test_h1_neutral_gate(make_structure_fn, make_context_fn, default_config) -> None:
    primary = make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, quality=0.9)
    confirmations = [make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BULLISH, quality=0.8, timeframe=Timeframe.H1)]
    cand = SetupCandidate(SetupClass.OB_WITH_BOS, Direction.BULLISH, primary, confirmations)
    context = make_context_fn(trend_h1=Direction.NEUTRAL)

    result = evaluate_confluence(cand, context, _cfg(default_config))

    assert "h1_neutral_gate" in result.hard_rejects


def test_tier_b_without_h1_participation_blocked(make_structure_fn, make_context_fn, default_config) -> None:
    primary = make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, quality=0.9)
    confirmations = [make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BULLISH, quality=0.8, timeframe=Timeframe.M15)]
    cand = SetupCandidate(SetupClass.OB_WITH_BOS, Direction.BULLISH, primary, confirmations)
    context = make_context_fn()

    result = evaluate_confluence(cand, context, _cfg(default_config))

    assert result.confidence_tier == ConfidenceTier.B
    assert "tier_b_requires_h1_participation" in result.hard_rejects


def test_triple_penalty_quality_floor(make_structure_fn, make_context_fn, default_config) -> None:
    primary = make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, quality=0.75)
    confirmations = [make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BULLISH, quality=0.6, timeframe=Timeframe.H1)]
    cand = SetupCandidate(SetupClass.OB_WITH_BOS, Direction.BULLISH, primary, confirmations)
    context = make_context_fn(
        trend_m15=Direction.BEARISH,
        spread_atr_ratio=0.3,
        micro_window=True,
        atr_percentile=0.2,
    )

    result = evaluate_confluence(cand, context, _cfg(default_config))

    assert "triple_penalty_quality_floor" in result.hard_rejects


def test_effective_quality_rounding_and_penalty_cap(make_structure_fn, make_context_fn, default_config) -> None:
    primary = make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, quality=0.9876)
    confirmations = [make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BULLISH, quality=0.8, timeframe=Timeframe.H1)]
    cand = SetupCandidate(SetupClass.OB_WITH_BOS, Direction.BULLISH, primary, confirmations)
    context = make_context_fn(
        trend_m15=Direction.BEARISH,
        spread_atr_ratio=0.5,
        micro_window=True,
        atr_percentile=0.1,
    )

    result = evaluate_confluence(cand, context, _cfg(default_config))

    assert result.quality_penalty == 0.5
    assert result.effective_quality == round(result.effective_quality, 3)
