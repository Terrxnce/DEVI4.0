"""Tests for LiveOrderWrapper with real order_send wiring.

All broker calls use mocked MT5 clients. No real trades are placed.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.core.arming import ArmingService
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
from src.core.kill_switch import KillSwitch
from src.core.models import (
    ConfluenceResult,
    ContextSnapshot,
    DetectedStructure,
    ExitPlan,
    RiskVerdict,
    TradeIntent,
)
from src.core.runtime_state import RuntimeState
from src.execution.live_wrapper import LiveOrderResult, LiveOrderWrapper


class _MockMT5:
    """Fake MT5 client with configurable order_send response."""

    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1

    def __init__(self, *, retcode: int = 10009, order: int = 12345, price: float | None = None) -> None:
        self._retcode = retcode
        self._order = order
        self._price = price
        self.calls: list[dict] = []

    def order_send(self, request: dict) -> Any:
        self.calls.append(request)
        class Result:
            pass
        r = Result()
        r.retcode = self._retcode
        r.order = self._order
        r.price = self._price if self._price is not None else request.get("price")
        r.comment = f"mock:{self._retcode}"
        return r


def _make_trade_intent(
    *,
    entry_price: float = 1.1000,
    stop_loss: float = 1.0980,
    lot_size: float = 0.10,
) -> TradeIntent:
    now = datetime.now(tz=UTC)
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
                bar_time=now,
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
            bar_time=now,
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
        bar_time=now,
    )


class _FakeDataSource:
    def __init__(
        self,
        *,
        bid: float = 1.1000,
        ask: float = 1.1002,
        balance: float = 2000.0,
        equity: float = 2050.0,
        free_margin: float = 1900.0,
        trade_allowed: bool = True,
        trade_mode: int = 4,
        session_deals: bool = True,
        point: float = 1e-05,
        contract_size: float = 100000.0,
        lot_step: float = 0.01,
        min_lot: float = 0.01,
        max_lot: float = 100.0,
        mt5_client: Any | None = None,
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
        self.mt5_client = mt5_client

    def fetch_tick(self, symbol: str) -> dict:
        return self._tick

    def fetch_account_info(self) -> dict:
        return self._account

    def fetch_instrument_profile(self, symbol: str) -> dict:
        return self._profile

    def fetch_symbol_info(self, symbol: str) -> dict:
        return self._symbol


def _armed_service(symbols: list[str] | None = None) -> ArmingService:
    svc = ArmingService()
    svc.arm(
        run_id="run_001",
        armed_by="op",
        reason="test",
        symbols=symbols or ["EURUSD"],
        max_orders=1,
    )
    return svc


def _make_wrapper(mt5_client: Any | None = None, **ds_kwargs) -> LiveOrderWrapper:
    return LiveOrderWrapper(data_source=_FakeDataSource(mt5_client=mt5_client, **ds_kwargs))


def _send(
    wrapper: LiveOrderWrapper,
    intent: TradeIntent,
    *,
    arming_service: ArmingService | None = None,
    kill_switch: KillSwitch | None = None,
    runtime_state: RuntimeState | None = None,
    decision_spread: float = 0.0002,
    max_orders: int = 1,
    kill_switch_enabled: bool = False,
) -> LiveOrderResult:
    return wrapper.send(
        intent,
        arming_service=arming_service or _armed_service(),
        kill_switch=kill_switch or KillSwitch(),
        runtime_state=runtime_state or RuntimeState(run_id="run_001"),
        decision_spread=decision_spread,
        max_orders_per_run=max_orders,
        kill_switch_enabled=kill_switch_enabled,
    )


# --- Gate 1: Arming ---


def test_blocked_without_arming_token() -> None:
    wrapper = _make_wrapper()
    result = _send(wrapper, _make_trade_intent(), arming_service=ArmingService())
    assert result.sent is False
    assert result.status == "blocked_not_armed"
    assert "No valid live arming token" in result.reason


def test_blocked_with_expired_token() -> None:
    svc = ArmingService()
    svc.arm(
        run_id="run_001", armed_by="op", reason="test",
        symbols=["EURUSD"], max_orders=1, ttl_minutes=-1,
    )
    wrapper = _make_wrapper()
    result = _send(wrapper, _make_trade_intent(), arming_service=svc)
    assert result.sent is False
    assert result.status == "blocked_not_armed"


def test_blocked_when_symbol_not_authorized() -> None:
    wrapper = _make_wrapper()
    result = _send(
        wrapper,
        _make_trade_intent(),
        arming_service=_armed_service(symbols=["GBPUSD"]),
    )
    assert result.sent is False
    assert result.status == "blocked_symbol_not_authorized"


# --- Gate 2: Kill Switch ---


def test_blocked_when_kill_switch_triggered() -> None:
    ks = KillSwitch()
    ks.trigger("manual")
    wrapper = _make_wrapper()
    result = _send(wrapper, _make_trade_intent(), kill_switch=ks)
    assert result.sent is False
    assert "blocked_kill_switch" in result.status
    assert "manual" in result.reason


def test_blocked_when_kill_switch_from_config() -> None:
    """kill_switch_enabled=True in config must block execution via evaluate()."""
    ks = KillSwitch()
    wrapper = _make_wrapper()
    # Pass the config flag through — wrapper now forwards it to ks.evaluate()
    result = _send(
        wrapper,
        _make_trade_intent(),
        kill_switch=ks,
        kill_switch_enabled=True,
    )
    assert result.sent is False
    assert "blocked_kill_switch" in result.status
    assert "config_kill_switch" in result.reason


def test_kill_switch_config_false_does_not_block() -> None:
    """kill_switch_enabled=False (default) must not block a clean kill switch."""
    ks = KillSwitch()
    wrapper = _make_wrapper(mt5_client=_MockMT5())
    result = _send(
        wrapper,
        _make_trade_intent(),
        kill_switch=ks,
        kill_switch_enabled=False,
    )
    # Should reach broker (or be blocked by no mt5 client), not by kill switch
    assert "blocked_kill_switch" not in result.status


# --- Gate 3: Max Orders ---


def test_blocked_when_max_orders_reached() -> None:
    rs = RuntimeState(run_id="run_001")
    rs.record_trade("t1")  # now orders_this_run = 1
    wrapper = _make_wrapper()
    result = _send(wrapper, _make_trade_intent(), runtime_state=rs, max_orders=1)
    assert result.sent is False
    assert result.status == "blocked_max_orders_exceeded"


# --- Gate 4: Pre-Trade Recheck ---


def test_blocked_when_spread_widened() -> None:
    wrapper = _make_wrapper(bid=1.1000, ask=1.1006)
    result = _send(wrapper, _make_trade_intent(), decision_spread=0.0002)
    assert result.sent is False
    assert "blocked_recheck" in result.status
    assert "spread_widened" in result.reason


def test_blocked_when_market_closed() -> None:
    wrapper = _make_wrapper(session_deals=False)
    result = _send(wrapper, _make_trade_intent())
    assert result.sent is False
    assert "blocked_recheck" in result.status
    assert "market_closed" in result.reason


def test_blocked_when_account_balance_zero() -> None:
    wrapper = _make_wrapper(balance=0.0, equity=0.0, free_margin=0.0)
    result = _send(wrapper, _make_trade_intent())
    assert result.sent is False
    assert "blocked_recheck" in result.status
    assert "account_balance_zero" in result.reason


def test_token_not_consumed_when_blocked_before_execution_stage() -> None:
    svc = _armed_service()
    wrapper = _make_wrapper(session_deals=False)  # market closed -> blocked at recheck
    result = _send(wrapper, _make_trade_intent(), arming_service=svc)
    assert result.sent is False
    assert svc.is_armed is True


# --- Happy Path ---


def test_order_send_success_retcode_10009() -> None:
    """Mocked MT5 returns 10009 -> sent=True, ticket captured."""
    mock = _MockMT5(retcode=10009, order=77777, price=1.1002)
    wrapper = _make_wrapper(mt5_client=mock)
    intent = _make_trade_intent()
    result = _send(wrapper, intent)
    assert result.sent is True
    assert result.status == "FILLED"
    assert result.ticket == 77777
    assert result.broker_retcode == 10009
    assert result.slippage is not None
    assert result.execution_time is not None
    assert len(mock.calls) == 1
    req = mock.calls[0]
    assert req["symbol"] == "EURUSD"
    assert req["volume"] == intent.risk_verdict.lot_size
    assert req["sl"] == intent.exit_plan.stop_loss
    assert req["tp"] == intent.exit_plan.take_profit
    assert "devi:" in req["comment"]


def test_token_consumed_on_execution_attempt() -> None:
    mock = _MockMT5(retcode=10009)
    svc = _armed_service()
    wrapper = _make_wrapper(mt5_client=mock)

    result = _send(wrapper, _make_trade_intent(), arming_service=svc)
    assert result.sent is True
    assert svc.is_armed is False


def test_order_send_failure_retcode_10027() -> None:
    """Mocked MT5 returns 10027 (NO_MONEY) -> sent=False."""
    mock = _MockMT5(retcode=10027)
    wrapper = _make_wrapper(mt5_client=mock)
    result = _send(wrapper, _make_trade_intent())
    assert result.sent is False
    assert result.status == "NO_MONEY"
    assert result.broker_retcode == 10027
    assert "Not enough money" in result.reason


def test_broker_failure_records_kill_switch_failure_count() -> None:
    """Non-success broker retcode should increment kill-switch failure tracking."""
    mock = _MockMT5(retcode=10027)
    wrapper = _make_wrapper(mt5_client=mock)
    ks = KillSwitch()

    _send(wrapper, _make_trade_intent(), kill_switch=ks)
    assert len(ks._failed_orders) == 1


def test_blocked_when_no_mt5_client() -> None:
    """If data_source has no mt5_client, execution is blocked."""
    wrapper = _make_wrapper(mt5_client=None)
    result = _send(wrapper, _make_trade_intent())
    assert result.sent is False
    assert result.status == "blocked_no_mt5_client"


def test_telemetry_logged_on_success() -> None:
    """Telemetry writer receives live order record on success."""
    logged: list[dict] = []

    class FakeTelemetry:
        def write_live_order(self, record: dict) -> None:
            logged.append(record)

    mock = _MockMT5(retcode=10009, order=11111)
    wrapper = LiveOrderWrapper(
        data_source=_FakeDataSource(mt5_client=mock),
        telemetry_writer=FakeTelemetry(),
    )
    result = _send(wrapper, _make_trade_intent())
    assert result.sent is True
    assert len(logged) == 1
    rec = logged[0]
    assert rec["run_id"] == "run_001"
    assert rec["ticket"] == 11111
    assert rec["sent"] is True
    assert rec["broker_retcode"] == 10009
    assert "request" in rec


def test_telemetry_logged_on_failure() -> None:
    """Telemetry writer receives live order record even on failure."""
    logged: list[dict] = []

    class FakeTelemetry:
        def write_live_order(self, record: dict) -> None:
            logged.append(record)

    mock = _MockMT5(retcode=10027)
    wrapper = LiveOrderWrapper(
        data_source=_FakeDataSource(mt5_client=mock),
        telemetry_writer=FakeTelemetry(),
    )
    result = _send(wrapper, _make_trade_intent())
    assert result.sent is False
    assert len(logged) == 1
    assert logged[0]["sent"] is False
    assert logged[0]["broker_retcode"] == 10027
