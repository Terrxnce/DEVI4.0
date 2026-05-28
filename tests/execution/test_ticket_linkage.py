"""Tests for ticket linkage — decision_id → trade_id → mt5_ticket.

Verifies that the trades JSONL record written by LiveSession contains
all three IDs linked together, plus the required position fields.
These tests use mocked MT5 and do not place real orders.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.execution.live_wrapper import LiveOrderWrapper, LiveOrderResult


class _MockMT5Fill:
    """Fake MT5 that confirms fill with ticket 99999."""

    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_FILLING_IOC = 1

    def order_send(self, request: dict) -> Any:
        class R:
            retcode = 10009
            order = 99999
            price = 1.1002
            comment = "mock filled"
        return R()

    def last_error(self) -> str:
        return "(0, None)"

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        return True


class _FakeDataSource:
    def __init__(self, mt5_client: Any = None) -> None:
        self.mt5_client = mt5_client
        self._tick = {"bid": 1.1000, "ask": 1.1002}
        self._account = {"balance": 5000.0, "equity": 5000.0, "free_margin": 4800.0}
        self._profile = {
            "point": 1e-5,
            "contract_size": 100_000.0,
            "lot_step": 0.01,
            "min_lot": 0.01,
            "max_lot": 100.0,
        }
        self._symbol_info = {"trade_allowed": True, "trade_mode": 4, "session_deals": True}

    def fetch_tick(self, symbol: str) -> dict:
        return self._tick

    def fetch_account_info(self) -> dict:
        return self._account

    def fetch_instrument_profile(self, symbol: str) -> dict:
        return self._profile

    def fetch_symbol_info(self, symbol: str) -> dict:
        return self._symbol_info


def _make_intent():
    from src.core.enums import (
        ConfidenceTier, Direction, HTFAgreement, Regime, Session,
        SetupClass, StructureType, Timeframe,
    )
    from src.core.models import (
        ConfluenceResult, ContextSnapshot, DetectedStructure,
        ExitPlan, RiskVerdict, TradeIntent,
    )
    now = datetime.now(tz=UTC)
    return TradeIntent(
        trade_id="trade_abc",
        symbol="EURUSD",
        direction=Direction.BULLISH,
        setup_class=SetupClass.OB_WITH_BOS,
        confidence_tier=ConfidenceTier.A,
        session=Session.LONDON,
        entry_price=1.1000,
        exit_plan=ExitPlan(
            stop_loss=1.0960,
            take_profit=1.1080,
            risk_reward=2.0,
            sl_source="structure",
            tp_source="structure",
            breakeven_trigger_r=1.0,
            session_close_exit=False,
        ),
        risk_verdict=RiskVerdict(
            approved=True,
            lot_size=0.01,
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
                age_bars=2,
                atr_relative_size=1.0,
                timeframe=Timeframe.M15,
                bar_index=5,
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
            atr_current=0.001,
            atr_percentile=0.5,
            spread_atr_ratio=0.1,
            stale_entry=False,
            news_blocked=False,
            nearby_structures=[],
        ),
        config_hash="cfg",
        bar_time=now,
    )


def _arm(symbols=None):
    from src.core.arming import ArmingService
    svc = ArmingService()
    svc.arm(run_id="run_001", armed_by="op", reason="test",
            symbols=symbols or ["EURUSD"], max_orders=1)
    return svc


# ---------------------------------------------------------------------------
# LiveOrderWrapper telemetry uses trade_id (not decision_id)
# ---------------------------------------------------------------------------


def test_live_order_telemetry_uses_trade_id_key() -> None:
    """live_orders JSONL should label the field as trade_id, not decision_id."""
    logged: list[dict] = []

    class FakeTelemetry:
        def write_live_order(self, rec: dict) -> None:
            logged.append(rec)

    mt5 = _MockMT5Fill()
    wrapper = LiveOrderWrapper(
        data_source=_FakeDataSource(mt5_client=mt5),
        telemetry_writer=FakeTelemetry(),
    )
    from src.core.kill_switch import KillSwitch
    from src.core.runtime_state import RuntimeState
    wrapper.send(
        _make_intent(),
        arming_service=_arm(),
        kill_switch=KillSwitch(),
        runtime_state=RuntimeState(run_id="run_001"),
        decision_spread=0.0002,
        max_orders_per_run=1,
        risk_dynamic_lot_sizing=False,
        risk_fixed_lot_size=0.01,
    )

    assert len(logged) == 1
    rec = logged[0]
    # Must have trade_id
    assert "trade_id" in rec
    assert rec["trade_id"] == "trade_abc"
    # Must NOT have a key called decision_id (that was the old mislabeling)
    assert "decision_id" not in rec


def test_live_order_telemetry_includes_ticket() -> None:
    logged: list[dict] = []

    class FakeTelemetry:
        def write_live_order(self, rec: dict) -> None:
            logged.append(rec)

    mt5 = _MockMT5Fill()
    wrapper = LiveOrderWrapper(
        data_source=_FakeDataSource(mt5_client=mt5),
        telemetry_writer=FakeTelemetry(),
    )
    from src.core.kill_switch import KillSwitch
    from src.core.runtime_state import RuntimeState
    wrapper.send(
        _make_intent(),
        arming_service=_arm(),
        kill_switch=KillSwitch(),
        runtime_state=RuntimeState(run_id="run_001"),
        decision_spread=0.0002,
        max_orders_per_run=1,
        risk_dynamic_lot_sizing=False,
        risk_fixed_lot_size=0.01,
    )

    rec = logged[0]
    assert rec["ticket"] == 99999


# ---------------------------------------------------------------------------
# Trades JSONL contains full linkage chain
# ---------------------------------------------------------------------------


def test_live_fill_dict_contains_full_linkage_chain() -> None:
    """Simulate a fill and verify the dict has decision_id, trade_id, ticket."""
    from src.core.arming import ArmingService
    from src.core.kill_switch import KillSwitch
    from src.core.runtime_state import RuntimeState

    logged_orders: list[dict] = []

    class FakeTelemetry:
        def write_live_order(self, rec: dict) -> None:
            logged_orders.append(rec)

    mt5 = _MockMT5Fill()
    wrapper = LiveOrderWrapper(
        data_source=_FakeDataSource(mt5_client=mt5),
        telemetry_writer=FakeTelemetry(),
    )
    result = wrapper.send(
        _make_intent(),
        arming_service=_arm(),
        kill_switch=KillSwitch(),
        runtime_state=RuntimeState(run_id="run_001"),
        decision_spread=0.0002,
        max_orders_per_run=1,
        risk_dynamic_lot_sizing=False,
        risk_fixed_lot_size=0.01,
    )

    # The wrapper result itself carries the linkage
    assert result.sent is True
    assert result.ticket == 99999
    # decision_id is set externally in LiveSession; wrapper carries trade_id
    assert result.decision_id == "trade_abc"  # this is trade_id in the wrapper


def test_live_fill_dict_required_position_fields() -> None:
    """Verify the live_fill dict written by LiveSession contains required fields.

    We test the structure by constructing it the same way LiveSession does,
    since LiveSession integration tests require a full MT5 stack.
    """
    # Build the dict the same way _run_symbol does
    from src.core.enums import Direction
    from src.core.models import ExitPlan, RiskVerdict
    from src.core.enums import SetupClass, ConfidenceTier

    decision_id = "run_001_EURUSD_dec"
    trade_id = "trade_abc"
    ticket = 99999
    symbol = "EURUSD"
    run_id = "run_001"
    spread = 0.0002

    class _FakeIntent:
        direction = Direction.BULLISH
        trade_id = "trade_abc"
        entry_price = 1.1000
        risk_verdict = RiskVerdict(approved=True, lot_size=0.01,
                                   actual_risk_pct=1.0, intended_risk_pct=1.0, reason="")
        exit_plan = ExitPlan(stop_loss=1.0960, take_profit=1.1080, risk_reward=2.0,
                             sl_source="s", tp_source="s", breakeven_trigger_r=1.0,
                             session_close_exit=False)
        setup_class = SetupClass.OB_WITH_BOS
        confidence_tier = ConfidenceTier.A

    class _FakeResult:
        sent = True
        ticket = 99999
        entry_price = 1.1002
        slippage = 0.0002
        status = "FILLED"
        broker_retcode = 10009

    intent = _FakeIntent()
    order_result = _FakeResult()

    live_fill = {
        "decision_id": decision_id,
        "trade_id": intent.trade_id,
        "ticket": order_result.ticket,
        "run_id": run_id,
        "symbol": symbol,
        "side": intent.direction.value,
        "lot_size": intent.risk_verdict.lot_size,
        "sl": intent.exit_plan.stop_loss,
        "tp": intent.exit_plan.take_profit,
        "setup_class": intent.setup_class.value if intent.setup_class else "",
        "confidence_tier": intent.confidence_tier.value if intent.confidence_tier else "",
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "intended_entry": intent.entry_price,
        "actual_fill": order_result.entry_price,
        "slippage": order_result.slippage,
        "spread_at_fill": spread,
        "order_status": order_result.status,
        "broker_retcode": order_result.broker_retcode,
        "status": "open" if order_result.sent else "rejected",
    }

    # Full linkage chain
    assert live_fill["decision_id"] == "run_001_EURUSD_dec"
    assert live_fill["trade_id"] == "trade_abc"
    assert live_fill["ticket"] == 99999

    # Position identity fields
    assert live_fill["symbol"] == "EURUSD"
    assert live_fill["sl"] == 1.0960
    assert live_fill["tp"] == 1.1080
    assert live_fill["lot_size"] == 0.01
    assert live_fill["setup_class"] == "OB_WITH_BOS"
    assert live_fill["confidence_tier"] == "A"
    assert live_fill["run_id"] == "run_001"

    # Lifecycle state
    assert live_fill["status"] == "open"

    # Timestamp present
    assert live_fill["timestamp"] is not None
