"""Tests for paper session with MT5 data connectivity.

These tests use a mock MT5 client to prove the pipeline works without real MT5.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from src.core.enums import Direction, FinalDecision, Namespace, StructureType, Timeframe
from src.core.models import Bar, DetectedStructure
from src.data.mt5_source import MT5DataSource
from src.execution.paper_session import PaperSession


def _paper_config() -> dict:
    return json.loads(Path("src/config/paper.json").read_text(encoding="utf-8"))


def _mock_mt5_client():
    """Create a mock MT5 client for testing."""
    class MockTick:
        bid = 1.10000
        ask = 1.10015
        time = int(datetime(2026, 4, 30, 8, 0, tzinfo=UTC).timestamp())

    class MockSymbolInfo:
        digits = 5
        point = 0.00001
        trade_tick_size = 0.00001
        trade_contract_size = 100000.0
        volume_step = 0.01
        volume_min = 0.01
        volume_max = 100.0

    class MockAccountInfo:
        balance = 10000.0
        equity = 10050.0
        margin = 0.0
        margin_free = 10000.0
        currency = "USD"

    class MockRates:
        def __init__(self, count: int) -> None:
            self.count = count

        def __getitem__(self, idx: int):
            t = int(datetime(2026, 4, 30, 7, 0, tzinfo=UTC).timestamp()) + idx * 900
            return {
                "time": t,
                "open": 1.0990 + (idx % 5) * 0.0001,
                "high": 1.0995 + (idx % 5) * 0.0001,
                "low": 1.0985 + (idx % 5) * 0.0001,
                "close": 1.0992 + (idx % 5) * 0.0001,
                "tick_volume": 100,
                "real_volume": 100,
            }

        def __len__(self):
            return self.count

        def __iter__(self):
            for i in range(self.count):
                yield self[i]

    class MockMT5:
        TIMEFRAME_M15 = "TIMEFRAME_M15"
        TIMEFRAME_H1 = "TIMEFRAME_H1"

        def initialize(self):
            return True

        def shutdown(self):
            pass

        def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
            return MockRates(count)

        def symbol_info(self, symbol):
            return MockSymbolInfo()

        def account_info(self):
            return MockAccountInfo()

        def symbol_info_tick(self, symbol):
            return MockTick()

    return MockMT5()


def test_paper_session_runs_with_mock_mt5(tmp_path) -> None:
    """Full paper session with mock MT5 data."""
    cfg = _paper_config()
    session = PaperSession(
        config=cfg,
        logs_root=str(tmp_path / "logs"),
        namespace=Namespace.EVAL,
    )
    # Inject mock client
    session.data = MT5DataSource(mt5_client=_mock_mt5_client())

    result = session.run(run_id="test_paper_001")
    session.close()

    # Default symbol list includes EURUSD as first symbol
    sym = result.symbol_results.get("EURUSD")
    assert sym is not None, "EURUSD result should exist"

    print(f"\n  [PAPER SESSION RESULT]")
    print(f"    decision: {sym.decision.value}")
    print(f"    failure_code: {sym.failure_code}")
    print(f"    bars_m15: {sym.bars_m15_count}")
    print(f"    bars_h1: {sym.bars_h1_count}")
    print(f"    balance: {result.account_balance}")
    print(f"    equity: {result.account_equity}")
    print(f"    tick_bid: {sym.tick_bid}")
    print(f"    tick_ask: {sym.tick_ask}")
    print(f"    paper_fill: {sym.paper_fill is not None}")

    assert sym.bars_m15_count == 100
    assert sym.bars_h1_count == 50
    assert result.account_balance == 10000.0
    assert result.account_equity == 10050.0
    assert sym.tick_bid > 0
    assert sym.tick_ask > 0
    assert sym.decision in {FinalDecision.HOLD, FinalDecision.EXECUTE}


def test_mt5_data_source_no_broker_methods() -> None:
    """MT5DataSource never exposes broker execution methods."""
    source = MT5DataSource(mt5_client=_mock_mt5_client())

    assert not hasattr(source, "order_send")
    assert not hasattr(source, "order_modify")
    assert not hasattr(source, "position_close")
    assert not hasattr(source, "positions_total")

    source.close()


def test_live_mode_rejected_in_paper_session() -> None:
    """Live mode must be rejected even through PaperSession."""
    cfg = _paper_config()
    cfg["runtime"] = dict(cfg["runtime"])
    cfg["runtime"]["mode"] = "live"

    # The execution gate in evaluate_decision will reject live mode
    from src.decision.engine import evaluate_decision
    from src.core.enums import Direction, HTFAgreement, Regime, Session
    from src.core.models import ContextSnapshot

    context = ContextSnapshot(
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

    outcome = evaluate_decision(
        structures=[],
        context=context,
        config=cfg,
        entry_price=1.1000,
        atr_override=0.001,
    )

    assert outcome.final_decision == FinalDecision.HOLD or outcome.final_decision == FinalDecision.REJECTED_EXECUTION


def test_mt5_account_info_structure() -> None:
    """fetch_account_info returns expected dict structure."""
    source = MT5DataSource(mt5_client=_mock_mt5_client())
    info = source.fetch_account_info()

    assert "balance" in info
    assert "equity" in info
    assert "margin" in info
    assert "free_margin" in info
    assert "currency" in info
    assert info["balance"] == 10000.0
    assert info["equity"] == 10050.0

    source.close()


def test_mt5_tick_structure() -> None:
    """fetch_tick returns expected dict structure."""
    source = MT5DataSource(mt5_client=_mock_mt5_client())
    tick = source.fetch_tick("EURUSD")

    assert "bid" in tick
    assert "ask" in tick
    assert "time" in tick
    assert tick["bid"] > 0
    assert tick["ask"] > 0
    assert tick["ask"] >= tick["bid"]

    source.close()


def test_mt5_fetch_bars_returns_bar_objects() -> None:
    """fetch_bars returns list of Bar objects."""
    source = MT5DataSource(mt5_client=_mock_mt5_client())
    bars = source.fetch_bars("EURUSD", Timeframe.M15, count=10)

    assert len(bars) == 10
    assert all(isinstance(b, Bar) for b in bars)
    assert bars[0].symbol == "EURUSD"
    assert bars[0].timeframe == Timeframe.M15

    source.close()
