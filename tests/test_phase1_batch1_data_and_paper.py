from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

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
from src.data.base import DataSourceError
from src.data.csv_source import CsvDataSource
from src.data.mt5_source import MT5DataSource
from src.execution.paper_adapter import PaperExecutionAdapter


class FakeSymbolInfo:
    digits = 5
    point = 0.00001
    trade_tick_size = 0.00001
    trade_contract_size = 100000.0
    volume_step = 0.01
    volume_min = 0.01
    volume_max = 50.0


class FakeMT5Client:
    TIMEFRAME_M15 = 15
    TIMEFRAME_H1 = 60

    def __init__(self) -> None:
        self.calls: list[str] = []

    def initialize(self) -> bool:
        self.calls.append("initialize")
        return True

    def shutdown(self) -> None:
        self.calls.append("shutdown")

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start_pos: int, count: int):
        self.calls.append("copy_rates_from_pos")
        return [
            {
                "time": 1714464000,
                "open": 1.0800,
                "high": 1.0810,
                "low": 1.0790,
                "close": 1.0805,
                "tick_volume": 100,
            },
            {
                "time": 1714464900,
                "open": 1.0805,
                "high": 1.0820,
                "low": 1.0800,
                "close": 1.0818,
                "tick_volume": 120,
            },
        ][:count]

    def symbol_info(self, symbol: str):
        self.calls.append("symbol_info")
        return FakeSymbolInfo()

    def order_send(self, *_args, **_kwargs):
        self.calls.append("order_send")
        raise AssertionError("order_send must never be called in MT5 data source")


def _make_trade_intent(direction: Direction = Direction.BULLISH) -> TradeIntent:
    structure = DetectedStructure(
        structure_type=StructureType.ORDER_BLOCK,
        direction=direction,
        price_high=1.0810,
        price_low=1.0790,
        quality=0.8,
        age_bars=1,
        atr_relative_size=0.9,
        timeframe=Timeframe.M15,
        bar_index=10,
        bar_time=datetime(2026, 4, 30, 8, 0, tzinfo=UTC),
        metadata={},
    )
    confluence = ConfluenceResult(
        setup_class=SetupClass.OB_WITH_BOS,
        direction=direction,
        primary_trigger=structure,
        structural_confirmations=[structure],
        structural_labels=["htf_zone_overlap"],
        minor_confluences=["ema_alignment"],
        hard_rejects=[],
        soft_penalties=[],
        structural_count=1,
        minor_count=1,
        quality_penalty=0.0,
        effective_quality=1.0,
        confluence_pass=True,
        confidence_tier=ConfidenceTier.A,
        tier_reason="test",
    )
    context = ContextSnapshot(
        symbol="EURUSD",
        bar_time=datetime(2026, 4, 30, 8, 0, tzinfo=UTC),
        session=Session.LONDON,
        micro_window=False,
        trend_m15=direction,
        trend_h1=direction,
        htf_agreement=HTFAgreement.AGREES,
        regime=Regime.TRENDING,
        atr_current=0.0012,
        atr_percentile=0.6,
        spread_atr_ratio=0.1,
        stale_entry=False,
        news_blocked=False,
        nearby_structures=[structure],
    )
    exit_plan = ExitPlan(
        stop_loss=1.0790,
        take_profit=1.0830,
        risk_reward=1.5,
        sl_source="ORDER_BLOCK",
        tp_source="ORDER_BLOCK",
        breakeven_trigger_r=1.0,
        session_close_exit=True,
    )
    risk = RiskVerdict(
        approved=True,
        lot_size=0.01,
        actual_risk_pct=0.4,
        intended_risk_pct=0.4,
        reason="approved",
    )
    return TradeIntent(
        trade_id="trade_001",
        symbol="EURUSD",
        direction=direction,
        setup_class=SetupClass.OB_WITH_BOS,
        confidence_tier=ConfidenceTier.A,
        session=Session.LONDON,
        entry_price=1.0800,
        exit_plan=exit_plan,
        risk_verdict=risk,
        confluence=confluence,
        context=context,
        config_hash="abc123",
        bar_time=datetime(2026, 4, 30, 8, 0, tzinfo=UTC),
    )


def test_csv_source_reads_and_limits_rows(tmp_path: Path) -> None:
    bars_csv = tmp_path / "bars.csv"
    bars_csv.write_text(
        "symbol,timeframe,time,open,high,low,close,volume,bar_index\n"
        "EURUSD,M15,2026-04-30T08:00:00Z,1.0800,1.0810,1.0790,1.0805,100,10\n"
        "EURUSD,M15,2026-04-30T08:15:00Z,1.0805,1.0820,1.0800,1.0818,120,11\n"
        "EURUSD,H1,2026-04-30T08:00:00Z,1.0790,1.0830,1.0780,1.0820,500,5\n",
        encoding="utf-8",
    )

    source = CsvDataSource(bars_file=str(bars_csv))
    bars = source.fetch_bars("EURUSD", Timeframe.M15, 1)

    assert len(bars) == 1
    assert bars[0].bar_index == 11


def test_csv_source_missing_column_raises(tmp_path: Path) -> None:
    bars_csv = tmp_path / "bars_bad.csv"
    bars_csv.write_text(
        "symbol,timeframe,time,open,high,low,close,volume\n"
        "EURUSD,M15,2026-04-30T08:00:00Z,1.0800,1.0810,1.0790,1.0805,100\n",
        encoding="utf-8",
    )

    source = CsvDataSource(bars_file=str(bars_csv))
    with pytest.raises(DataSourceError):
        source.fetch_bars("EURUSD", Timeframe.M15, 10)


def test_mt5_source_data_calls_only() -> None:
    fake = FakeMT5Client()
    source = MT5DataSource(mt5_client=fake)

    bars = source.fetch_bars("EURUSD", Timeframe.M15, 2)
    profile = source.fetch_instrument_profile("EURUSD")
    source.close()

    assert len(bars) == 2
    assert profile.symbol == "EURUSD"
    assert "copy_rates_from_pos" in fake.calls
    assert "symbol_info" in fake.calls
    assert "order_send" not in fake.calls


def test_paper_adapter_simulates_fill_without_broker() -> None:
    adapter = PaperExecutionAdapter()
    buy_intent = _make_trade_intent(Direction.BULLISH)
    sell_intent = _make_trade_intent(Direction.BEARISH)

    buy_fill = adapter.execute(buy_intent, spread_at_decision=0.0001)
    sell_fill = adapter.execute(sell_intent, spread_at_decision=0.0001)

    assert buy_fill.side == "BUY"
    assert sell_fill.side == "SELL"
    assert buy_fill.actual_fill > buy_fill.intended_entry
    assert sell_fill.actual_fill < sell_fill.intended_entry
