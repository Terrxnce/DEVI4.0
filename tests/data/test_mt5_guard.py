"""Tests for MT5 paper safety guard.

Proves that forbidden broker methods are blocked in paper mode.
"""
from __future__ import annotations

import pytest

from src.data.mt5_guard import FORBIDDEN_METHODS, MT5BrokerMethodForbidden, MT5PaperGuard


class FakeMT5:
    """Fake MT5 client with some allowed and some forbidden methods."""

    def initialize(self):
        return True

    def shutdown(self):
        pass

    def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
        return []

    def symbol_info(self, symbol):
        return {"point": 0.00001}

    def symbol_info_tick(self, symbol):
        return {"bid": 1.1, "ask": 1.10015}

    def account_info(self):
        return {"balance": 10000.0}

    def order_send(self, request):
        return {"retcode": 10009}

    def order_check(self, request):
        return {"retcode": 0}

    def order_modify(self, ticket, price, sl, tp, expiration):
        return {"retcode": 10009}

    def position_close(self, ticket):
        return {"retcode": 10009}

    def positions_total(self):
        return 0

    TIMEFRAME_M15 = "M15"
    TIMEFRAME_H1 = "H1"
    TIMEFRAME_H4 = "H4"


def test_allowed_methods_pass_through() -> None:
    """Allowed data methods work through the guard."""
    guard = MT5PaperGuard(FakeMT5())

    assert guard.initialize() is True
    assert guard.copy_rates_from_pos("EURUSD", "M15", 0, 10) == []
    assert guard.symbol_info("EURUSD") == {"point": 0.00001}
    assert guard.account_info() == {"balance": 10000.0}
    assert guard.symbol_info_tick("EURUSD") == {"bid": 1.1, "ask": 1.10015}


def test_forbidden_methods_are_blocked() -> None:
    """Forbidden broker methods raise MT5BrokerMethodForbidden."""
    guard = MT5PaperGuard(FakeMT5())

    for method in FORBIDDEN_METHODS:
        with pytest.raises(MT5BrokerMethodForbidden) as exc_info:
            getattr(guard, method)
        assert "forbidden in paper mode" in str(exc_info.value)
        assert method in str(exc_info.value)


def test_all_expected_forbidden_methods_covered() -> None:
    """The forbidden set covers all broker execution methods."""
    expected = {
        "order_send",
        "order_check",
        "order_modify",
        "position_close",
        "position_modify",
        "positions_total",
    }
    assert expected.issubset(FORBIDDEN_METHODS), (
        f"Missing forbidden methods: {expected - FORBIDDEN_METHODS}"
    )


def test_guard_does_not_break_class_identity() -> None:
    """The guard preserves class identity for MT5DataSource compatibility."""
    guard = MT5PaperGuard(FakeMT5())
    assert guard.__class__.__name__ == "FakeMT5"
