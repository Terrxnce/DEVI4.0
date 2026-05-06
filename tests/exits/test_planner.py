from __future__ import annotations

import pytest

from src.context.references import PriceReferenceLevels
from src.core.enums import Direction, Regime, StructureType, Timeframe
from src.exits.planner import ExitFailure, plan_exit


def test_valid_sl_from_ob(make_structure_fn, make_context_fn, default_config, empty_references) -> None:
    structures = [
        make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0980, high=1.1010, quality=0.9),
        make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, low=1.1032, high=1.1038, quality=0.9),
    ]

    plan, _ = plan_exit(
        entry_price=1.1000,
        direction=Direction.BULLISH,
        context=make_context_fn(regime=Regime.TRENDING),
        structures=structures,
        references=empty_references,
        atr=0.001,
        config=default_config,
    )

    assert plan.sl_source == StructureType.ORDER_BLOCK.value
    assert plan.stop_loss < 1.1000


def test_sl_rejects_if_too_close_trending_floor(make_structure_fn, make_context_fn, default_config, empty_references) -> None:
    cfg = dict(default_config)
    cfg["exits"] = dict(default_config["exits"])
    cfg["exits"]["atr_fallback_sl_mult"] = 1.4

    structures = [
        make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0990, high=1.1005, quality=0.9),
    ]

    with pytest.raises(ExitFailure, match="sl_too_close_for_regime_floor"):
        plan_exit(
            entry_price=1.1000,
            direction=Direction.BULLISH,
            context=make_context_fn(regime=Regime.TRENDING),
            structures=structures,
            references=empty_references,
            atr=0.001,
            config=cfg,
        )


def test_sl_rejects_if_too_close_neutral_floor(make_structure_fn, make_context_fn, default_config, empty_references) -> None:
    cfg = dict(default_config)
    cfg["exits"] = dict(default_config["exits"])
    cfg["exits"]["atr_fallback_sl_mult"] = 1.9

    structures = [
        make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0992, high=1.1003, quality=0.9),
    ]

    with pytest.raises(ExitFailure, match="sl_too_close_for_regime_floor"):
        plan_exit(
            entry_price=1.1000,
            direction=Direction.BULLISH,
            context=make_context_fn(regime=Regime.NEUTRAL),
            structures=structures,
            references=empty_references,
            atr=0.001,
            config=cfg,
        )


def test_atr_fallback_sl_works_only_if_satisfies_floor(make_structure_fn, make_context_fn, default_config, empty_references) -> None:
    structures = [
        make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0992, high=1.1003, quality=0.3),
        make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, low=1.1032, high=1.1038, quality=0.9),
    ]

    plan, _ = plan_exit(
        entry_price=1.1000,
        direction=Direction.BULLISH,
        context=make_context_fn(regime=Regime.TRENDING),
        structures=structures,
        references=empty_references,
        atr=0.001,
        config=default_config,
    )

    assert plan.sl_source == "ATR_FALLBACK"


def test_valid_m15_structural_tp_selected(make_structure_fn, make_context_fn, default_config, empty_references) -> None:
    structures = [
        make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0980, high=1.1008, quality=0.9),
        make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, low=1.1032, high=1.1036, quality=0.9, timeframe=Timeframe.M15),
    ]

    plan, _ = plan_exit(
        entry_price=1.1000,
        direction=Direction.BULLISH,
        context=make_context_fn(),
        structures=structures,
        references=empty_references,
        atr=0.001,
        config=default_config,
    )

    assert plan.tp_source == "M15_STRUCTURE"


def test_valid_h1_structural_tp_selected(make_structure_fn, make_context_fn, default_config, empty_references) -> None:
    structures = [
        make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0980, high=1.1008, quality=0.9),
        make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, low=1.1031, high=1.1034, quality=0.75, timeframe=Timeframe.M15),
        make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, low=1.1032, high=1.1038, quality=0.95, timeframe=Timeframe.H1, bar_index=12),
    ]

    plan, _ = plan_exit(
        entry_price=1.1000,
        direction=Direction.BULLISH,
        context=make_context_fn(),
        structures=structures,
        references=empty_references,
        atr=0.001,
        config=default_config,
    )

    assert plan.tp_source == "H1_STRUCTURE"


def test_swing_tp_selected(make_structure_fn, make_context_fn, default_config) -> None:
    structures = [
        make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0980, high=1.1008, quality=0.9),
    ]
    refs = PriceReferenceLevels(None, None, None, None, 1.1034, None)

    plan, _ = plan_exit(
        entry_price=1.1000,
        direction=Direction.BULLISH,
        context=make_context_fn(),
        structures=structures,
        references=refs,
        atr=0.001,
        config=default_config,
    )

    assert plan.tp_source == "SWING"


def test_prior_day_or_session_reference_tp_selected(make_structure_fn, make_context_fn, default_config) -> None:
    structures = [make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0980, high=1.1008, quality=0.9)]
    refs = PriceReferenceLevels(1.1034, None, 1.1032, None, None, None)

    plan, _ = plan_exit(
        entry_price=1.1000,
        direction=Direction.BULLISH,
        context=make_context_fn(),
        structures=structures,
        references=refs,
        atr=0.001,
        config=default_config,
    )

    assert plan.tp_source in ("PRIOR_DAY", "PRIOR_SESSION")


def test_rr_fallback_disabled_blocks_synthetic_tp(make_structure_fn, make_context_fn, default_config, empty_references) -> None:
    structures = [make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0980, high=1.1008, quality=0.9)]

    with pytest.raises(ExitFailure, match="rr_fallback_disabled_no_structural_tp"):
        plan_exit(
            entry_price=1.1000,
            direction=Direction.BULLISH,
            context=make_context_fn(),
            structures=structures,
            references=empty_references,
            atr=0.001,
            config=default_config,
        )


