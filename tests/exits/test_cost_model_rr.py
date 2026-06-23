"""Tests for the cost-model net-RR adjustment in the exit planner.

Net RR deducts round-turn commission from the reward and adds it to the risk
before the min_rr gate, so a trade's RR is measured net of cost. With no
cost_model configured, commission is 0 and net RR == gross RR (exact parity).
"""
from __future__ import annotations

from src.exits.planner import _commission_price_distance, _net_rr


# ---------------------------------------------------------------------------
# _commission_price_distance
# ---------------------------------------------------------------------------


def test_commission_zero_when_no_cost_model() -> None:
    cfg = {"instrument": {"tick_value": 1.0, "tick_size": 0.00001}}
    assert _commission_price_distance(cfg) == 0.0


def test_commission_zero_when_commission_zero() -> None:
    cfg = {
        "cost_model": {"commission_per_lot_round_turn": 0.0},
        "instrument": {"tick_value": 1.0, "tick_size": 0.00001},
    }
    assert _commission_price_distance(cfg) == 0.0


def test_commission_zero_when_instrument_tick_data_missing() -> None:
    # No tick data -> cannot convert to price units -> 0 (parity, never blocks)
    cfg = {"cost_model": {"commission_per_lot_round_turn": 7.0}, "instrument": {}}
    assert _commission_price_distance(cfg) == 0.0


def test_commission_price_distance_value() -> None:
    # $7 round-turn, EURUSD-like: tick_value=$1 per 0.00001 tick.
    # c = 7 * 0.00001 / 1.0 = 0.00007 price = 0.7 pip = the $7 expressed in price.
    cfg = {
        "cost_model": {"commission_per_lot_round_turn": 7.0},
        "instrument": {"tick_value": 1.0, "tick_size": 0.00001},
    }
    assert abs(_commission_price_distance(cfg) - 0.00007) < 1e-12


# ---------------------------------------------------------------------------
# _net_rr
# ---------------------------------------------------------------------------


def test_net_rr_parity_when_no_commission() -> None:
    # c == 0 returns gross exactly
    assert _net_rr(1.25, 0.0010, 0.0) == 1.25
    assert _net_rr(3.0, 0.0005, 0.0) == 3.0


def test_net_rr_reduces_with_commission() -> None:
    # gross 1.25 on a 10-pip (0.0010) stop, $7 commission (c=0.00007)
    # net = (1.25*0.0010 - 0.00007) / (0.0010 + 0.00007) = 0.00118/0.00107
    net = _net_rr(1.25, 0.0010, 0.00007)
    assert abs(net - 1.10280) < 1e-4
    assert net < 1.25


def test_net_rr_marginal_trade_flips_below_floor() -> None:
    # A 1.25 gross trade fails a 1.2 min_rr once commission is netted in.
    min_rr = 1.2
    gross = 1.25
    risk = 0.0010
    assert gross >= min_rr  # passes gross
    assert _net_rr(gross, risk, 0.00007) < min_rr  # fails net ($7)
    assert _net_rr(gross, risk, 0.00003) < min_rr  # fails net even at $3


def test_net_rr_high_rr_trade_survives() -> None:
    # A 3.0 RR trade is barely affected by commission and stays well above floor.
    net = _net_rr(3.0, 0.0010, 0.00007)
    assert net > 2.7
    assert net < 3.0


def test_net_rr_is_price_based_not_lot_based() -> None:
    # net_rr depends only on price distances and the price-equivalent cost,
    # never on lot size — so it is identical regardless of account/position size.
    # Same gross RR + same proportional cost -> same net RR at any risk scale.
    a = _net_rr(1.5, 0.0010, 0.00007)
    b = _net_rr(1.5, 0.0020, 0.00014)  # double risk, double price-cost
    assert abs(a - b) < 1e-9


def test_net_rr_denominator_guard() -> None:
    # Degenerate inputs never raise; fall back to gross.
    assert _net_rr(1.5, 0.0, 0.0) == 1.5
