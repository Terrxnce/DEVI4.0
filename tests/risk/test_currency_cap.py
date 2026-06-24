"""Tests for the generalized per-currency exposure cap.

Caps how many open positions may share any single currency. Blocks a new trade
only when one of ITS currencies is already at the limit, so diversified trades
are unaffected. With no max_positions_per_currency config, nothing changes.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from src.core.enums import Direction, HTFAgreement, Regime, Session
from src.core.models import ContextSnapshot
from src.risk.evaluator import evaluate_risk
from src.risk.usd_correlation import currencies_in, currency_counts


# ----------------------- pure helpers ---------------------------------------


def test_currencies_in_basic():
    assert currencies_in("EURCHF") == {"EUR", "CHF"}
    assert currencies_in("USDJPY") == {"USD", "JPY"}
    assert currencies_in("GBPNZD") == {"GBP", "NZD"}


def test_currencies_in_excludes_metals_and_indices():
    assert currencies_in("XAUUSD") == set()
    assert currencies_in("XAGUSD") == set()
    assert currencies_in("US30.cash") == set()


def test_currencies_in_strips_broker_suffix():
    assert currencies_in("EURCHF.pi") == {"EUR", "CHF"}
    assert currencies_in("GBPUSDm") == {"GBP", "USD"}
    assert currencies_in("USDCHF.pro") == {"USD", "CHF"}


def test_currency_counts():
    c = currency_counts(["EURCHF", "GBPCHF", "USDCHF"])
    assert c["CHF"] == 3
    assert c["EUR"] == 1 and c["GBP"] == 1 and c["USD"] == 1


# ----------------------- evaluator integration ------------------------------


def _context(symbol: str) -> ContextSnapshot:
    return ContextSnapshot(
        symbol=symbol,
        bar_time=datetime(2026, 6, 24, 8, 0, tzinfo=UTC),
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


def _config(with_cap: bool) -> dict:
    cfg = json.loads(Path("src/config/defaults.json").read_text(encoding="utf-8"))
    cfg["instrument"] = {
        "point": 0.00001, "tick_size": 0.00001, "tick_value": 1.0,
        "contract_size": 100000.0, "lot_step": 0.01, "min_lot": 0.01, "max_lot": 100.0,
    }
    if with_cap:
        cfg["risk"]["max_positions_per_currency"] = {"default": 2, "USD": 3}
    return cfg


def _eval(symbol, counts, with_cap=True):
    return evaluate_risk(
        context=_context(symbol),
        config=_config(with_cap),
        entry_price=1.1000,
        stop_loss=1.0980,
        state={"currency_counts": counts},
    )


def test_blocks_candidate_sharing_maxed_currency():
    # 2 CHF already open, cap default 2 -> a new USDCHF (has CHF) is blocked.
    v = _eval("USDCHF", {"CHF": 2})
    assert v.approved is False
    assert v.reason == "currency_cap:CHF"


def test_allows_candidate_not_sharing_maxed_currency():
    # CHF maxed, but a new EURUSD shares neither CHF -> allowed.
    v = _eval("EURUSD", {"CHF": 2})
    assert v.approved is True


def test_usd_override_higher_than_default():
    # USD cap is 3; 2 USD open -> a new USD pair still allowed.
    assert _eval("EURUSD", {"USD": 2}).approved is True
    # 3 USD open -> blocked at the USD override.
    v = _eval("GBPUSD", {"USD": 3})
    assert v.approved is False and v.reason == "currency_cap:USD"


def test_no_cap_config_is_parity():
    # Without the config key, currency exposure is never capped.
    v = _eval("USDCHF", {"CHF": 9}, with_cap=False)
    assert v.approved is True
