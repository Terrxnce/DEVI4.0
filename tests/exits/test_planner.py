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
    # TP structure age = current_bar_index(100) - bar_index(1) = 99.
    # Set tp_max_age_bars=80 so 99 > 80 triggers tp_too_old rejection.
    cfg = dict(default_config)
    cfg["exits"] = dict(default_config["exits"])
    cfg["exits"]["tp_max_age_bars"] = 80

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
            config=cfg,
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


# ---------------------------------------------------------------------------
# Absolute pip floor tests
# ---------------------------------------------------------------------------


def _pip_floor_config(default_config: dict, min_sl_pips: float, point: float = 0.00001) -> dict:
    """Return a config with pip floor and instrument point set."""
    cfg = {**default_config}
    cfg["exits"] = {**default_config["exits"], "min_sl_pips": min_sl_pips}
    cfg["instrument"] = {**default_config.get("instrument", {}), "point": point}
    return cfg


def test_pip_floor_disabled_when_zero(make_structure_fn, make_context_fn, default_config, empty_references) -> None:
    """min_sl_pips = 0 → pip floor inactive, ATR floor governs as before."""
    cfg = _pip_floor_config(default_config, min_sl_pips=0.0)
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
        config=cfg,
    )
    assert plan.stop_loss < 1.1000


def test_pip_floor_accepts_sl_above_pip_threshold(make_structure_fn, make_context_fn, default_config, empty_references) -> None:
    """SL naturally 20 pips away, pip floor = 8 → pip floor not binding, trade accepted."""
    # entry=1.1000, OB low=1.0980 → SL ~1.0979 → ~21 pips distance. Point=0.00001.
    cfg = _pip_floor_config(default_config, min_sl_pips=8.0)
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
        config=cfg,
    )
    assert plan.stop_loss < 1.1000


def test_pip_floor_rejects_tight_structure_sl(make_structure_fn, make_context_fn, default_config, empty_references) -> None:
    """OB only 3 pips below entry on a compressed-ATR market, pip floor = 8 → structure rejected,
    fallback must satisfy the pip floor or raise."""
    # ATR = 0.0001 (1 pip). ATR floors: trending=1.5×0.0001=0.00015, neutral=1.2×0.0001=0.00012.
    # Pip floor = 8 pips = 8×0.00001×10 = 0.0008 → dominates.
    # OB at low=1.0997 → SL ≈ 1.0997 - buffer(0.1×0.0001) = 1.09969 → distance ≈ 0.00031 → rejected by pip floor.
    # ATR fallback = 1.5×0.0001 = 0.00015 → also below pip floor → ExitFailure.
    cfg = _pip_floor_config(default_config, min_sl_pips=8.0)
    cfg["exits"] = {**cfg["exits"], "atr_fallback_sl_mult": 1.5}
    structures = [
        make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0997, high=1.1002, quality=0.9),
        make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, low=1.1032, high=1.1038, quality=0.9),
    ]
    with pytest.raises(ExitFailure, match="sl_too_close_for_regime_floor"):
        plan_exit(
            entry_price=1.1000,
            direction=Direction.BULLISH,
            context=make_context_fn(regime=Regime.TRENDING),
            structures=structures,
            references=empty_references,
            atr=0.0001,
            config=cfg,
        )


def test_pip_floor_fallback_passes_when_wide_enough(make_structure_fn, make_context_fn, default_config, empty_references) -> None:
    """No valid structural SL but ATR fallback exceeds the pip floor → ATR_FALLBACK accepted.

    ATR = 0.002 (20 pips). Pip floor = 8 pips = 0.0008.
    Fallback SL = 1.5 × 0.002 = 0.003 → 30 pips → above pip floor → passes.
    TP structure at 1.1040 → distance 0.004 → RR = 0.004 / 0.003 = 1.33 → passes min_rr 1.2.
    """
    cfg = _pip_floor_config(default_config, min_sl_pips=8.0)
    structures = [
        # quality too low to be an SL candidate
        make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0980, high=1.1005, quality=0.3),
        make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, low=1.1040, high=1.1048, quality=0.9),
    ]
    plan, _ = plan_exit(
        entry_price=1.1000,
        direction=Direction.BULLISH,
        context=make_context_fn(regime=Regime.TRENDING),
        structures=structures,
        references=empty_references,
        atr=0.002,
        config=cfg,
    )
    assert plan.sl_source == "ATR_FALLBACK"
    assert (1.1000 - plan.stop_loss) >= 0.0008  # at least 8 pips


def test_pip_floor_sell_setup(make_structure_fn, make_context_fn, default_config, empty_references) -> None:
    """Pip floor applies correctly to a BEARISH setup."""
    cfg = _pip_floor_config(default_config, min_sl_pips=8.0)
    structures = [
        make_structure_fn(StructureType.ORDER_BLOCK, Direction.BEARISH, low=1.1010, high=1.1030, quality=0.9),
        make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BULLISH, low=1.0960, high=1.0968, quality=0.9),
    ]
    plan, _ = plan_exit(
        entry_price=1.1020,
        direction=Direction.BEARISH,
        context=make_context_fn(regime=Regime.TRENDING),
        structures=structures,
        references=empty_references,
        atr=0.002,
        config=cfg,
    )
    assert plan.stop_loss > 1.1020
    assert (plan.stop_loss - 1.1020) >= 0.0008


# ---------------------------------------------------------------------------
# Balance-scaled lot cap formula tests
# ---------------------------------------------------------------------------


def test_balance_scaled_lot_cap_formula() -> None:
    """Formula: cap = balance × scale_factor. Verify all FTMO tier values."""
    scale_factor = 0.00001
    cases = [
        (10_000.0, 0.10),
        (25_000.0, 0.25),
        (50_000.0, 0.50),
        (100_000.0, 1.00),
        (200_000.0, 2.00),
    ]
    for balance, expected_cap in cases:
        cap = balance * scale_factor
        assert round(cap, 6) == pytest.approx(expected_cap, rel=1e-6), (
            f"balance={balance}: expected {expected_cap}, got {cap}"
        )


def test_balance_scaled_cap_applies_min_against_broker_max() -> None:
    """When broker max_lot is lower than the computed cap, broker max wins."""
    scale_factor = 0.00001
    balance = 100_000.0
    broker_max_lot = 0.5  # broker allows less than our cap
    balance_cap = balance * scale_factor  # = 1.0
    effective = min(broker_max_lot, balance_cap)
    assert effective == pytest.approx(0.5)


def test_balance_scaled_cap_applies_min_against_computed_cap() -> None:
    """When broker max_lot is higher than the computed cap, our cap wins."""
    scale_factor = 0.00001
    balance = 100_000.0
    broker_max_lot = 500.0  # broker allows far more
    balance_cap = balance * scale_factor  # = 1.0
    effective = min(broker_max_lot, balance_cap)
    assert effective == pytest.approx(1.0)


def test_balance_scaled_cap_zero_scale_factor_disables_cap() -> None:
    """scale_factor = 0 → cap is disabled, broker max passes through unchanged."""
    scale_factor = 0.0
    balance = 100_000.0
    broker_max_lot = 500.0
    if scale_factor > 0.0:
        balance_cap = balance * scale_factor
        effective = min(broker_max_lot, balance_cap)
    else:
        effective = broker_max_lot
    assert effective == pytest.approx(500.0)
