"""Tests for run_summary outcome matching and classification.

Covers the reporting bugs found on 2026-05-25:
  - P&L cloning across same-symbol trades (USDJPY x2, GBPUSD x3)
  - Partial close profit not summed (XAUUSD)
  - Profit-sign fallback causing false TP_HIT/SL_HIT labels
  - SESSION_CLOSE label for managed exits
"""
from __future__ import annotations

import pytest

from src.ops.run_summary import _classify_outcome, match_outcomes


# ---------------------------------------------------------------------------
# _classify_outcome
# ---------------------------------------------------------------------------

class TestClassifyOutcome:
    def test_tp_hit_by_proximity(self):
        # Close within 5% of TP-SL range from TP side
        # Range = 0.00300, tolerance = 0.00015, |close - tp| = 0.00001 => TP_HIT
        assert _classify_outcome(1.10049, 1.10050, 1.09750, 80.0) == "TP_HIT"

    def test_sl_hit_by_proximity(self):
        # Close within 5% of TP-SL range from SL side
        # Range = 0.00300, tolerance = 0.00015, |close - sl| = 0.00002 => SL_HIT
        assert _classify_outcome(1.09752, 1.10050, 1.09750, -80.0) == "SL_HIT"

    def test_session_close_midrange_profit(self):
        # Profitable exit but price nowhere near TP — must be SESSION_CLOSE not TP_HIT
        # This was the bug: GBPUSD closed at 1.34954 (TP=1.35323, SL=1.34736) → was labelled TP_HIT
        assert _classify_outcome(1.34954, 1.35323, 1.34736, 483.0) == "SESSION_CLOSE"

    def test_session_close_midrange_loss(self):
        # Small loss but price not near SL — must be SESSION_CLOSE not SL_HIT
        # CADJPY closed at 115.102 (SL=115.213, TP=114.921) with profit +19.55 → was labelled TP_HIT
        assert _classify_outcome(115.102, 114.921, 115.213, 19.55) == "SESSION_CLOSE"

    def test_no_profit_sign_fallback_on_profit(self):
        # Profitable trade but price unknown — must NOT return TP_HIT
        assert _classify_outcome(0.0, 0.0, 0.0, 500.0) == "SESSION_CLOSE"

    def test_no_profit_sign_fallback_on_loss(self):
        # Losing trade but price unknown — must NOT return SL_HIT
        assert _classify_outcome(0.0, 0.0, 0.0, -200.0) == "SESSION_CLOSE"

    def test_tp_sl_equal_falls_through(self):
        # Degenerate case: tp == sl, can't compute tolerance
        assert _classify_outcome(1.10000, 1.10000, 1.10000, 50.0) == "SESSION_CLOSE"

    def test_usdjpy_sl_proximity(self):
        # USDJPY trade 1: closed at 158.939, SL=158.965, TP=158.588
        # Range=0.377, tolerance=0.01885, |close-SL|=0.026 > tolerance => SESSION_CLOSE
        # (was incorrectly labelled SL_HIT under 10% tolerance)
        assert _classify_outcome(158.939, 158.588, 158.965, -367.71) == "SESSION_CLOSE"


# ---------------------------------------------------------------------------
# match_outcomes
# ---------------------------------------------------------------------------

def _order(symbol: str, ticket: int, tp: float, sl: float, **kw) -> dict:
    return {"symbol": symbol, "ticket": ticket, "take_profit": tp, "stop_loss": sl, **kw}


def _deal(symbol: str, ticket: int, profit: float, price: float) -> dict:
    return {"symbol": symbol, "ticket": ticket, "profit": profit, "price": price}


