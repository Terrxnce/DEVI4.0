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
