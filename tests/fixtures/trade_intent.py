"""Deterministic TradeIntent fixtures for controlled paper fill testing.

These fixtures bypass the full decision pipeline to directly test
paper execution adapter behavior with known inputs.
"""
from __future__ import annotations

from datetime import UTC, datetime

from src.core.enums import (
    ConfidenceTier,
    Direction,
    HTFAgreement,
    Regime,
    Session,
    SetupClass,
    StructureType,
)
from src.core.models import (
    Bar,
    ConfluenceResult,
    ContextSnapshot,
    DetectedStructure,
    ExitPlan,
    RiskVerdict,
    TradeIntent,
)


def make_trade_intent(
    *,
    side: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    lot_size: float,
    decision_id: str = "test_decision_001",
    symbol: str = "EURUSD",
    spread: float = 0.00002,
) -> TradeIntent:
    """Build a deterministic TradeIntent for paper fill testing.

    Args:
        side: "BUY" or "SELL"
        entry_price: entry price for the trade
        stop_loss: stop loss price
        take_profit: take profit price
        lot_size: position size in lots
        decision_id: unique decision identifier
        symbol: trading symbol
        spread: current spread (for context only)
    """
    direction = Direction.BULLISH if side == "BUY" else Direction.BEARISH

    trigger = DetectedStructure(
        structure_type=StructureType.ORDER_BLOCK,
        direction=direction,
        price_high=entry_price + 0.0005,
        price_low=entry_price - 0.0005,
        quality=0.9,
        age_bars=5,
        atr_relative_size=2.0,
        timeframe="M15",
        bar_index=100,
        bar_time=datetime(2026, 4, 30, 8, 0, tzinfo=UTC),
    )

    exit_plan = ExitPlan(
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_reward=abs(take_profit - entry_price) / abs(entry_price - stop_loss),
        sl_source="STRUCTURE",
        tp_source="STRUCTURE",
        breakeven_trigger_r=1.0,
        session_close_exit=True,
    )

    risk_verdict = RiskVerdict(
        approved=True,
        lot_size=lot_size,
        actual_risk_pct=0.4,
        intended_risk_pct=0.4,
        reason="approved",
    )

    confluence = ConfluenceResult(
        setup_class=SetupClass.OB_WITH_BOS,
        direction=direction,
        primary_trigger=trigger,
        structural_confirmations=[trigger],
        structural_labels=["OB", "BOS"],
        minor_confluences=[],
        hard_rejects=[],
        soft_penalties=[],
        structural_count=2,
        minor_count=0,
        quality_penalty=0.0,
        effective_quality=0.85,
        confluence_pass=True,
        confidence_tier=ConfidenceTier.A,
        tier_reason="strong_confluence",
    )

    context = ContextSnapshot(
        symbol=symbol,
        bar_time=datetime(2026, 4, 30, 8, 0, tzinfo=UTC),
        session=Session.LONDON,
        micro_window=False,
        trend_m15=direction,
        trend_h1=direction,
        htf_agreement=HTFAgreement.AGREES,
        regime=Regime.TRENDING,
        atr_current=0.001,
        atr_percentile=0.5,
        spread_atr_ratio=spread / 0.001,
        stale_entry=False,
        news_blocked=False,
        nearby_structures=[trigger],
    )

    return TradeIntent(
        trade_id=decision_id,
        symbol=symbol,
        direction=direction,
        setup_class=SetupClass.OB_WITH_BOS,
        confidence_tier=ConfidenceTier.A,
        session=Session.LONDON,
        entry_price=entry_price,
        exit_plan=exit_plan,
        risk_verdict=risk_verdict,
        confluence=confluence,
        context=context,
        config_hash="test_cfg_001",
        bar_time=datetime(2026, 4, 30, 8, 0, tzinfo=UTC),
    )
