from __future__ import annotations

from src.core.enums import ConfidenceTier, Direction, FinalDecision, SetupClass, StructureType, Timeframe
from src.core.models import ConfluenceResult
from src.decision.engine import evaluate_decision, select_best_confluence


def _fake_confluence(
    *,
    setup: SetupClass,
    tier: ConfidenceTier,
    passed: bool,
    effective_quality: float,
    structural_count: int,
    trigger_quality: float,
    make_structure_fn,
) -> ConfluenceResult:
    trigger = make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, quality=trigger_quality, timeframe=Timeframe.M15)
    confirmations = [make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BULLISH, quality=0.7, timeframe=Timeframe.H1)]
    return ConfluenceResult(
        setup_class=setup,
        direction=Direction.BULLISH,
        primary_trigger=trigger,
        structural_confirmations=confirmations,
        structural_labels=["x"],
        minor_confluences=["y"],
        hard_rejects=[] if passed else ["fail"],
        soft_penalties=[],
        structural_count=structural_count,
        minor_count=1,
        quality_penalty=0.0,
        effective_quality=effective_quality,
        confluence_pass=passed,
        confidence_tier=tier,
        tier_reason="test",
    )


def test_disabled_setup_produces_hold(make_structure_fn, make_context_fn, default_config) -> None:
    structures = [
        make_structure_fn(StructureType.REJECTION, Direction.BULLISH, quality=0.9),
        make_structure_fn(StructureType.FAIR_VALUE_GAP, Direction.BULLISH, quality=0.8, bar_index=11),
    ]

    out = evaluate_decision(structures=structures, context=make_context_fn(), config=default_config)

    assert out.final_decision == FinalDecision.HOLD
    assert out.failure_code in ("setup_disabled", "gate_blocked_setup_disabled")


def test_confluence_pass_but_exit_plan_rejected(make_structure_fn, make_context_fn, default_config) -> None:
    structures = [
        make_structure_fn(
            StructureType.ORDER_BLOCK,
            Direction.BULLISH,
            quality=0.9,
            bar_index=10,
        ),
        make_structure_fn(
            StructureType.BREAK_OF_STRUCTURE,
            Direction.BULLISH,
            quality=0.85,
            timeframe=Timeframe.H1,
            bar_index=11,
        ),
    ]

    out = evaluate_decision(
        structures=structures,
        context=make_context_fn(),
        config=default_config,
        entry_price=1.1000,
        atr_override=0.001,
    )

    assert out.final_decision == FinalDecision.REJECTED_EXIT_PLAN
    assert out.failure_code == "rr_fallback_disabled_no_structural_tp"
    assert out.exit_plan is None
    assert "schema_version" in out.tp_debug
    assert "found" in out.tp_debug
    assert "rejected" in out.tp_debug
    assert "selected" in out.tp_debug


def test_confluence_pass_with_valid_exit_plan_holds_when_pipeline_disabled(
    make_structure_fn,
    make_context_fn,
    default_config,
) -> None:
    """When enable_full_phase1_flow is false, pipeline stops after exits."""
    structures = [
        make_structure_fn(
            StructureType.ORDER_BLOCK,
            Direction.BULLISH,
            quality=0.9,
            bar_index=10,
        ),
        make_structure_fn(
            StructureType.BREAK_OF_STRUCTURE,
            Direction.BULLISH,
            quality=0.85,
            timeframe=Timeframe.H1,
            bar_index=11,
        ),
        make_structure_fn(
            StructureType.BREAK_OF_STRUCTURE,
            Direction.BEARISH,
            quality=0.9,
            bar_index=12,
        ),
    ]

    out = evaluate_decision(
        structures=structures,
        context=make_context_fn(),
        config=default_config,
        entry_price=1.0990,
        atr_override=0.001,
    )

    assert out.final_decision == FinalDecision.HOLD
    assert out.failure_code == "pipeline_full_flow_disabled"
    assert out.exit_plan is not None
    assert out.risk_verdict is None
    assert out.trade_intent is None


