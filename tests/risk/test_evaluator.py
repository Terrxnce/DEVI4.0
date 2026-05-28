from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from src.core.enums import Direction, HTFAgreement, Regime, Session
from src.core.models import ContextSnapshot
from src.risk.evaluator import evaluate_risk


def _context() -> ContextSnapshot:
    return ContextSnapshot(
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


def _config() -> dict:
    cfg = json.loads(Path("src/config/defaults.json").read_text(encoding="utf-8"))
    cfg["instrument"] = {
        "point": 0.00001,
        "tick_size": 0.00001,
        "tick_value": 1.0,  # EURUSD/USD account: 1 tick = $1 per lot
        "contract_size": 100000.0,
        "lot_step": 0.01,
        "min_lot": 0.01,
        "max_lot": 100.0,
    }
    return cfg


def _eval_risk(entry_price: float = 1.1000, stop_loss: float = 1.0980, state: dict | None = None):
    return evaluate_risk(
        context=_context(),
        config=_config(),
        entry_price=entry_price,
        stop_loss=stop_loss,
        state=state,
    )


def test_risk_approved_default_state() -> None:
    verdict = _eval_risk()

    assert verdict.approved is True
    assert verdict.reason == "approved"
    assert verdict.intended_risk_pct == 0.4
    assert verdict.actual_risk_pct == 0.4
    assert verdict.lot_size > 0.0


def test_risk_real_lot_sizing_calculation() -> None:
    # EURUSD: 0.4% risk on $10000 = $40 risk
    # SL = 20 pips (0.0020) at 1.1000 -> stop_loss = 1.0980
    # point = 0.00001, contract_size = 100000
    # loss_per_lot = (0.0020 / 0.00001) * (100000 * 0.00001) = 200 * 1.0 = $200
    # lot_size = $40 / $200 = 0.2
    verdict = _eval_risk(entry_price=1.1000, stop_loss=1.0980)

    assert verdict.approved is True
    assert verdict.lot_size == 0.2


def test_risk_soft_reduction_when_daily_drawdown_hit() -> None:
    verdict = _eval_risk(state={"daily_pnl_pct": -1.6})

    assert verdict.approved is True
    assert verdict.reason == "soft_reduction_active"
    assert verdict.intended_risk_pct == 0.4
    assert verdict.actual_risk_pct == 0.2
    # With halved risk (0.2%), lot_size = $20 / $200 = 0.1
    assert verdict.lot_size == 0.1


def test_risk_rejects_at_daily_block_threshold() -> None:
    verdict = _eval_risk(state={"daily_pnl_pct": -3.0})

    assert verdict.approved is False
    assert verdict.reason == "daily_drawdown_block"


def test_risk_rejects_at_total_force_close_threshold() -> None:
    verdict = _eval_risk(state={"total_pnl_pct": -9.5})

    assert verdict.approved is False
    assert verdict.reason == "total_drawdown_force_close"


def test_risk_rejects_max_open_positions_total() -> None:
    verdict = _eval_risk(state={"open_positions_total": 3})

    assert verdict.approved is False
    assert verdict.reason == "max_open_positions_total"


def test_risk_rejects_same_direction_correlation_cap() -> None:
    verdict = _eval_risk(state={"same_direction_correlated_positions": 1})

    assert verdict.approved is False
    assert verdict.reason == "same_direction_correlation_cap"


def test_risk_rejects_lot_size_calculation_failure() -> None:
    verdict = _eval_risk(entry_price=0.0, stop_loss=1.0980)

    assert verdict.approved is False
    assert verdict.reason == "lot_size_calculation_failed"


def test_risk_deviation_check_rejects_excessive_slippage() -> None:
    # Very tight SL that causes rounding to produce high deviation
    # entry=1.1000, sl=1.09995 (0.5 pips), lot_step=0.01
    # risk_amount = $40, loss_per_lot = 5 * 1.0 = $5, lot_raw = 8.0, stepped = 8.0 (max_lot=100)
    # actual_risk = 8.0 * $5 / $10000 * 100 = 4.0%, deviation = (4.0 - 0.4) / 0.4 = 900%
    # Actually this would fail the deviation check. Let me use a more realistic case.

    # Use a config with lot_step=1.0 (forcing big rounding) to trigger deviation
    cfg = _config()
    cfg["instrument"]["lot_step"] = 1.0
    cfg["instrument"]["min_lot"] = 1.0

    verdict = evaluate_risk(
        context=_context(),
        config=cfg,
        entry_price=1.1000,
        stop_loss=1.0980,
    )

    # lot_raw = $40 / $200 = 0.2, but lot_step=1.0 so stepped=0.0 -> below min_lot -> clamped to 1.0
    # actual_risk with lot=1.0 = $200 / $10000 * 100 = 2.0%
    # deviation = |2.0 - 0.4| / 0.4 = 4.0 = 400% > 20%
    assert verdict.approved is False
    assert verdict.reason == "risk_deviation_exceeded"


# ---------------------------------------------------------------------------
# Fixed lot mode
# ---------------------------------------------------------------------------


def _config_fixed_lot(fixed_lot_size: float = 0.01) -> dict:
    cfg = _config()
    cfg["risk"]["dynamic_lot_sizing"] = False
    cfg["risk"]["fixed_lot_size"] = fixed_lot_size
    return cfg


def test_fixed_lot_returns_configured_lot() -> None:
    """When dynamic_lot_sizing=False, evaluator returns fixed_lot_size regardless of balance/SL."""
    cfg = _config_fixed_lot(fixed_lot_size=0.01)
    verdict = evaluate_risk(
        context=_context(),
        config=cfg,
        entry_price=1.1000,
        stop_loss=1.0980,
    )
    assert verdict.approved is True
    assert verdict.lot_size == 0.01
    assert verdict.reason == "fixed_lot_approved"


def test_fixed_lot_ignores_balance_and_sl_distance() -> None:
    """Fixed lot must not change when balance or SL distance changes."""
    cfg = _config_fixed_lot(fixed_lot_size=0.05)
    # Large balance, tight SL — dynamic would give a big lot
    verdict = evaluate_risk(
        context=_context(),
        config=cfg,
        entry_price=1.1000,
        stop_loss=1.09998,  # 0.2 pip SL
        state={"account_balance": 500000.0},
    )
    assert verdict.approved is True
    assert verdict.lot_size == 0.05
    assert verdict.reason == "fixed_lot_approved"


def test_fixed_lot_still_blocks_on_drawdown() -> None:
    """Drawdown gates must still apply even in fixed lot mode."""
    cfg = _config_fixed_lot(fixed_lot_size=0.01)
    verdict = evaluate_risk(
        context=_context(),
        config=cfg,
        entry_price=1.1000,
        stop_loss=1.0980,
        state={"daily_pnl_pct": -3.0},
    )
    assert verdict.approved is False
    assert verdict.reason == "daily_drawdown_block"


def test_fixed_lot_still_blocks_on_position_limit() -> None:
    """Position limits must still apply in fixed lot mode."""
    cfg = _config_fixed_lot(fixed_lot_size=0.01)
    verdict = evaluate_risk(
        context=_context(),
        config=cfg,
        entry_price=1.1000,
        stop_loss=1.0980,
        state={"open_positions_total": 3},
    )
    assert verdict.approved is False
    assert verdict.reason == "max_open_positions_total"


def test_fixed_lot_invalid_zero_returns_failure() -> None:
    cfg = _config_fixed_lot(fixed_lot_size=0.0)
    verdict = evaluate_risk(
        context=_context(),
        config=cfg,
        entry_price=1.1000,
        stop_loss=1.0980,
    )
    assert verdict.approved is False
    assert verdict.reason == "fixed_lot_invalid"


def test_fixed_lot_out_of_bounds_returns_failure() -> None:
    """fixed_lot_size below broker min_lot must be rejected."""
    cfg = _config_fixed_lot(fixed_lot_size=0.001)
    cfg["instrument"]["min_lot"] = 0.01
    verdict = evaluate_risk(
        context=_context(),
        config=cfg,
        entry_price=1.1000,
        stop_loss=1.0980,
    )
    assert verdict.approved is False
    assert verdict.reason == "fixed_lot_out_of_bounds"


# ---------------------------------------------------------------------------
# JPY pip value tests (P0 bug fix verification)
# ---------------------------------------------------------------------------


def _config_jpy(balance: float = 100000.0) -> dict:
    """USDJPY instrument profile, USD account, price ~150.00.

    MT5 trade_tick_value for USDJPY at 150:
        contract_size * tick_size / price = 100000 * 0.001 / 150 ≈ 0.66667 USD per tick per lot
    """
    cfg = json.loads(Path("src/config/defaults.json").read_text(encoding="utf-8"))
    cfg["risk"]["risk_per_trade_pct"] = 0.5
    cfg["instrument"] = {
        "point": 0.001,
        "tick_size": 0.001,
        "tick_value": 0.66667,  # ~100000 * 0.001 / 150 USD per tick per lot
        "contract_size": 100000.0,
        "lot_step": 0.01,
        "min_lot": 0.01,
        "max_lot": 200.0,
    }
    return cfg


def test_jpy_lot_sizing_is_in_account_currency() -> None:
    """USDJPY lot sizing must produce a sensible USD-denominated lot size.

    0.5% of $100,000 = $500 risk.
    SL = 50 pips = 0.500 price distance on USDJPY (5-digit broker: 500 points).
    sl_points = 0.500 / 0.001 = 500
    point_value = 0.66667 * (0.001 / 0.001) = 0.66667 USD per point
    loss_per_lot = 500 * 0.66667 = 333.33 USD
    lot_size = 500 / 333.33 ≈ 1.50 lots (stepped to 1.50)
    """
    cfg = _config_jpy(balance=100000.0)
    verdict = evaluate_risk(
        context=_context(),
        config=cfg,
        entry_price=150.000,
        stop_loss=149.500,  # 50 pip SL
        state={"account_balance": 100000.0},
    )
    assert verdict.approved is True
    # Lots must be in the 1-3 range — NOT 0.01-0.05 (the old broken result)
    assert verdict.lot_size >= 1.0, f"JPY lot too small: {verdict.lot_size} — formula still broken"
    assert verdict.lot_size <= 5.0, f"JPY lot unexpectedly large: {verdict.lot_size}"


def test_jpy_lot_sizing_not_microsized() -> None:
    """The old formula produced lots ~100x too small for JPY pairs.
    Verify the fixed formula never returns a sub-0.10 lot at 0.5% risk on $100k with a normal SL.
    """
    cfg = _config_jpy(balance=100000.0)
    verdict = evaluate_risk(
        context=_context(),
        config=cfg,
        entry_price=150.000,
        stop_loss=149.700,  # 30 pip SL
        state={"account_balance": 100000.0},
    )
    assert verdict.approved is True
    assert verdict.lot_size >= 0.5, f"JPY lot is micro-sized: {verdict.lot_size}"


def test_jpy_lot_sizing_scales_with_balance() -> None:
    """Lot size should roughly double when balance doubles."""
    cfg_50k = _config_jpy(balance=50000.0)
    cfg_100k = _config_jpy(balance=100000.0)

    v50 = evaluate_risk(
        context=_context(),
        config=cfg_50k,
        entry_price=150.000,
        stop_loss=149.500,
        state={"account_balance": 50000.0},
    )
    v100 = evaluate_risk(
        context=_context(),
        config=cfg_100k,
        entry_price=150.000,
        stop_loss=149.500,
        state={"account_balance": 100000.0},
    )
    assert v50.approved is True
    assert v100.approved is True
    # $100k lot should be ~2x the $50k lot (within lot_step rounding)
    ratio = v100.lot_size / v50.lot_size
    assert 1.8 <= ratio <= 2.2, f"Lot scaling off: {v50.lot_size} vs {v100.lot_size} (ratio {ratio:.2f})"


# ---------------------------------------------------------------------------
# USD correlation cap
# ---------------------------------------------------------------------------


def test_usd_correlation_cap_blocks_at_limit() -> None:
    """When usd_correlated_positions >= max_usd_correlated_positions, reject."""
    cfg = _config()
    cfg["risk"]["max_usd_correlated_positions"] = 1
    verdict = evaluate_risk(
        context=_context(),
        config=cfg,
        entry_price=1.1000,
        stop_loss=1.0980,
        state={"usd_correlated_positions": 1},
    )
    assert verdict.approved is False
    assert verdict.reason == "usd_correlation_cap"


def test_usd_correlation_cap_allows_below_limit() -> None:
    """When usd_correlated_positions < max_usd_correlated_positions, allow."""
    cfg = _config()
    cfg["risk"]["max_usd_correlated_positions"] = 2
    verdict = evaluate_risk(
        context=_context(),
        config=cfg,
        entry_price=1.1000,
        stop_loss=1.0980,
        state={"usd_correlated_positions": 1},
    )
    assert verdict.approved is True


def test_usd_correlation_cap_absent_means_no_limit() -> None:
    """If max_usd_correlated_positions is not present in config, cap is not enforced."""
    cfg = _config()
    cfg["risk"].pop("max_usd_correlated_positions", None)
    verdict = evaluate_risk(
        context=_context(),
        config=cfg,
        entry_price=1.1000,
        stop_loss=1.0980,
        state={"usd_correlated_positions": 99},
    )
    assert verdict.approved is True


# ---------------------------------------------------------------------------
# JPY correlation cap
# ---------------------------------------------------------------------------


def test_jpy_correlation_cap_blocks_at_limit() -> None:
    """When jpy_correlated_positions >= max_jpy_correlated_positions, reject.

    Real scenario: CADJPY + USDJPY both open in Asia. A third JPY pair
    (e.g. EURJPY) must be blocked.
    """
    cfg = _config()
    cfg["risk"]["max_jpy_correlated_positions"] = 2
    verdict = evaluate_risk(
        context=_context(),
        config=cfg,
        entry_price=1.1000,
        stop_loss=1.0980,
        state={"jpy_correlated_positions": 2},
    )
    assert verdict.approved is False
    assert verdict.reason == "jpy_correlation_cap"


def test_jpy_correlation_cap_allows_below_limit() -> None:
    """When jpy_correlated_positions < max_jpy_correlated_positions, allow."""
    cfg = _config()
    cfg["risk"]["max_jpy_correlated_positions"] = 2
    verdict = evaluate_risk(
        context=_context(),
        config=cfg,
        entry_price=1.1000,
        stop_loss=1.0980,
        state={"jpy_correlated_positions": 1},
    )
    assert verdict.approved is True


def test_jpy_correlation_cap_absent_means_no_limit() -> None:
    """If max_jpy_correlated_positions is not present in config, cap is not enforced."""
    cfg = _config()
    cfg["risk"].pop("max_jpy_correlated_positions", None)
    verdict = evaluate_risk(
        context=_context(),
        config=cfg,
        entry_price=1.1000,
        stop_loss=1.0980,
        state={"jpy_correlated_positions": 99},
    )
    assert verdict.approved is True


def test_jpy_cap_independent_of_usd_cap() -> None:
    """JPY cap and USD cap are independent — hitting JPY cap does not affect USD cap verdict."""
    cfg = _config()
    cfg["risk"]["max_jpy_correlated_positions"] = 2
    cfg["risk"]["max_usd_correlated_positions"] = 3
    # USD under cap, JPY at cap — should block on JPY
    verdict = evaluate_risk(
        context=_context(),
        config=cfg,
        entry_price=1.1000,
        stop_loss=1.0980,
        state={"usd_correlated_positions": 1, "jpy_correlated_positions": 2},
    )
    assert verdict.approved is False
    assert verdict.reason == "jpy_correlation_cap"


def test_usd_correlation_cap_zero_blocks_all() -> None:
    """Cap of 0 means no USD positions allowed at all."""
    cfg = _config()
    cfg["risk"]["max_usd_correlated_positions"] = 0
    verdict = evaluate_risk(
        context=_context(),
        config=cfg,
        entry_price=1.1000,
        stop_loss=1.0980,
        state={"usd_correlated_positions": 0},
    )
    assert verdict.approved is False
    assert verdict.reason == "usd_correlation_cap"


def test_usd_correlation_cap_defaults_json_is_permissive() -> None:
    """defaults.json cap of 99 should not block a normal session."""
    verdict = _eval_risk(state={"usd_correlated_positions": 4})
    assert verdict.approved is True
