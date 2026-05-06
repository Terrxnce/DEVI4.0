from __future__ import annotations

from dataclasses import replace

from src.core.enums import Direction, Regime, StructureType
from src.core.models import ExitPlan
from src.exits.validator import validate_exit_plan


def test_validator_rejects_invalid_sl_side(make_structure_fn, make_context_fn) -> None:
    plan = ExitPlan(
        stop_loss=1.1002,
        take_profit=1.1030,
        risk_reward=1.6,
        sl_source="ATR_FALLBACK",
        tp_source="M15_STRUCTURE",
        breakeven_trigger_r=1.0,
        session_close_exit=True,
    )

    ok, code = validate_exit_plan(
        entry_price=1.1000,
        direction=Direction.BULLISH,
        plan=plan,
        context=make_context_fn(regime=Regime.TRENDING),
        min_rr=1.3,
        min_rr_neutral=1.5,
    )

    assert not ok
    assert code == "invalid_sl_not_behind_entry"


def test_validator_rejects_invalid_tp_side(make_structure_fn, make_context_fn) -> None:
    plan = ExitPlan(
        stop_loss=1.0980,
        take_profit=1.0998,
        risk_reward=1.6,
        sl_source="ORDER_BLOCK",
        tp_source="M15_STRUCTURE",
        breakeven_trigger_r=1.0,
        session_close_exit=True,
    )

    ok, code = validate_exit_plan(
        entry_price=1.1000,
        direction=Direction.BULLISH,
        plan=plan,
        context=make_context_fn(regime=Regime.TRENDING),
        min_rr=1.3,
        min_rr_neutral=1.5,
    )

    assert not ok
    assert code == "invalid_tp_not_beyond_entry"


def test_validator_rejects_rr_below_floor_non_neutral(make_context_fn) -> None:
    plan = ExitPlan(
        stop_loss=1.0980,
        take_profit=1.1020,
        risk_reward=1.2,
        sl_source="ORDER_BLOCK",
        tp_source="M15_STRUCTURE",
        breakeven_trigger_r=1.0,
        session_close_exit=True,
    )

    ok, code = validate_exit_plan(
        entry_price=1.1000,
        direction=Direction.BULLISH,
        plan=plan,
        context=make_context_fn(regime=Regime.TRENDING),
        min_rr=1.3,
        min_rr_neutral=1.5,
    )

    assert not ok
    assert code == "rr_below_floor"


def test_validator_rejects_rr_below_floor_neutral(make_context_fn) -> None:
    plan = ExitPlan(
        stop_loss=1.0980,
        take_profit=1.1028,
        risk_reward=1.4,
        sl_source="ORDER_BLOCK",
        tp_source="M15_STRUCTURE",
        breakeven_trigger_r=1.0,
        session_close_exit=True,
    )

    ok, code = validate_exit_plan(
        entry_price=1.1000,
        direction=Direction.BULLISH,
        plan=plan,
        context=make_context_fn(regime=Regime.NEUTRAL),
        min_rr=1.3,
        min_rr_neutral=1.5,
    )

    assert not ok
    assert code == "neutral_rr_below_floor"


def test_validator_rejects_tp_inside_opposing_structure(make_structure_fn, make_context_fn) -> None:
    opposing = make_structure_fn(
        StructureType.ORDER_BLOCK,
        Direction.BEARISH,
        low=1.1025,
        high=1.1032,
        quality=0.9,
    )
    context = replace(make_context_fn(regime=Regime.TRENDING), nearby_structures=[opposing])

    plan = ExitPlan(
        stop_loss=1.0980,
        take_profit=1.1030,
        risk_reward=1.5,
        sl_source="ORDER_BLOCK",
        tp_source="M15_STRUCTURE",
        breakeven_trigger_r=1.0,
        session_close_exit=True,
    )

    ok, code = validate_exit_plan(
        entry_price=1.1000,
        direction=Direction.BULLISH,
        plan=plan,
        context=context,
        min_rr=1.3,
        min_rr_neutral=1.5,
    )

    assert not ok
    assert code == "tp_inside_opposing_structure"


def test_validator_accepts_valid_plan(make_context_fn) -> None:
    plan = ExitPlan(
        stop_loss=1.0980,
        take_profit=1.1032,
        risk_reward=1.6,
        sl_source="ORDER_BLOCK",
        tp_source="H1_STRUCTURE",
        breakeven_trigger_r=1.0,
        session_close_exit=True,
    )

    ok, code = validate_exit_plan(
        entry_price=1.1000,
        direction=Direction.BULLISH,
        plan=plan,
        context=make_context_fn(regime=Regime.TRENDING),
        min_rr=1.3,
        min_rr_neutral=1.5,
    )

    assert ok
    assert code == "ok"
