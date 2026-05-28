from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.core.enums import ConfidenceTier, Direction, FinalDecision, SetupClass
from src.core.models import ConfluenceResult, ContextSnapshot, DetectedStructure, ExitPlan, RiskVerdict, TradeIntent
from src.context.references import PriceReferenceLevels
from src.decision.confluence import ConfluenceConfig, evaluate_confluence
from src.decision.entry_gate import evaluate_entry_proximity
from src.decision.session_proximity import evaluate_session_proximity
from src.decision.setup_rules import SetupCandidate, match_setup_candidates
from src.execution.gate import evaluate_execution
from src.exits.planner import ExitFailure, plan_exit
from src.exits.tp_diagnostics import new_tp_debug
from src.position.manager import build_trade_intent
from src.risk.evaluator import evaluate_risk
from src.supervisor.gate import evaluate_supervisor

if TYPE_CHECKING:
    from src.context.session_levels import SessionLevels
    from src.core.models import Bar


@dataclass(frozen=True)
class DecisionOutcome:
    final_decision: FinalDecision
    failure_code: str
    confluence: ConfluenceResult | None
    exit_plan: ExitPlan | None
    risk_verdict: RiskVerdict | None
    trade_intent: TradeIntent | None
    tp_debug: dict[str, Any]


def _confidence_rank(tier: ConfidenceTier) -> int:
    if tier == ConfidenceTier.A:
        return 0
    if tier == ConfidenceTier.B:
        return 1
    return 2


def _best_sort_key(item: ConfluenceResult) -> tuple[int, int, float, int, float, str]:
    return (
        0 if item.confluence_pass else 1,
        _confidence_rank(item.confidence_tier),
        -item.effective_quality,
        -item.structural_count,
        -item.primary_trigger.quality,
        item.setup_class.value,
    )


def select_best_confluence(results: list[ConfluenceResult]) -> ConfluenceResult | None:
    if not results:
        return None
    return sorted(results, key=_best_sort_key)[0]


def _is_setup_allowed(setup: SetupClass, config: dict[str, Any]) -> tuple[bool, str]:
    gates = config.get("gates", {})
    allowed_raw = gates.get("allowed_setups", [])
    mode = str(gates.get("mode", "enforce"))
    allowed = {str(value) for value in allowed_raw}
    if setup.value in allowed:
        return (True, "")
    if mode == "enforce":
        return (False, "gate_blocked_setup_disabled")
    return (False, "setup_disabled")


def _empty_references() -> PriceReferenceLevels:
    return PriceReferenceLevels(
        prior_day_high=None,
        prior_day_low=None,
        prior_session_high=None,
        prior_session_low=None,
        prominent_swing_high=None,
        prominent_swing_low=None,
    )


def _derive_entry_price(confluence: ConfluenceResult) -> float:
    trigger = confluence.primary_trigger
    return (trigger.price_high + trigger.price_low) / 2.0


def _build_narrative_candidates(
    session_levels: "SessionLevels",
    structures: list[DetectedStructure],
    current_price: float,
    atr: float,
    narrative_cfg: dict[str, Any],
) -> list[SetupCandidate]:
    """Generate SWEEP_REVERSAL candidates from the narrative layer.

    Returns an empty list when no complete narrative is found or when the
    narrative builder is not available (import guard for safety).
    """
    try:
        from src.narrative.narrative_builder import NarrativeBuilder, narrative_to_candidate
    except ImportError:
        return []

    builder = NarrativeBuilder(
        min_fvg_quality=float(narrative_cfg.get("min_fvg_quality", 0.35)),
        retrace_tolerance_atr=float(narrative_cfg.get("retrace_tolerance_atr", 0.15)),
    )
    narrative = builder.evaluate(
        session_levels=session_levels,
        structures=structures,
        current_price=current_price,
        atr=atr,
    )
    if narrative is None or not narrative.sequence_complete:
        return []
    return [narrative_to_candidate(narrative, structures)]


def _apply_h4_gate(
    candidates: list[SetupCandidate],
    current_price: float,
    bars_h4: list["Bar"],
    config: dict[str, Any],
) -> list[SetupCandidate]:
    """Filter out candidates where current price is inside a counter-H4 structure.

    Returns all candidates unchanged when the H4 gate module is unavailable.
    Non-counter and neutral H4 alignment candidates always pass through.
    """
    try:
        from src.context.h4_context import H4Alignment, H4ContextGate
    except ImportError:
        return candidates

    gate = H4ContextGate()
    filtered: list[SetupCandidate] = []
    for candidate in candidates:
        result = gate.evaluate(
            direction=candidate.direction,
            current_price=current_price,
            bars_h4=bars_h4,
            config=config,
        )
        if result.alignment != H4Alignment.COUNTER:
            filtered.append(candidate)
    return filtered


