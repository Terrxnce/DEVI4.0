from __future__ import annotations

import json
from datetime import UTC, datetime

from src.core.enums import Direction, Namespace, StructureType, Timeframe
from src.decision.engine import evaluate_decision
from src.ops.telemetry import TelemetryWriter


def test_write_decision_outcome_uses_stage_mapping(
    tmp_path,
    make_structure_fn,
    make_context_fn,
    default_config,
) -> None:
    writer = TelemetryWriter(logs_root=str(tmp_path / "logs"), namespace=Namespace.EVAL)

    cfg = dict(default_config)
    cfg["pipeline"] = {"enable_full_phase1_flow": True}

    structures = [
        make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, quality=0.9, bar_index=10),
        make_structure_fn(
            StructureType.BREAK_OF_STRUCTURE,
            Direction.BULLISH,
            quality=0.85,
            timeframe=Timeframe.H1,
            bar_index=11,
        ),
        make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, quality=0.9, bar_index=12),
    ]

    outcome = evaluate_decision(
        structures=structures,
        context=make_context_fn(),
        config=cfg,
        entry_price=1.0990,
        atr_override=0.001,
    )

    writer.write_decision_outcome(
        run_id="run_telemetry",
        scan_id="scan_telemetry",
        config_hash="cfg_hash",
        snapshot_id="snap_telemetry",
        context=make_context_fn(),
        outcome=outcome,
        decision_id="decision_telemetry",
        timestamp=datetime(2026, 4, 30, 8, 0, tzinfo=UTC),
    )

    decisions_files = sorted((tmp_path / "logs" / "eval").glob("decisions_*.jsonl"))
    assert decisions_files
    decisions_path = decisions_files[-1]
    rows = decisions_path.read_text(encoding="utf-8").strip().splitlines()
    payload = json.loads(rows[-1])

    assert payload["decision_id"] == "decision_telemetry"
    assert payload["final_decision"] == "EXECUTE"
    assert payload["stage_entered"] == "EXECUTION"
    assert payload["stage_failed"] == ""
    assert payload["failure_code"] == "approved"
    assert payload["record_valid"] is True
    assert payload["record_invalid_reasons"] == []
