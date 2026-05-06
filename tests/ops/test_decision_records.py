from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from src.core.enums import Direction, FinalDecision, StructureType, Timeframe
from src.decision.engine import evaluate_decision
from src.ops.decision_records import build_decision_record


def _config() -> dict:
    return json.loads(Path("src/config/defaults.json").read_text(encoding="utf-8"))


def _structures(make_structure_fn):
    return [
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


def test_build_decision_record_for_exit_rejection(make_structure_fn, make_context_fn) -> None:
    cfg = _config()
    outcome = evaluate_decision(
        structures=[
            make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, quality=0.9, bar_index=10),
            make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BULLISH, quality=0.85, timeframe=Timeframe.H1, bar_index=11),
        ],
        context=make_context_fn(),
        config=cfg,
        entry_price=1.1000,
        atr_override=0.001,
    )

    record = build_decision_record(
        run_id="run_1",
        scan_id="scan_1",
        config_hash="cfg_hash",
        snapshot_id="snap_1",
        context=make_context_fn(),
        outcome=outcome,
        decision_id="dec_1",
        timestamp=datetime(2026, 4, 30, 8, 0, tzinfo=UTC),
    )

    assert record["final_decision"] == FinalDecision.REJECTED_EXIT_PLAN.value
    assert record["stage_entered"] == "EXIT_PLAN"
    assert record["stage_failed"] == "EXIT_PLAN"
    assert record["failure_code"] == "rr_fallback_disabled_no_structural_tp"


def test_build_decision_record_for_risk_rejection(make_structure_fn, make_context_fn) -> None:
    cfg = _config()
    cfg["pipeline"] = {"enable_full_phase1_flow": True}
    outcome = evaluate_decision(
        structures=_structures(make_structure_fn),
        context=make_context_fn(),
        config=cfg,
        entry_price=1.0990,
        atr_override=0.001,
        risk_state={"open_positions_total": 3},
    )

    record = build_decision_record(
        run_id="run_2",
        scan_id="scan_2",
        config_hash="cfg_hash",
        snapshot_id="snap_2",
        context=make_context_fn(),
        outcome=outcome,
    )

    assert record["final_decision"] == FinalDecision.REJECTED_RISK.value
    assert record["stage_entered"] == "RISK"
    assert record["stage_failed"] == "RISK"
    assert record["failure_code"] == "max_open_positions_total"


def test_build_decision_record_for_execute(make_structure_fn, make_context_fn) -> None:
    cfg = _config()
    cfg["pipeline"] = {"enable_full_phase1_flow": True}
    outcome = evaluate_decision(
        structures=_structures(make_structure_fn),
        context=make_context_fn(),
        config=cfg,
        entry_price=1.0990,
        atr_override=0.001,
    )

    record = build_decision_record(
        run_id="run_3",
        scan_id="scan_3",
        config_hash="cfg_hash",
        snapshot_id="snap_3",
        context=make_context_fn(),
        outcome=outcome,
    )

    assert record["final_decision"] == FinalDecision.EXECUTE.value
    assert record["stage_entered"] == "EXECUTION"
    assert record["stage_failed"] == ""
    assert record["failure_code"] == "approved"
