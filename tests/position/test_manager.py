from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from src.core.enums import ConfidenceTier, Direction, HTFAgreement, Regime, Session, SetupClass, StructureType, Timeframe
from src.core.models import ConfluenceResult, ContextSnapshot, DetectedStructure, ExitPlan, RiskVerdict
from src.position.manager import build_trade_intent


def _config() -> dict:
    return json.loads(Path("src/config/defaults.json").read_text(encoding="utf-8"))


def _context() -> ContextSnapshot:
    return ContextSnapshot(
        symbol="EURUSD",
        bar_time=datetime(2026, 4, 30, 8, 0, tzinfo=UTC),
        session=Session.LONDON,
        micro_window=False,
        trend_m15=Direction.BULLISH,
        trend_h1=Direction.BULLISH,
        htf_agreement=HTFAgreement.AGREES,
        regime=Regime.TRENDING,
        atr_current=0.001,
        atr_percentile=0.5,
        spread_atr_ratio=0.1,
        stale_entry=False,
        news_blocked=False,
        nearby_structures=[],
    )


def _structure(direction: Direction) -> DetectedStructure:
    return DetectedStructure(
        structure_type=StructureType.ORDER_BLOCK,
        direction=direction,
        price_high=1.1010,
        price_low=1.0990,
        quality=0.9,
        age_bars=1,
        atr_relative_size=0.7,
        timeframe=Timeframe.M15,
        bar_index=10,
        bar_time=datetime(2026, 4, 30, 8, 0, tzinfo=UTC),
        metadata={},
    )


def _confluence() -> ConfluenceResult:
    trigger = _structure(Direction.BULLISH)
    confirmation = DetectedStructure(
        structure_type=StructureType.BREAK_OF_STRUCTURE,
        direction=Direction.BULLISH,
        price_high=1.1020,
        price_low=1.1005,
        quality=0.8,
        age_bars=1,
        atr_relative_size=0.8,
        timeframe=Timeframe.H1,
        bar_index=11,
        bar_time=datetime(2026, 4, 30, 8, 0, tzinfo=UTC),
        metadata={},
    )
    return ConfluenceResult(
        setup_class=SetupClass.OB_WITH_BOS,
        direction=Direction.BULLISH,
        primary_trigger=trigger,
        structural_confirmations=[confirmation],
        structural_labels=["ORDER_BLOCK", "BREAK_OF_STRUCTURE"],
        minor_confluences=[],
        hard_rejects=[],
        soft_penalties=[],
        structural_count=2,
        minor_count=0,
        quality_penalty=0.0,
        effective_quality=0.9,
        confluence_pass=True,
        confidence_tier=ConfidenceTier.A,
        tier_reason="tier_a_confirmations",
    )


def _exit_plan() -> ExitPlan:
    return ExitPlan(
        stop_loss=1.0975,
        take_profit=1.1025,
        risk_reward=1.4,
        sl_source="ORDER_BLOCK",
        tp_source="M15_STRUCTURE",
        breakeven_trigger_r=1.0,
        session_close_exit=True,
    )


def test_build_trade_intent_approved() -> None:
    verdict = build_trade_intent(
        context=_context(),
        confluence=_confluence(),
        exit_plan=_exit_plan(),
        risk_verdict=RiskVerdict(True, 1.0, 0.4, 0.4, "approved"),
        entry_price=1.0990,
        config=_config(),
    )

    assert verdict.approved is True
    assert verdict.reason == "approved"
    assert verdict.trade_intent is not None
    assert verdict.trade_intent.symbol == "EURUSD"


def test_build_trade_intent_rejects_non_approved_risk() -> None:
    verdict = build_trade_intent(
        context=_context(),
        confluence=_confluence(),
        exit_plan=_exit_plan(),
        risk_verdict=RiskVerdict(False, 0.0, 0.0, 0.4, "max_open_positions_total"),
        entry_price=1.0990,
        config=_config(),
    )

    assert verdict.approved is False
    assert verdict.reason == "risk_not_approved"
    assert verdict.trade_intent is None


def test_build_trade_intent_rejects_zero_lot_size() -> None:
    verdict = build_trade_intent(
        context=_context(),
        confluence=_confluence(),
        exit_plan=_exit_plan(),
        risk_verdict=RiskVerdict(True, 0.0, 0.4, 0.4, "approved"),
        entry_price=1.0990,
        config=_config(),
    )

    assert verdict.approved is False
    assert verdict.reason == "invalid_lot_size"


def test_build_trade_intent_approved_with_partials_enabled() -> None:
    """partials_enabled=True must NOT block trade approval.

    Regression test for the inverted partials guard removed from
    build_trade_intent (fix: task #46). Previously the function returned
    partials_not_supported_phase1 whenever partials_enabled was True, which
    killed every Tier A setup on live_market_watch.json where partials are on.
    """
    cfg = _config()
    cfg["exits"] = dict(cfg["exits"])
    cfg["exits"]["management"] = dict(cfg["exits"].get("management", {}))
    cfg["exits"]["management"]["partials_enabled"] = True

    verdict = build_trade_intent(
        context=_context(),
        confluence=_confluence(),
        exit_plan=_exit_plan(),
        risk_verdict=RiskVerdict(True, 1.0, 0.4, 0.4, "approved"),
        entry_price=1.0990,
        config=cfg,
    )

    assert verdict.approved is True
    assert verdict.reason == "approved"
    assert verdict.trade_intent is not None


def test_build_trade_intent_approved_with_partials_disabled() -> None:
    """partials_enabled=False must also approve normally."""
    cfg = _config()
    cfg["exits"] = dict(cfg["exits"])
    cfg["exits"]["management"] = dict(cfg["exits"].get("management", {}))
    cfg["exits"]["management"]["partials_enabled"] = False

    verdict = build_trade_intent(
        context=_context(),
        confluence=_confluence(),
        exit_plan=_exit_plan(),
        risk_verdict=RiskVerdict(True, 1.0, 0.4, 0.4, "approved"),
        entry_price=1.0990,
        config=cfg,
    )

    assert verdict.approved is True
    assert verdict.reason == "approved"
    assert verdict.trade_intent is not None


def test_build_trade_intent_never_returns_partials_not_supported() -> None:
    """build_trade_intent must never return partials_not_supported_phase1.

    The reason string was the signature of the removed Phase 1 guard.
    If it ever reappears it means the guard was accidentally re-added.
    """
    for partials_flag in (True, False):
        cfg = _config()
        cfg["exits"] = dict(cfg["exits"])
        cfg["exits"]["management"] = dict(cfg["exits"].get("management", {}))
        cfg["exits"]["management"]["partials_enabled"] = partials_flag

        verdict = build_trade_intent(
            context=_context(),
            confluence=_confluence(),
            exit_plan=_exit_plan(),
            risk_verdict=RiskVerdict(True, 1.0, 0.4, 0.4, "approved"),
            entry_price=1.0990,
            config=cfg,
        )

        assert verdict.reason != "partials_not_supported_phase1", (
            "partials_not_supported_phase1 guard must not exist "
            "(partials_enabled={})".format(partials_flag)
        )