def test_confluence_pass_with_valid_exit_plan_executes_when_all_layers_pass(
    make_structure_fn,
    make_context_fn,
    default_config,
) -> None:
    """Full pipeline only runs when enable_full_phase1_flow is explicitly true."""
    cfg = dict(default_config)
    cfg["pipeline"] = {"enable_full_phase1_flow": True}

    structures = [
        make_structure_fn(
            StructureType.ORDER_BLOCK,
            Direction.BULLISH,
            quality=0.9,
            bar_index=10,
        ),
        make_structure_fn(
            StructureType.BREAK_OF_STRUCTURE,
            Direction.BULLISH,
            quality=0.85,
            timeframe=Timeframe.H1,
            bar_index=11,
        ),
        make_structure_fn(
            StructureType.BREAK_OF_STRUCTURE,
            Direction.BEARISH,
            quality=0.9,
            bar_index=12,
        ),
    ]

    out = evaluate_decision(
        structures=structures,
        context=make_context_fn(),
        config=cfg,
        entry_price=1.0990,
        atr_override=0.001,
    )

    assert out.final_decision == FinalDecision.EXECUTE
    assert out.failure_code == "approved"
    assert out.exit_plan is not None
    assert out.risk_verdict is not None
    assert out.risk_verdict.approved is True
    assert out.trade_intent is not None
    assert out.trade_intent.symbol == "EURUSD"
    assert out.trade_intent.setup_class == out.confluence.setup_class
    assert out.exit_plan.risk_reward >= 1.3
    assert out.exit_plan.tp_source in ("M15_STRUCTURE", "H1_STRUCTURE")
    assert out.tp_debug.get("selected")


def test_confluence_pass_exit_ok_but_risk_rejected(make_structure_fn, make_context_fn, default_config) -> None:
    cfg = dict(default_config)
    cfg["pipeline"] = {"enable_full_phase1_flow": True}

    structures = [
        make_structure_fn(
            StructureType.ORDER_BLOCK,
            Direction.BULLISH,
            quality=0.9,
            bar_index=10,
        ),
        make_structure_fn(
            StructureType.BREAK_OF_STRUCTURE,
            Direction.BULLISH,
            quality=0.85,
            timeframe=Timeframe.H1,
            bar_index=11,
        ),
        make_structure_fn(
            StructureType.BREAK_OF_STRUCTURE,
            Direction.BEARISH,
            quality=0.9,
            bar_index=12,
        ),
    ]

    out = evaluate_decision(
        structures=structures,
        context=make_context_fn(),
        config=cfg,
        entry_price=1.0990,
        atr_override=0.001,
        risk_state={"open_positions_total": 3},
    )

    assert out.final_decision == FinalDecision.REJECTED_RISK
    assert out.failure_code == "max_open_positions_total"
    assert out.exit_plan is not None
    assert out.risk_verdict is not None
    assert out.risk_verdict.approved is False


def test_confluence_pass_exit_and_risk_ok_but_compliance_rejected(
    make_structure_fn,
    make_context_fn,
    default_config,
) -> None:
    cfg = dict(default_config)
    cfg["pipeline"] = {"enable_full_phase1_flow": True}
    cfg["runtime"] = dict(default_config["runtime"])
    cfg["execution"] = dict(default_config["execution"])
    cfg["runtime"]["mode"] = "live"
    cfg["execution"]["live_confirmed"] = False

    structures = [
        make_structure_fn(
            StructureType.ORDER_BLOCK,
            Direction.BULLISH,
            quality=0.9,
            bar_index=10,
        ),
        make_structure_fn(
            StructureType.BREAK_OF_STRUCTURE,
            Direction.BULLISH,
            quality=0.85,
            timeframe=Timeframe.H1,
            bar_index=11,
        ),
        make_structure_fn(
            StructureType.BREAK_OF_STRUCTURE,
            Direction.BEARISH,
            quality=0.9,
            bar_index=12,
        ),
    ]

    out = evaluate_decision(
        structures=structures,
        context=make_context_fn(),
        config=cfg,
        entry_price=1.0990,
        atr_override=0.001,
    )

    assert out.final_decision == FinalDecision.REJECTED_COMPLIANCE
    assert out.failure_code == "live_not_confirmed_in_config"
    assert out.exit_plan is not None
    assert out.risk_verdict is not None
    assert out.risk_verdict.approved is True


