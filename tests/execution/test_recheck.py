"""Tests for pre-trade rechecks — 5 checks run before any execution attempt.

All tests use fake data sources (no MT5 calls).
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.core.enums import (
    ConfidenceTier,
    Direction,
    HTFAgreement,
    Regime,
    Session,
    SetupClass,
    StructureType,
    Timeframe,
)
from src.core.models import (
    ConfluenceResult,
    ContextSnapshot,
    DetectedStructure,
    ExitPlan,
    RiskVerdict,
    TradeIntent,
)
from src.execution.recheck import PreTradeRecheck, RecheckVerdict


def _make_trade_intent(
    *,
    entry_price: float = 1.1000,
    stop_loss: float = 1.0980,
    lot_size: float = 0.10,
) -> TradeIntent:
    return TradeIntent(
        trade_id="trade_001",
        symbol="EURUSD",
        direction=Direction.BULLISH,
        setup_class=SetupClass.OB_WITH_BOS,
        confidence_tier=ConfidenceTier.A,
        session=Session.LONDON,
        entry_price=entry_price,
        exit_plan=ExitPlan(
            stop_loss=stop_loss,
            take_profit=1.1040,
            risk_reward=2.0,
            sl_source="structure",
            tp_source="structure",
            breakeven_trigger_r=1.0,
            session_close_exit=False,
        ),
        risk_verdict=RiskVerdict(
            approved=True,
            lot_size=lot_size,
            actual_risk_pct=1.0,
            intended_risk_pct=1.0,
            reason="",
        ),
        confluence=ConfluenceResult(
            setup_class=SetupClass.OB_WITH_BOS,
            direction=Direction.BULLISH,
            primary_trigger=DetectedStructure(
                structure_type=StructureType.ORDER_BLOCK,
                direction=Direction.BULLISH,
                price_high=1.1010,
                price_low=1.0990,
                quality=0.9,
                age_bars=1,
                atr_relative_size=1.0,
                timeframe=Timeframe.M15,
                bar_index=10,
                bar_time=datetime.now(tz=UTC),
            ),
            structural_confirmations=[],
            structural_labels=[],
            minor_confluences=[],
            hard_rejects=[],
            soft_penalties=[],
            structural_count=1,
            minor_count=0,
            quality_penalty=0.0,
            effective_quality=0.9,
            confluence_pass=True,
            confidence_tier=ConfidenceTier.A,
            tier_reason="",
        ),
        context=ContextSnapshot(
            symbol="EURUSD",
            bar_time=datetime.now(tz=UTC),
            session=Session.LONDON,
            micro_window=False,
            trend_m15=Direction.BULLISH,
            trend_h1=Direction.BULLISH,
            htf_agreement=HTFAgreement.AGREES,
            regime=Regime.TRENDING,
            atr_current=0.0010,
            atr_percentile=0.5,
            spread_atr_ratio=0.1,
            stale_entry=False,
            news_blocked=False,
            nearby_structures=[],
        ),
        config_hash="cfg_hash",
        bar_time=datetime.now(tz=UTC),
    )


class _FakeDataSource:
    """Configurable fake data source for recheck testing."""

    def __init__(
        self,
        *,
        bid: float = 1.1000,
        ask: float = 1.1002,
        balance: float = 2000.0,
        equity: float = 10050.0,
        free_margin: float = 9000.0,
        trade_allowed: bool = True,
        trade_mode: int = 4,
        session_deals: bool = True,
        point: float = 1e-05,
        contract_size: float = 100000.0,
        lot_step: float = 0.01,
        min_lot: float = 0.01,
        max_lot: float = 100.0,
    ) -> None:
        self._tick = {"bid": bid, "ask": ask}
        self._account = {
            "balance": balance,
            "equity": equity,
            "free_margin": free_margin,
            "currency": "USD",
        }
        self._profile = {
            "point": point,
            "contract_size": contract_size,
            "lot_step": lot_step,
            "min_lot": min_lot,
            "max_lot": max_lot,
        }
        self._symbol = {
            "trade_allowed": trade_allowed,
            "trade_mode": trade_mode,
            "session_deals": session_deals,
        }

    def fetch_tick(self, symbol: str) -> dict:
        return self._tick

    def fetch_account_info(self) -> dict:
        return self._account

    def fetch_instrument_profile(self, symbol: str) -> dict:
        return self._profile

    def fetch_symbol_info(self, symbol: str) -> dict:
        return self._symbol


# --- Spread Recheck ---


def test_spread_passes_when_within_tolerance() -> None:
    ds = _FakeDataSource(bid=1.1000, ask=1.1002)  # spread = 0.0002
    recheck = PreTradeRecheck(data_source=ds)
    verdict = recheck.recheck_spread(_make_trade_intent(), decision_spread=0.0002)
    assert verdict.passed is True
    assert verdict.reason == ""


def test_spread_fails_when_widened() -> None:
    ds = _FakeDataSource(bid=1.1000, ask=1.1006)  # spread = 0.0006 (> 2 * 0.0002)
    recheck = PreTradeRecheck(data_source=ds)
    verdict = recheck.recheck_spread(_make_trade_intent(), decision_spread=0.0002)
    assert verdict.passed is False
    assert "spread_widened" in verdict.reason


def test_spread_fails_when_zero() -> None:
    ds = _FakeDataSource(bid=1.1000, ask=1.1000)  # spread = 0
    recheck = PreTradeRecheck(data_source=ds)
    verdict = recheck.recheck_spread(_make_trade_intent(), decision_spread=0.0002)
    assert verdict.passed is False
    assert verdict.reason == "spread_zero"


# --- Account Recheck ---


def test_account_passes_with_healthy_balance() -> None:
    ds = _FakeDataSource(balance=10000.0, equity=10050.0, free_margin=9000.0)
    recheck = PreTradeRecheck(data_source=ds)
    verdict = recheck.recheck_account(_make_trade_intent())
    assert verdict.passed is True
    assert verdict.reason == ""


def test_account_fails_when_balance_zero() -> None:
    ds = _FakeDataSource(balance=0.0, equity=0.0)
    recheck = PreTradeRecheck(data_source=ds)
    verdict = recheck.recheck_account(_make_trade_intent())
    assert verdict.passed is False
    assert verdict.reason == "account_balance_zero"


def test_account_fails_when_equity_low() -> None:
    ds = _FakeDataSource(balance=10000.0, equity=4000.0, free_margin=9000.0)
    recheck = PreTradeRecheck(data_source=ds)
    verdict = recheck.recheck_account(_make_trade_intent())
    assert verdict.passed is False
    assert "equity_below_threshold" in verdict.reason


def test_account_fails_when_insufficient_margin() -> None:
    ds = _FakeDataSource(balance=10000.0, equity=10000.0, free_margin=10.0)
    recheck = PreTradeRecheck(data_source=ds)
    verdict = recheck.recheck_account(_make_trade_intent(lot_size=1.0))
    assert verdict.passed is False
    assert "insufficient_margin" in verdict.reason


# --- Risk Recheck ---


def test_risk_passes_when_lot_unchanged() -> None:
    # Balance 10000 -> risk 100 -> raw lot 0.50; use lot_size=0.50 to match
    ds = _FakeDataSource(balance=10000.0)
    recheck = PreTradeRecheck(data_source=ds)
    verdict = recheck.recheck_risk(_make_trade_intent(lot_size=0.50))
    assert verdict.passed is True
    assert verdict.reason == ""


def test_risk_fails_when_lot_deviates_too_much() -> None:
    ds = _FakeDataSource(balance=50000.0)  # much larger balance -> larger lot
    recheck = PreTradeRecheck(data_source=ds)
    intent = _make_trade_intent(lot_size=0.10)  # small lot, but large balance wants more
    verdict = recheck.recheck_risk(intent, max_lot_deviation_pct=5.0)
    assert verdict.passed is False
    assert "lot_size_deviation" in verdict.reason


def test_risk_fixed_lot_passes_when_match_and_broker_constraints_ok() -> None:
    ds = _FakeDataSource(min_lot=0.01, lot_step=0.01, max_lot=100.0)
    recheck = PreTradeRecheck(data_source=ds)
    intent = _make_trade_intent(lot_size=0.01)
    verdict = recheck.recheck_risk(
        intent,
        dynamic_lot_sizing=False,
        fixed_lot_size=0.01,
    )
    assert verdict.passed is True
    assert verdict.reason == ""


def test_risk_fixed_lot_fails_on_mismatch() -> None:
    ds = _FakeDataSource(min_lot=0.01, lot_step=0.01, max_lot=100.0)
    recheck = PreTradeRecheck(data_source=ds)
    intent = _make_trade_intent(lot_size=0.02)
    verdict = recheck.recheck_risk(
        intent,
        dynamic_lot_sizing=False,
        fixed_lot_size=0.01,
    )
    assert verdict.passed is False
    assert "fixed_lot_mismatch" in verdict.reason


def test_risk_fixed_lot_fails_when_out_of_bounds() -> None:
    ds = _FakeDataSource(min_lot=0.01, lot_step=0.01, max_lot=0.05)
    recheck = PreTradeRecheck(data_source=ds)
    intent = _make_trade_intent(lot_size=0.10)
    verdict = recheck.recheck_risk(
        intent,
        dynamic_lot_sizing=False,
        fixed_lot_size=0.10,
    )
    assert verdict.passed is False
    assert "fixed_lot_out_of_bounds" in verdict.reason


def test_risk_fixed_lot_fails_when_step_mismatch() -> None:
    ds = _FakeDataSource(min_lot=0.01, lot_step=0.01, max_lot=100.0)
    recheck = PreTradeRecheck(data_source=ds)
    intent = _make_trade_intent(lot_size=0.015)
    verdict = recheck.recheck_risk(
        intent,
        dynamic_lot_sizing=False,
        fixed_lot_size=0.015,
    )
    assert verdict.passed is False
    assert "fixed_lot_step_mismatch" in verdict.reason


# --- Symbol Recheck ---


def test_symbol_passes_when_tradable() -> None:
    ds = _FakeDataSource(trade_allowed=True, trade_mode=4)
    recheck = PreTradeRecheck(data_source=ds)
    verdict = recheck.recheck_symbol(_make_trade_intent())
    assert verdict.passed is True


def test_symbol_fails_when_trade_disabled() -> None:
    ds = _FakeDataSource(trade_allowed=False)
    recheck = PreTradeRecheck(data_source=ds)
    verdict = recheck.recheck_symbol(_make_trade_intent())
    assert verdict.passed is False
    assert verdict.reason == "symbol_trade_disabled"


def test_symbol_fails_when_trade_mode_invalid() -> None:
    ds = _FakeDataSource(trade_allowed=True, trade_mode=2)
    recheck = PreTradeRecheck(data_source=ds)
    verdict = recheck.recheck_symbol(_make_trade_intent())
    assert verdict.passed is False
    assert "symbol_trade_mode_invalid" in verdict.reason


# --- Market Recheck ---


def test_market_passes_when_open() -> None:
    ds = _FakeDataSource(session_deals=True)
    recheck = PreTradeRecheck(data_source=ds)
    verdict = recheck.recheck_market(_make_trade_intent())
    assert verdict.passed is True


def test_market_fails_when_closed() -> None:
    ds = _FakeDataSource(session_deals=False)
    recheck = PreTradeRecheck(data_source=ds)
    verdict = recheck.recheck_market(_make_trade_intent())
    assert verdict.passed is False
    assert verdict.reason.startswith("market_closed:")
    assert "session_deals=False" in verdict.reason
    assert "market_open=False" in verdict.reason


def test_market_passes_when_session_deals_missing_and_tick_tradable_valid() -> None:
    ds = _FakeDataSource(session_deals=True)
    del ds._symbol["session_deals"]
    recheck = PreTradeRecheck(data_source=ds)
    verdict = recheck.recheck_market(_make_trade_intent())
    assert verdict.passed is True


# --- Orchestrator: run_all ---


def test_run_all_passes_when_all_checks_pass() -> None:
    ds = _FakeDataSource()
    recheck = PreTradeRecheck(data_source=ds)
    verdict = recheck.run_all(_make_trade_intent(), decision_spread=0.0002)
    assert verdict.passed is True
    assert verdict.reason == ""


def test_run_all_fails_on_first_bad_check() -> None:
    ds = _FakeDataSource(session_deals=False)  # market closed
    recheck = PreTradeRecheck(data_source=ds)
    verdict = recheck.run_all(_make_trade_intent(), decision_spread=0.0002)
    assert verdict.passed is False
    # Spread, account, risk, symbol pass; market fails
    assert verdict.reason.startswith("market:market_closed")


def test_run_all_fails_on_spread_when_spread_bad() -> None:
    ds = _FakeDataSource(bid=1.1000, ask=1.1010, session_deals=False)
    recheck = PreTradeRecheck(data_source=ds)
    verdict = recheck.run_all(_make_trade_intent(), decision_spread=0.0002)
    assert verdict.passed is False
    # Spread is first, so it fails before market
    assert verdict.reason.startswith("spread:spread_widened")


# --- No data source ---


def test_all_checks_fail_without_data_source() -> None:
    recheck = PreTradeRecheck(data_source=None)
    intent = _make_trade_intent()

    assert recheck.recheck_spread(intent, decision_spread=0.0002).passed is False
    assert recheck.recheck_account(intent).passed is False
    assert recheck.recheck_risk(intent).passed is False
    assert recheck.recheck_symbol(intent).passed is False
    assert recheck.recheck_market(intent).passed is False
    assert recheck.run_all(intent, decision_spread=0.0002).passed is False
