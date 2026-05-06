"""Tests for PaperPositionTracker minimal position management."""
from __future__ import annotations

from src.execution.paper_adapter import PaperExecutionAdapter, PaperFillResult
from src.execution.position_tracker import PaperPosition, PaperPositionTracker


def _make_fill(trade_id: str, symbol: str = "EURUSD", side: str = "BUY") -> PaperFillResult:
    adapter = PaperExecutionAdapter()
    # Build a minimal PaperFillResult directly
    return PaperFillResult(
        decision_id=trade_id,
        trade_id=trade_id,
        ticket=100001,
        symbol=symbol,
        side=side,
        intended_entry=1.10000,
        actual_fill=1.10002 if side == "BUY" else 1.09998,
        planned_sl=1.09800 if side == "BUY" else 1.10200,
        planned_tp=1.10400 if side == "BUY" else 1.09600,
        slippage=0.00002 if side == "BUY" else -0.00002,
        spread_at_decision=0.00002,
        spread_at_fill=0.00002,
        paper_retcode=10009,
        order_status="FILLED",
        execution_time="2026-04-30T08:00:00+00:00",
    )


def test_open_position_creates_open_state() -> None:
    tracker = PaperPositionTracker()
    fill = _make_fill("trade_001")
    pos = tracker.open_position(fill=fill)

    assert pos.status == "OPEN"
    assert pos.trade_id == "trade_001"
    assert pos.open_price == fill.actual_fill
    assert pos.close_price is None
    assert pos.close_time is None
    assert pos.realized_pnl is None


def test_get_open_positions_filters_correctly() -> None:
    tracker = PaperPositionTracker()
    tracker.open_position(fill=_make_fill("t1"))
    tracker.open_position(fill=_make_fill("t2", side="SELL"))

    open_pos = tracker.get_open_positions()
    assert len(open_pos) == 2
    assert all(p.status == "OPEN" for p in open_pos)


def test_close_position_sets_closed_state() -> None:
    tracker = PaperPositionTracker()
    fill = _make_fill("trade_001", side="BUY")
    tracker.open_position(fill=fill)
    tracker.update_lot_size("trade_001", 0.19)

    closed = tracker.close_position("trade_001", close_price=1.10100, reason="tp_hit")
    assert closed is not None
    assert closed.status == "CLOSED"
    assert closed.close_price == 1.10100
    assert closed.close_reason == "tp_hit"
    assert closed.close_time is not None
    assert closed.realized_pnl is not None  # points


def test_buy_pnl_positive_when_price_rises() -> None:
    tracker = PaperPositionTracker()
    fill = _make_fill("t1", side="BUY")
    tracker.open_position(fill=fill)
    tracker.update_lot_size("t1", 1.0)

    closed = tracker.close_position("t1", close_price=1.10200, reason="tp")
    assert closed is not None
    # BUY: close - open = 1.10200 - 1.10002 = positive
    assert closed.realized_pnl > 0


def test_sell_pnl_positive_when_price_falls() -> None:
    tracker = PaperPositionTracker()
    fill = _make_fill("t1", side="SELL")
    tracker.open_position(fill=fill)
    tracker.update_lot_size("t1", 1.0)

    closed = tracker.close_position("t1", close_price=1.09800, reason="tp")
    assert closed is not None
    # SELL: open - close = 1.09998 - 1.09800 = positive
    assert closed.realized_pnl > 0


def test_unrealized_pnl_computed_correctly() -> None:
    tracker = PaperPositionTracker()
    fill = _make_fill("t1", side="BUY")
    tracker.open_position(fill=fill)

    pnl = tracker.compute_unrealized_pnl("t1", current_price=1.10100)
    assert pnl is not None
    assert pnl > 0  # price rose


def test_close_nonexistent_returns_none() -> None:
    tracker = PaperPositionTracker()
    assert tracker.close_position("missing", 1.10000, "test") is None


def test_close_already_closed_returns_none() -> None:
    tracker = PaperPositionTracker()
    fill = _make_fill("t1")
    tracker.open_position(fill=fill)
    tracker.close_position("t1", 1.10000, "test")
    assert tracker.close_position("t1", 1.10000, "test") is None


def test_all_positions_returns_both_open_and_closed() -> None:
    tracker = PaperPositionTracker()
    tracker.open_position(fill=_make_fill("open_1"))
    tracker.open_position(fill=_make_fill("closed_1"))
    tracker.close_position("closed_1", 1.10000, "test")

    all_pos = tracker.get_all_positions()
    assert len(all_pos) == 2
    assert len(tracker.get_open_positions()) == 1
    assert len(tracker.get_closed_positions()) == 1
