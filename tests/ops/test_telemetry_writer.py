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


def test_write_trade_close_appends_to_trades_jsonl(tmp_path) -> None:
    """write_trade_close must append a status=closed record to the same trades JSONL
    as write_trade, so consumers can resolve final trade state by reading the last
    record per trade_id.
    """
    writer = TelemetryWriter(logs_root=str(tmp_path / "logs"), namespace=Namespace.PROD)

    ts = datetime(2026, 5, 25, 16, 0, tzinfo=UTC)

    open_record = {
        "event": "trade_open",
        "trade_id": "trade-abc-001",
        "ticket": 100001,
        "symbol": "EURUSD",
        "side": "BULLISH",
        "lot_size": 0.05,
        "open_price": 1.10000,
        "sl": 1.09800,
        "tp": 1.10300,
        "status": "open",
        "run_id": "run-001",
        "timestamp": ts.isoformat(),
    }
    writer.write_trade(open_record)

    close_record = {
        "event": "trade_close",
        "trade_id": "trade-abc-001",
        "decision_id": "dec-001",
        "ticket": 100001,
        "symbol": "EURUSD",
        "side": "BULLISH",
        "lot_size": 0.05,
        "open_price": 1.10000,
        "close_price": 1.10280,
        "close_time": "2026-05-25T15:59:00+00:00",
        "close_reason": "session_close_exit",
        "close_pnl": 140.0,
        "status": "closed",
        "run_id": "run-001",
        "timestamp": ts.isoformat(),
    }
    writer.write_trade_close(close_record)

    trades_files = sorted((tmp_path / "logs" / "prod").glob("trades_*.jsonl"))
    assert trades_files, "No trades JSONL written"
    rows = trades_files[-1].read_text(encoding="utf-8").strip().splitlines()

    # Both records must be in the same file
    assert len(rows) == 2

    open_row = json.loads(rows[0])
    close_row = json.loads(rows[1])

    assert open_row["status"] == "open"
    assert open_row["event"] == "trade_open"

    assert close_row["status"] == "closed"
    assert close_row["event"] == "trade_close"
    assert close_row["trade_id"] == "trade-abc-001"
    assert close_row["ticket"] == 100001
    assert close_row["close_reason"] == "session_close_exit"
    assert close_row["close_pnl"] == 140.0
