from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from src.core.enums import Direction, FinalDecision
from src.core.models import ContextSnapshot, DecisionRecord, to_primitive
from src.decision.engine import DecisionOutcome
from src.exits.tp_diagnostics import new_tp_debug
from src.ops.schema_validator import validate_decision_record



def _stage_mapping(final_decision: FinalDecision) -> tuple[str, str]:
    if final_decision == FinalDecision.REJECTED_EXIT_PLAN:
        return ("EXIT_PLAN", "EXIT_PLAN")
    if final_decision == FinalDecision.REJECTED_RISK:
        return ("RISK", "RISK")
    if final_decision == FinalDecision.REJECTED_COMPLIANCE:
        return ("COMPLIANCE", "COMPLIANCE")
    if final_decision == FinalDecision.REJECTED_EXECUTION:
        return ("EXECUTION", "EXECUTION")
    if final_decision == FinalDecision.EXECUTE:
        return ("EXECUTION", "")
    return ("CONFLUENCE", "CONFLUENCE")



def _execution_side(outcome: DecisionOutcome) -> str:
    if outcome.confluence is None:
        return "NONE"
    if outcome.confluence.direction == Direction.BULLISH:
        return "BUY"
    if outcome.confluence.direction == Direction.BEARISH:
        return "SELL"
    return "NONE"



def build_decision_record(
    *,
    run_id: str,
    scan_id: str,
    config_hash: str,
    snapshot_id: str,
    context: ContextSnapshot,
    outcome: DecisionOutcome,
    decision_id: str | None = None,
    timestamp: datetime | None = None,
    entry_price: float | None = None,
    instrument_point: float | None = None,
) -> dict[str, object]:
    ts = timestamp or datetime.now(tz=UTC)
    entered, failed = _stage_mapping(outcome.final_decision)

    # Compute SL distances if data is available
    sl_distance_price = 0.0
    sl_distance_points = 0.0
    sl_distance_pips = 0.0
    if entry_price is not None and outcome.exit_plan is not None and outcome.exit_plan.stop_loss is not None:
        sl_distance_price = abs(entry_price - outcome.exit_plan.stop_loss)
        if instrument_point is not None and instrument_point > 0:
            sl_distance_points = sl_distance_price / instrument_point
            sl_distance_pips = sl_distance_points / 10

    setup_class = ""
    confidence_tier = ""
    if outcome.confluence is not None:
        setup_class = outcome.confluence.setup_class.value if outcome.confluence.setup_class is not None else ""
        confidence_tier = outcome.confluence.confidence_tier.value if outcome.confluence.confidence_tier is not None else ""

    record = DecisionRecord(
        run_id=run_id,
        scan_id=scan_id,
        decision_id=decision_id or str(uuid4()),
        timestamp=ts,
        symbol=context.symbol,
        session=context.session,
        execution_side=_execution_side(outcome),
        stage_entered=entered,
        stage_failed=failed,
        failure_code=outcome.failure_code,
        failure_detail=outcome.failure_code,
        final_decision=outcome.final_decision.value,
        final_decision_reason=outcome.failure_code,
        config_hash=config_hash,
        snapshot_id=snapshot_id,
        tp_debug=outcome.tp_debug if isinstance(outcome.tp_debug, dict) else new_tp_debug(),
        record_valid=False,
        record_invalid_reasons=[],
        sl_distance_price=round(sl_distance_price, 5),
        sl_distance_points=round(sl_distance_points, 1),
        sl_distance_pips=round(sl_distance_pips, 2),
        setup_class=setup_class,
        confidence_tier=confidence_tier,
    )

    # Convert to dict, validate, and set correct validation fields
    record_dict = to_primitive(record)
    validation = validate_decision_record(record_dict)
    record_dict["record_valid"] = validation.valid
    record_dict["record_invalid_reasons"] = validation.reasons

    return record_dict