def test_confluence_pass_exit_risk_compliance_ok_but_execution_stage_rejected(
    make_structure_fn,
    make_context_fn,
    default_config,
) -> None:
    from src.core.arming import ArmingService

    cfg = dict(default_config)
    cfg["pipeline"] = {"enable_full_phase1_flow": True}
    cfg["runtime"] = dict(default_config["runtime"])
    cfg["execution"] = dict(default_config["execution"])
    cfg["runtime"]["mode"] = "live"
    cfg["execution"]["live_confirmed"] = True

    arming = ArmingService()
    arming.arm(run_id="run_001", armed_by="op", reason="test", symbols=["EURUSD"], max_orders=1)

    structures = [
        make_structure_fn(
            StructureType.ORDER_BLOCK,
            Direction.BULLISH,
            quality=0.9,
            bar_index=10,
        ),
        make_structure_fn(
            StructureType.BREAK_OF_STRUCTURE,
            Direction.BULLISH,
            quality=0.85,
            timeframe=Timeframe.H1,
            bar_index=11,
        ),
        make_structure_fn(
            StructureType.BREAK_OF_STRUCTURE,
            Direction.BEARISH,
            quality=0.9,
            bar_index=12,
        ),
    ]

    out = evaluate_decision(
        structures=structures,
        context=make_context_fn(),
        config=cfg,
        entry_price=1.0990,
        atr_override=0.001,
        arming_service=arming,
    )

    assert out.final_decision == FinalDecision.REJECTED_EXECUTION
    assert out.failure_code == "live_execution_not_allowed_phase1"
    assert out.exit_plan is not None
    assert out.risk_verdict is not None
    assert out.risk_verdict.approved is True


def test_position_management_rejects_when_partials_enabled(
    make_structure_fn,
    make_context_fn,
    default_config,
) -> None:
    cfg = dict(default_config)
    cfg["pipeline"] = {"enable_full_phase1_flow": True}
    cfg["exits"] = dict(default_config["exits"])
    cfg["exits"]["management"] = dict(default_config["exits"]["management"])
    cfg["exits"]["management"]["partials_enabled"] = True

    structures = [
        make_structure_fn(
            StructureType.ORDER_BLOCK,
            Direction.BULLISH,
            quality=0.9,
            bar_index=10,
        ),
        make_structure_fn(
            StructureType.BREAK_OF_STRUCTURE,
            Direction.BULLISH,
            quality=0.85,
            timeframe=Timeframe.H1,
            bar_index=11,
        ),
        make_structure_fn(
            StructureType.BREAK_OF_STRUCTURE,
            Direction.BEARISH,
            quality=0.9,
            bar_index=12,
        ),
    ]

    out = evaluate_decision(
        structures=structures,
        context=make_context_fn(),
        config=cfg,
        entry_price=1.0990,
        atr_override=0.001,
    )

    assert out.final_decision == FinalDecision.REJECTED_EXECUTION
    assert out.failure_code == "partials_not_supported_phase1"
    assert out.exit_plan is not None
    assert out.risk_verdict is not None
    assert out.trade_intent is None


def test_deterministic_best_setup_selection(make_structure_fn) -> None:
    results = [
        _fake_confluence(
            setup=SetupClass.OB_WITH_ENGULFING,
            tier=ConfidenceTier.B,
            passed=True,
            effective_quality=0.80,
            structural_count=4,
            trigger_quality=0.70,
            make_structure_fn=make_structure_fn,
        ),
        _fake_confluence(
            setup=SetupClass.OB_WITH_BOS,
            tier=ConfidenceTier.A,
            passed=True,
            effective_quality=0.78,
            structural_count=3,
            trigger_quality=0.68,
            make_structure_fn=make_structure_fn,
        ),
        _fake_confluence(
            setup=SetupClass.OB_WITH_BOS,
            tier=ConfidenceTier.A,
            passed=False,
            effective_quality=0.99,
            structural_count=5,
            trigger_quality=0.99,
            make_structure_fn=make_structure_fn,
        ),
    ]

    best = select_best_confluence(results)

    assert best is not None
    assert best.setup_class == SetupClass.OB_WITH_BOS
    assert best.confidence_tier == ConfidenceTier.A
    assert best.confluence_pass is True