def test_no_structural_tp_produces_expected_code(make_structure_fn, make_context_fn, default_config, empty_references) -> None:
    structures = [make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0980, high=1.1008, quality=0.9)]

    with pytest.raises(ExitFailure) as exc:
        plan_exit(
            entry_price=1.1000,
            direction=Direction.BULLISH,
            context=make_context_fn(),
            structures=structures,
            references=empty_references,
            atr=0.001,
            config=default_config,
        )

    assert exc.value.failure_code == "rr_fallback_disabled_no_structural_tp"


def test_neutral_rejects_rr_below_1_5(make_structure_fn, make_context_fn, default_config, empty_references) -> None:
    structures = [
        make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0978, high=1.1008, quality=0.9),
        make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, low=1.1030, high=1.1031, quality=0.9),
    ]

    with pytest.raises(ExitFailure, match="neutral_rr_below_floor"):
        plan_exit(
            entry_price=1.1000,
            direction=Direction.BULLISH,
            context=make_context_fn(regime=Regime.NEUTRAL),
            structures=structures,
            references=empty_references,
            atr=0.001,
            config=default_config,
        )


def test_neutral_accepts_structural_tp_rr_ge_1_5(make_structure_fn, make_context_fn, default_config, empty_references) -> None:
    structures = [
        make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0978, high=1.1008, quality=0.9),
        make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, low=1.1036, high=1.1039, quality=0.9),
    ]

    plan, _ = plan_exit(
        entry_price=1.1000,
        direction=Direction.BULLISH,
        context=make_context_fn(regime=Regime.NEUTRAL),
        structures=structures,
        references=empty_references,
        atr=0.001,
        config=default_config,
    )

    assert plan.risk_reward >= 1.5


def test_tp_behind_entry_rejected(make_structure_fn, make_context_fn, default_config, empty_references) -> None:
    structures = [
        make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0980, high=1.1008, quality=0.9),
        make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, low=1.0990, high=1.0995, quality=0.9),
    ]

    with pytest.raises(ExitFailure, match="rr_fallback_disabled_no_structural_tp"):
        plan_exit(
            entry_price=1.1000,
            direction=Direction.BULLISH,
            context=make_context_fn(),
            structures=structures,
            references=empty_references,
            atr=0.001,
            config=default_config,
        )


def test_tp_too_far_rejected(make_structure_fn, make_context_fn, default_config, empty_references) -> None:
    structures = [
        make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0980, high=1.1008, quality=0.9),
        make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, low=1.1200, high=1.1210, quality=0.9, timeframe=Timeframe.H1),
    ]

    with pytest.raises(ExitFailure) as exc:
        plan_exit(
            entry_price=1.1000,
            direction=Direction.BULLISH,
            context=make_context_fn(),
            structures=structures,
            references=empty_references,
            atr=0.001,
            config=default_config,
        )

    assert exc.value.failure_code == "rr_fallback_disabled_no_structural_tp"
    assert any(item.get("rejection_reason") == "tp_too_far" for item in exc.value.tp_debug["rejected"])


def test_tp_too_old_rejected(make_structure_fn, make_context_fn, default_config, empty_references) -> None:
    structures = [
        make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0980, high=1.1008, quality=0.9, bar_index=100),
        make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, low=1.1030, high=1.1034, quality=0.9, bar_index=1),
    ]

    with pytest.raises(ExitFailure) as exc:
        plan_exit(
            entry_price=1.1000,
            direction=Direction.BULLISH,
            context=make_context_fn(),
            structures=structures,
            references=empty_references,
            atr=0.001,
            config=default_config,
        )

    assert any(item.get("rejection_reason") == "tp_too_old" for item in exc.value.tp_debug["rejected"])


def test_deterministic_tp_candidate_ordering(make_structure_fn, make_context_fn, default_config, empty_references) -> None:
    structures = [
        make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0980, high=1.1008, quality=0.9, bar_index=100),
        make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, low=1.1032, high=1.1036, quality=0.8, bar_index=90),
        make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, low=1.1038, high=1.1042, quality=0.8, bar_index=90),
    ]

    first, debug_first = plan_exit(
        entry_price=1.1000,
        direction=Direction.BULLISH,
        context=make_context_fn(),
        structures=structures,
        references=empty_references,
        atr=0.001,
        config=default_config,
    )
    second, debug_second = plan_exit(
        entry_price=1.1000,
        direction=Direction.BULLISH,
        context=make_context_fn(),
        structures=structures,
        references=empty_references,
        atr=0.001,
        config=default_config,
    )

    assert first == second
    assert debug_first["selected"] == debug_second["selected"]


def test_tp_debug_populated_on_all_exit_failures(make_structure_fn, make_context_fn, default_config, empty_references) -> None:
    structures = [make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0990, high=1.1005, quality=0.9)]

    with pytest.raises(ExitFailure) as exc:
        plan_exit(
            entry_price=1.1000,
            direction=Direction.BULLISH,
            context=make_context_fn(regime=Regime.NEUTRAL),
            structures=structures,
            references=empty_references,
            atr=0.001,
            config=default_config,
        )

    tp_debug = exc.value.tp_debug
    assert isinstance(tp_debug, dict)
    assert "schema_version" in tp_debug
    assert "found" in tp_debug
    assert "rejected" in tp_debug
    assert "selected" in tp_debug
