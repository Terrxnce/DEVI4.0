"""Controlled paper fill tests using deterministic TradeIntent fixtures.

Proves that PaperExecutionAdapter creates simulated fills correctly
for both BUY and SELL, with proper MT5-derived pricing logic.
"""
from __future__ import annotations

import pytest

from src.core.enums import Direction
from src.execution.paper_adapter import PaperExecutionAdapter
from tests.fixtures.trade_intent import make_trade_intent


def test_buy_fill_uses_ask_side_logic() -> None:
    """BUY fill: actual_fill = entry_price + half_spread."""
    adapter = PaperExecutionAdapter()
    intent = make_trade_intent(
        side="BUY",
        entry_price=1.10000,
        stop_loss=1.09800,
        take_profit=1.10300,
        lot_size=0.19,
    )
    spread = 0.00020

    fill = adapter.execute(intent, spread_at_decision=spread)

    expected_fill = 1.10000 + spread  # 1.10020 (ask)
    assert fill.side == "BUY"
    assert fill.symbol == "EURUSD"
    assert fill.decision_id == intent.trade_id
    assert fill.trade_id == intent.trade_id
    assert fill.ticket >= 100001
    assert fill.intended_entry == 1.10000
    assert round(fill.actual_fill - expected_fill, 10) < 1e-9
    assert fill.planned_sl == 1.09800
    assert fill.planned_tp == 1.10300
    assert round(fill.slippage, 10) == round(spread, 10)
    assert fill.spread_at_decision == spread
    assert fill.spread_at_fill == spread
    assert fill.order_status == "FILLED"
    assert fill.paper_retcode == 10009


def test_sell_fill_uses_bid_side_logic() -> None:
    """SELL fill: actual_fill = entry_price - spread (bid)."""
    adapter = PaperExecutionAdapter()
    intent = make_trade_intent(
        side="SELL",
        entry_price=1.10000,
        stop_loss=1.10200,
        take_profit=1.09700,
        lot_size=0.19,
    )
    spread = 0.00020

    fill = adapter.execute(intent, spread_at_decision=spread)

    expected_fill = 1.10000 - spread  # 1.09980 (bid)
    assert fill.side == "SELL"
    assert fill.decision_id == intent.trade_id
    assert abs(fill.actual_fill - expected_fill) < 1e-9
    assert round(fill.slippage, 10) == round(-spread, 10)
    assert fill.order_status == "FILLED"


def test_trade_id_links_to_decision_id() -> None:
    """Paper fill trade_id must match the TradeIntent trade_id (decision_id)."""
    adapter = PaperExecutionAdapter()
    intent = make_trade_intent(
        side="BUY",
        entry_price=1.10000,
        stop_loss=1.09800,
        take_profit=1.10300,
        lot_size=0.19,
        decision_id="dec_test_123",
    )
    fill = adapter.execute(intent, spread_at_decision=0.0002)

    assert fill.trade_id == "dec_test_123"
    assert fill.decision_id == "dec_test_123"


def test_fill_ticket_is_synthetic_and_increments() -> None:
    """Each fill gets a unique synthetic ticket number."""
    adapter = PaperExecutionAdapter()

    intent1 = make_trade_intent(
        side="BUY", entry_price=1.10000, stop_loss=1.09800,
        take_profit=1.10300, lot_size=0.19, decision_id="dec_001",
    )
    intent2 = make_trade_intent(
        side="BUY", entry_price=1.10010, stop_loss=1.09810,
        take_profit=1.10310, lot_size=0.19, decision_id="dec_002",
    )

    fill1 = adapter.execute(intent1, spread_at_decision=0.0002)
    fill2 = adapter.execute(intent2, spread_at_decision=0.0002)

    assert fill1.ticket >= 100001
    assert fill2.ticket >= 100001
    assert fill2.ticket > fill1.ticket


def test_slippage_is_math_based_not_broker_derived() -> None:
    """Slippage is calculated from entry vs fill price, not from broker."""
    adapter = PaperExecutionAdapter()
    intent = make_trade_intent(
        side="BUY", entry_price=1.10000, stop_loss=1.09800,
        take_profit=1.10300, lot_size=0.19,
    )
    spread = 0.00030
    fill = adapter.execute(intent, spread_at_decision=spread)

    # Slippage = fill_price - entry_price = spread (cost of crossing spread)
    assert round(fill.slippage, 10) == round(spread, 10)
    # No broker interaction occurred
    assert fill.paper_retcode == 10009  # synthetic paper placeholder


def test_paper_retcode_is_placeholder_not_real() -> None:
    """paper_retcode is a synthetic placeholder, never from a real broker."""
    adapter = PaperExecutionAdapter()
    intent = make_trade_intent(
        side="BUY", entry_price=1.10000, stop_loss=1.09800,
        take_profit=1.10300, lot_size=0.19,
    )
    fill = adapter.execute(intent, spread_at_decision=0.0002)

    assert fill.paper_retcode == 10009


def test_adapter_has_no_broker_connection() -> None:
    """PaperExecutionAdapter has no broker or MT5 connection attributes."""
    adapter = PaperExecutionAdapter()

    assert not hasattr(adapter, "_broker")
    assert not hasattr(adapter, "_mt5")
    assert not hasattr(adapter, "_connection")
    assert not hasattr(adapter, "order_send")