def evaluate_decision(
    structures: list[DetectedStructure],
    context: ContextSnapshot,
    config: dict[str, Any],
    *,
    entry_price: float | None = None,
    references: PriceReferenceLevels | None = None,
    atr_override: float | None = None,
    risk_state: dict[str, Any] | None = None,
    runtime_state: Any | None = None,
    arming_service: Any | None = None,
    kill_switch: Any | None = None,
    tp_structures: list[DetectedStructure] | None = None,
    session_levels: "SessionLevels | None" = None,
    bars_h4: "list[Bar] | None" = None,
) -> DecisionOutcome:
    confluence_cfg = config["confluence"]
    cfg = ConfluenceConfig(
        tier_a_min_confirmations=int(confluence_cfg["tier_a_min_confirmations"]),
        tier_b_min_confirmations=int(confluence_cfg["tier_b_min_confirmations"]),
        tier_c_min_confirmations=int(confluence_cfg["tier_c_min_confirmations"]),
        tier_c_tradable=bool(confluence_cfg["tier_c_tradable"]),
        triple_penalty_quality_floor=float(confluence_cfg["triple_penalty_quality_floor"]),
        block_ranging_regime=bool(confluence_cfg["block_ranging_regime"]),
        atr_percentile_hard_reject=float(confluence_cfg.get("atr_percentile_hard_reject", 1.0)),
    )

    candidates = [
        *match_setup_candidates(structures, Direction.BULLISH),
        *match_setup_candidates(structures, Direction.BEARISH),
    ]

    # Narrative layer — opt-in via config["narrative"]["narrative_mode"].
    # When enabled and session_levels is provided, generates SWEEP_REVERSAL candidates
    # from the sweep->FVG->retrace sequence and appends them to the candidate pool.
    _narrative_cfg = config.get("narrative", {})
    _resolved_atr = atr_override if atr_override is not None else context.atr_current
    if bool(_narrative_cfg.get("narrative_mode", False)) and session_levels is not None and entry_price is not None:
        _narrative_candidates = _build_narrative_candidates(
            session_levels=session_levels,
            structures=structures,
            current_price=entry_price,
            atr=_resolved_atr,
            narrative_cfg=_narrative_cfg,
        )
        candidates = [*candidates, *_narrative_candidates]

    # H4 context gate — opt-in via config["narrative"]["h4_context_gate"].
    # When enabled, removes any candidate where current price is inside a counter-H4
    # structure (e.g. BEARISH trade inside a bullish H4 FVG).
    if bool(_narrative_cfg.get("h4_context_gate", False)) and bars_h4 is not None and entry_price is not None:
        candidates = _apply_h4_gate(
            candidates=candidates,
            current_price=entry_price,
            bars_h4=bars_h4,
            config=config,
        )

    if not candidates:
        return DecisionOutcome(
            final_decision=FinalDecision.HOLD,
            failure_code="no_setup_match",
            confluence=None,
            exit_plan=None,
            risk_verdict=None,
            trade_intent=None,
            tp_debug=new_tp_debug(),
        )

    results: list[ConfluenceResult] = []
    for candidate in candidates:
        allowed, code = _is_setup_allowed(candidate.setup_class, config)
        if not allowed:
            continue
        results.append(evaluate_confluence(candidate=candidate, context=context, config=cfg))

    if not results:
        disabled = [_is_setup_allowed(candidate.setup_class, config)[1] for candidate in candidates]
        failure_code = "gate_blocked_setup_disabled" if "gate_blocked_setup_disabled" in disabled else "setup_disabled"
        return DecisionOutcome(
            final_decision=FinalDecision.HOLD,
            failure_code=failure_code,
            confluence=None,
            exit_plan=None,
            risk_verdict=None,
            trade_intent=None,
            tp_debug=new_tp_debug(),
        )

    best = select_best_confluence(results)
    if best is None:
        return DecisionOutcome(
            final_decision=FinalDecision.HOLD,
            failure_code="no_confluence_result",
            confluence=None,
            exit_plan=None,
            risk_verdict=None,
            trade_intent=None,
            tp_debug=new_tp_debug(),
        )

    if not best.confluence_pass:
        return DecisionOutcome(
            final_decision=FinalDecision.HOLD,
            failure_code=best.hard_rejects[0] if best.hard_rejects else "confluence_failed",
            confluence=best,
            exit_plan=None,
            risk_verdict=None,
            trade_intent=None,
            tp_debug=new_tp_debug(),
        )

    resolved_entry_price = entry_price if entry_price is not None else _derive_entry_price(best)
    resolved_references = references if references is not None else _empty_references()
    resolved_atr = atr_override if atr_override is not None else context.atr_current

    # Entry proximity gate: block setups where price has drifted away from the OB zone.
    # Configured under config["entry_gate"]. Disabled by default so existing tests pass
    # unless the key is explicitly present and enabled.
    _entry_gate_cfg = config.get("entry_gate", {})
    if bool(_entry_gate_cfg.get("proximity_gate_enabled", False)):
        _proximity_mult = float(_entry_gate_cfg.get("proximity_atr_mult", 0.5))
        _prox_ok, _prox_code = evaluate_entry_proximity(
            confluence=best,
            entry_price=resolved_entry_price,
            atr=resolved_atr,
            proximity_atr_mult=_proximity_mult,
        )
        if not _prox_ok:
            return DecisionOutcome(
                final_decision=FinalDecision.HOLD,
                failure_code=_prox_code,
                confluence=best,
                exit_plan=None,
                risk_verdict=None,
                trade_intent=None,
                tp_debug=new_tp_debug(),
            )

    # Session proximity gate: block entries when the active session closes soon.
    # Configured under config["entry_gate"]["session_proximity_gate_enabled"].
    # Uses context.bar_time (UTC) and context.symbol to check remaining session time.
    _sess_prox_ok, _sess_prox_code = evaluate_session_proximity(
        symbol=context.symbol,
        now_utc=context.bar_time,
        config=config,
    )
    if not _sess_prox_ok:
        return DecisionOutcome(
            final_decision=FinalDecision.HOLD,
            failure_code=_sess_prox_code,
            confluence=best,
            exit_plan=None,
            risk_verdict=None,
            trade_intent=None,
            tp_debug=new_tp_debug(),
        )

    try:
        exit_plan, tp_debug = plan_exit(
            entry_price=resolved_entry_price,
            direction=best.direction,
            context=context,
            structures=structures,
            references=resolved_references,
            atr=resolved_atr,
            config=config,
            tp_structures=tp_structures,
            session_levels=session_levels,
        )
    except ExitFailure as exc:
        return DecisionOutcome(
            final_decision=FinalDecision.REJECTED_EXIT_PLAN,
            failure_code=exc.failure_code,
            confluence=best,
            exit_plan=None,
            risk_verdict=None,
            trade_intent=None,
            tp_debug=exc.tp_debug,
        )

    pipeline_cfg = config.get("pipeline", {})
    if not pipeline_cfg.get("enable_full_phase1_flow", False):
        return DecisionOutcome(
            final_decision=FinalDecision.HOLD,
            failure_code="pipeline_full_flow_disabled",
            confluence=best,
            exit_plan=exit_plan,
            risk_verdict=None,
            trade_intent=None,
            tp_debug=tp_debug,
        )

    risk_verdict = evaluate_risk(
        context=context,
        config=config,
        entry_price=resolved_entry_price,
        stop_loss=exit_plan.stop_loss,
        state=risk_state,
    )
    if not risk_verdict.approved:
        return DecisionOutcome(
            final_decision=FinalDecision.REJECTED_RISK,
            failure_code=risk_verdict.reason,
            confluence=best,
            exit_plan=exit_plan,
            risk_verdict=risk_verdict,
            trade_intent=None,
            tp_debug=tp_debug,
        )

    supervisor_verdict = evaluate_supervisor(
        context=context,
        config=config,
        runtime_state=runtime_state,
        current_orders_this_run=config.get("current_orders_this_run", 0),
        arming_service=arming_service,
        kill_switch=kill_switch,
    )
    if not supervisor_verdict.approved:
        return DecisionOutcome(
            final_decision=FinalDecision.REJECTED_COMPLIANCE,
            failure_code=supervisor_verdict.reason,
            confluence=best,
            exit_plan=exit_plan,
            risk_verdict=risk_verdict,
            trade_intent=None,
            tp_debug=tp_debug,
        )

    execution_verdict = evaluate_execution(config=config)
    if not execution_verdict.approved:
        return DecisionOutcome(
            final_decision=FinalDecision.REJECTED_EXECUTION,
            failure_code=execution_verdict.reason,
            confluence=best,
            exit_plan=exit_plan,
            risk_verdict=risk_verdict,
            trade_intent=None,
            tp_debug=tp_debug,
        )

    pm_verdict = build_trade_intent(
        context=context,
        confluence=best,
        exit_plan=exit_plan,
        risk_verdict=risk_verdict,
        entry_price=resolved_entry_price,
        config=config,
    )
    if not pm_verdict.approved:
        return DecisionOutcome(
            final_decision=FinalDecision.REJECTED_EXECUTION,
            failure_code=pm_verdict.reason,
            confluence=best,
            exit_plan=exit_plan,
            risk_verdict=risk_verdict,
            trade_intent=None,
            tp_debug=tp_debug,
        )

    return DecisionOutcome(
        final_decision=FinalDecision.EXECUTE,
        failure_code="approved",
        confluence=best,
        exit_plan=exit_plan,
        risk_verdict=risk_verdict,
        trade_intent=pm_verdict.trade_intent,
        tp_debug=tp_debug,
    )
