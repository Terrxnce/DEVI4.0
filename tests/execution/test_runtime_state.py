"""Tests for RuntimeState order/decision tracking."""
from __future__ import annotations

from src.core.runtime_state import RuntimeState


def test_runtime_state_starts_empty() -> None:
    rs = RuntimeState(run_id="test_001")
    assert rs.orders_this_run == 0
    assert rs.decision_count == 0
    assert rs.trade_count == 0
    assert rs.can_place_order(max_orders=1) is True


def test_record_decision_increments_count() -> None:
    rs = RuntimeState()
    rs.record_decision("dec_001")
    rs.record_decision("dec_002")
    assert rs.decision_count == 2
    assert rs.has_decision("dec_001")
    assert not rs.has_decision("dec_999")


def test_record_trade_increments_orders() -> None:
    rs = RuntimeState()
    rs.record_trade("trade_001")
    rs.record_trade("trade_002")
    assert rs.orders_this_run == 2
    assert rs.trade_count == 2
    assert rs.has_trade("trade_001")
    assert not rs.has_trade("trade_999")


def test_can_place_order_respects_max() -> None:
    rs = RuntimeState()
    assert rs.can_place_order(max_orders=1) is True
    rs.record_trade("t1")
    assert rs.can_place_order(max_orders=1) is False
    assert rs.can_place_order(max_orders=2) is True
    rs.record_trade("t2")
    assert rs.can_place_order(max_orders=2) is False


def test_reset_per_run() -> None:
    rs1 = RuntimeState(run_id="run_a")
    rs1.record_trade("t1")
    assert rs1.orders_this_run == 1

    rs2 = RuntimeState(run_id="run_b")
    assert rs2.orders_this_run == 0
    assert rs2.trade_count == 0