class TestMatchOutcomes:

    # --- Ticket-based matching ---

    def test_two_usdjpy_trades_get_distinct_deals(self):
        """USDJPY x2: second trade must NOT get the first trade's loss."""
        orders = [
            _order("USDJPY", 1001, 158.588, 158.965),
            _order("USDJPY", 1002, 158.724, 159.042),
        ]
        deals = [
            _deal("USDJPY", 1001, -367.71, 158.939),  # trade 1: loss
            _deal("USDJPY", 1002,   14.68, 158.903),  # trade 2: small win
        ]
        results = match_outcomes(orders, deals)
        assert len(results) == 2
        assert results[0]["profit"] == pytest.approx(-367.71)
        assert results[0]["outcome"] == "SESSION_CLOSE"
        assert results[1]["profit"] == pytest.approx(14.68)
        assert results[1]["outcome"] == "SESSION_CLOSE"

    def test_three_gbpusd_trades_get_distinct_deals(self):
        """GBPUSD x3: each trade gets its own deal — no cloning."""
        orders = [
            _order("GBPUSD", 3001, 1.35323, 1.34736),
            _order("GBPUSD", 3002, 1.35203, 1.34883),
            _order("GBPUSD", 3003, 1.35203, 1.34932),
        ]
        deals = [
            _deal("GBPUSD", 3001, 483.00, 1.34954),
            _deal("GBPUSD", 3002,   7.66, 1.35014),
            _deal("GBPUSD", 3003, 297.50, 1.35066),
        ]
        results = match_outcomes(orders, deals)
        assert results[0]["profit"] == pytest.approx(483.00)
        assert results[1]["profit"] == pytest.approx(7.66)
        assert results[2]["profit"] == pytest.approx(297.50)
        # All should be SESSION_CLOSE — none near TP/SL
        for r in results:
            assert r["outcome"] == "SESSION_CLOSE"

    def test_xauusd_summed_profit(self):
        """XAUUSD with partial close: profit already summed in _fetch_day_outcomes."""
        orders = [_order("XAUUSD", 2001, 4579.32, 4551.80)]
        deals = [_deal("XAUUSD", 2001, 660.00, 4571.889)]  # partial + final summed
        results = match_outcomes(orders, deals)
        assert results[0]["profit"] == pytest.approx(660.00)
        assert results[0]["outcome"] == "SESSION_CLOSE"

    def test_no_matching_deal_returns_running(self):
        orders = [_order("EURUSD", 9001, 1.10200, 1.09800)]
        results = match_outcomes(orders, [])
        assert results[0]["outcome"] == "RUNNING"
        assert results[0]["profit"] is None
        assert results[0]["close_price"] is None

    def test_tp_hit_correctly_identified(self):
        """A genuine TP hit: close price within 5% of range from TP."""
        orders = [_order("EURUSD", 5001, 1.10050, 1.09750)]
        # close at 1.10049 — 1 pip from TP, range=0.003, tolerance=0.00015 => TP_HIT
        deals = [_deal("EURUSD", 5001, 150.0, 1.10049)]
        results = match_outcomes(orders, deals)
        assert results[0]["outcome"] == "TP_HIT"

    def test_sl_hit_correctly_identified(self):
        """A genuine SL hit: close price within 5% of range from SL."""
        orders = [_order("EURUSD", 5002, 1.10050, 1.09750)]
        # close at 1.09752 — 2 pips from SL, range=0.003, tolerance=0.00015 => SL_HIT
        deals = [_deal("EURUSD", 5002, -150.0, 1.09752)]
        results = match_outcomes(orders, deals)
        assert results[0]["outcome"] == "SL_HIT"

    # --- Fallback symbol matching (no ticket) ---

    def test_symbol_fallback_consumes_deals_in_order(self):
        """Without tickets, deals are consumed in list order — no reuse."""
        orders = [
            {"symbol": "EURCHF", "take_profit": 0.91313, "stop_loss": 0.90986},
            {"symbol": "EURCHF", "take_profit": 0.92000, "stop_loss": 0.91500},
        ]
        deals = [
            {"symbol": "EURCHF", "profit": 219.73, "price": 0.91139},  # no ticket
            {"symbol": "EURCHF", "profit":  50.00, "price": 0.91600},
        ]
        results = match_outcomes(orders, deals)
        assert results[0]["profit"] == pytest.approx(219.73)
        assert results[1]["profit"] == pytest.approx(50.00)

    def test_symbol_fallback_second_has_no_deal(self):
        """Without tickets, second same-symbol order gets RUNNING if only one deal."""
        orders = [
            {"symbol": "GBPUSD", "take_profit": 1.352, "stop_loss": 1.347},
            {"symbol": "GBPUSD", "take_profit": 1.355, "stop_loss": 1.349},
        ]
        deals = [
            {"symbol": "GBPUSD", "profit": 300.0, "price": 1.350},  # only one deal
        ]
        results = match_outcomes(orders, deals)
        assert results[0]["outcome"] != "RUNNING"
        assert results[1]["outcome"] == "RUNNING"
