"""Reconciliation tests for the per-leg partial-close booking fix.

Validates the contract: realized PnL for a trade_id is the SUM of close_pnl
across all trade_partial_close records plus the final trade_close record.
Before the fix, only the runner's final close was booked and the partial
leg's PnL was silently dropped, understating realized PnL on scaled trades.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.execution.live_position_tracker import LivePosition
from src.execution.live_session import _build_partial_close_record


@dataclass
class _Event:
    ticket: int
    symbol: str
    timestamp: str
    detail: dict
    event_type: str = "partial_close_sent"


def _pos(ticket: int = 479017919) -> LivePosition:
    return LivePosition(
        ticket=ticket,
        trade_id="5c73f56b-d44e-540d-86eb-4a9fd0892f5f",
        decision_id="live_scan_loop_052_AUDJPY_dec",
        symbol="AUDJPY",
        side="SELL",
        lot_size=2.66,
        open_price=112.835,
        current_price=112.733,
        sl=112.986,
        tp=112.173,
        profit=0.0,
        swap=0.0,
    )


def test_partial_record_books_leg_pnl_and_identity() -> None:
    ev = _Event(
        ticket=479017919,
        symbol="AUDJPY",
        timestamp="2026-06-23T04:00:00+00:00",
        detail={"volume": 2.66, "closed_volume": 2.66, "price": 112.733,
                "realized_pnl": 164.65, "deal": 5555},
    )
    rec = _build_partial_close_record(ev, _pos(), run_id="live_scan_loop_061")

    assert rec is not None
    assert rec["event"] == "trade_partial_close"
    assert rec["trade_id"] == "5c73f56b-d44e-540d-86eb-4a9fd0892f5f"
    assert rec["ticket"] == 479017919
    assert rec["close_pnl"] == 164.65
    assert rec["lot_size"] == 2.66
    assert rec["status"] == "partial_closed"


def test_realized_pnl_reconciles_partial_plus_runner() -> None:
    # AUDJPY broker truth: full position +457.66 on 5.32 lots,
    # split as partial leg +164.65 (2.66) and runner +293.01 (2.66).
    partial_ev = _Event(
        ticket=479017919, symbol="AUDJPY",
        timestamp="2026-06-23T04:00:00+00:00",
        detail={"closed_volume": 2.66, "price": 112.733, "realized_pnl": 164.65},
    )
    partial_rec = _build_partial_close_record(partial_ev, _pos(), run_id="r1")

    # The runner's final close record (written by the tracker close path).
    final_close_pnl = 293.01

    realized = (partial_rec["close_pnl"] or 0.0) + final_close_pnl
    assert round(realized, 2) == 457.66


def test_no_record_without_trade_id() -> None:
    pos = _pos()
    pos.trade_id = ""  # orphaned position with no identity
    ev = _Event(ticket=1, symbol="AUDJPY", timestamp="t",
                detail={"realized_pnl": 10.0})
    assert _build_partial_close_record(ev, pos, run_id="r1") is None


def test_no_record_without_position() -> None:
    ev = _Event(ticket=1, symbol="AUDJPY", timestamp="t",
                detail={"realized_pnl": 10.0})
    assert _build_partial_close_record(ev, None, run_id="r1") is None
