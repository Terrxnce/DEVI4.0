"""Tests for multi-symbol paper session with runtime state and duplicate prevention."""
from __future__ import annotations

from datetime import UTC, datetime

from src.core.enums import Namespace, Timeframe
from src.core.models import Bar
from src.execution.paper_session import PaperSession
from tests.execution.test_paper_mt5_session import _paper_config


def _make_profile(*, point: float = 1e-05, lot_step: float = 0.01) -> dict:
    """Return a plain dict instrument profile for testing."""
    return {
        "point": point,
        "contract_size": 100000.0,
        "tick_size": 1e-05,
        "lot_step": lot_step,
        "min_lot": 0.01,
        "max_lot": 100.0,
        "digits": 5,
    }


def _make_bar(index: int = 0) -> Bar:
    return Bar(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        time=datetime.now(tz=UTC),
        open=1.1000,
        high=1.1010,
        low=1.0990,
        close=1.1005,
        volume=1000.0,
        bar_index=index,
    )


def _make_fake_data(*, point: float = 1e-05):
    """Return a FakeData class with correct method signatures."""
    bars = [_make_bar(i) for i in range(5)]

    class FakeData:
        def fetch_account_info(self) -> dict:
            return {"balance": 100000.0, "equity": 100000.0, "margin": 0.0, "free_margin": 100000.0, "currency": "USD"}

        def fetch_tick(self, symbol: str) -> dict:
            return {"bid": 1.1000, "ask": 1.1002, "time": 1714464000}

        def fetch_instrument_profile(self, symbol: str):
            return _make_profile(point=point)

        def fetch_bars(self, symbol: str, timeframe, count: int) -> list:
            return bars

        def close(self) -> None:
            pass

    return FakeData()


def test_multi_symbol_runs_deterministically() -> None:
    """PaperSession iterates symbols in sorted order and produces independent results."""
    cfg = _paper_config()
    session = PaperSession(
        config=cfg,
        logs_root="/tmp/paper_test",
        namespace=Namespace.EVAL,
        symbols=["EURUSD", "GBPUSD"],
    )
    session.data = _make_fake_data()

    result = session.run(run_id="multi_001")
    session.close()

    assert len(result.symbol_results) == 2
    assert "EURUSD" in result.symbol_results
    assert "GBPUSD" in result.symbol_results
    assert list(result.symbol_results.keys()) == ["EURUSD", "GBPUSD"]


def test_missing_critical_data_skips_symbol() -> None:
    """If instrument profile lacks critical fields, symbol is skipped."""
    cfg = _paper_config()
    session = PaperSession(
        config=cfg,
        logs_root="/tmp/paper_test",
        namespace=Namespace.EVAL,
        symbols=["EURUSD"],
    )
    session.data = _make_fake_data(point=0.0)

    result = session.run(run_id="skip_001")
    session.close()

    eur = result.symbol_results["EURUSD"]
    assert eur.skipped_reason is not None
    assert "missing_instrument_data" in eur.skipped_reason
    assert "point" in eur.skipped_reason


def test_runtime_state_tracks_across_symbols() -> None:
    """Runtime state counts decisions and trades across all symbols."""
    cfg = _paper_config()
    session = PaperSession(
        config=cfg,
        logs_root="/tmp/paper_test",
        namespace=Namespace.EVAL,
        symbols=["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"],
    )
    session.data = _make_fake_data()

    result = session.run(run_id="track_001")
    session.close()

    assert result.decision_count == 4
    assert result.decision_count == len(result.symbol_results)
    assert result.trade_count == 0


def test_no_duplicate_decision_ids() -> None:
    """Each symbol gets a unique snapshot_id."""
    cfg = _paper_config()
    session = PaperSession(
        config=cfg,
        logs_root="/tmp/paper_test",
        namespace=Namespace.EVAL,
        symbols=["EURUSD", "GBPUSD"],
    )
    session.data = _make_fake_data()

    result = session.run(run_id="dup_001")
    session.close()

    ids = [r.snapshot_id for r in result.symbol_results.values()]
    assert len(ids) == len(set(ids)), "All snapshot IDs must be unique"


def test_max_orders_enforced_across_symbols() -> None:
    """Runtime state prevents exceeding max_orders_per_run across all symbols."""
    cfg = _paper_config()
    cfg["execution"]["max_orders_per_run"] = 1
    session = PaperSession(
        config=cfg,
        logs_root="/tmp/paper_test",
        namespace=Namespace.EVAL,
        symbols=["EURUSD", "GBPUSD"],
    )
    session.data = _make_fake_data()

    result = session.run(run_id="max_001")
    session.close()

    assert session.runtime_state.decision_count == 2
    assert session.runtime_state.can_place_order(max_orders=1) is True
