"""End-to-end paper flow test demonstrating full pipeline with telemetry.

This test proves that the full decision pipeline works in paper mode:
- confluence -> exits -> risk sizing -> supervisor -> execution -> TradeIntent -> paper fill -> telemetry
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.core.enums import Direction, FinalDecision, Namespace, StructureType, Timeframe
from src.core.models import ContextSnapshot
from src.decision.engine import evaluate_decision
from src.execution.paper_adapter import PaperExecutionAdapter
from src.ops.telemetry import TelemetryWriter


def _paper_config() -> dict:
    return json.loads(Path("src/config/paper.json").read_text(encoding="utf-8"))


def _context() -> ContextSnapshot:
    from src.core.enums import HTFAgreement, Regime, Session
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


def _make_structure(
    structure_type: StructureType,
    direction: Direction,
    *,
    quality: float = 0.9,
    low: float = 1.0980,
    high: float = 1.1010,
    timeframe: Timeframe = Timeframe.M15,
    bar_index: int = 10,
) -> None:
    from src.core.models import DetectedStructure
    return DetectedStructure(
        structure_type=structure_type,
        direction=direction,
        price_high=high,
        price_low=low,
        quality=quality,
        age_bars=5,
        atr_relative_size=2.0,
        timeframe=timeframe,
        bar_index=bar_index,
        bar_time=datetime(2026, 4, 30, 8, 0, tzinfo=UTC),
    )


def test_paper_full_flow_produces_execute_with_trade_intent() -> None:
    """Full paper pipeline: confluence -> exits -> risk -> supervisor -> execution -> TradeIntent."""
    cfg = _paper_config()

    structures = [
        _make_structure(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0980, high=1.1008, bar_index=10),
        _make_structure(StructureType.BREAK_OF_STRUCTURE, Direction.BULLISH, low=1.1032, high=1.1038, timeframe=Timeframe.H1, bar_index=11),
        _make_structure(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, low=1.1040, high=1.1045, bar_index=12),
    ]

    outcome = evaluate_decision(
        structures=structures,
        context=_context(),
        config=cfg,
        entry_price=1.1000,
        atr_override=0.001,
    )

    assert outcome.final_decision == FinalDecision.EXECUTE, f"Expected EXECUTE, got {outcome.final_decision} with {outcome.failure_code}"
    assert outcome.failure_code == "approved"
    assert outcome.confluence is not None
    assert outcome.exit_plan is not None
    assert outcome.exit_plan.stop_loss is not None
    assert outcome.exit_plan.take_profit is not None
    assert outcome.risk_verdict is not None
    assert outcome.risk_verdict.approved is True
    assert outcome.trade_intent is not None
    assert outcome.trade_intent.symbol == "EURUSD"
    assert outcome.trade_intent.direction == Direction.BULLISH


def test_paper_risk_sizing_evidence() -> None:
    """Risk sizing with real lot calculation from balance, entry, SL, and instrument config."""
    cfg = _paper_config()

    structures = [
        _make_structure(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0980, high=1.1008, bar_index=10),
        _make_structure(StructureType.BREAK_OF_STRUCTURE, Direction.BULLISH, low=1.1032, high=1.1038, timeframe=Timeframe.H1, bar_index=11),
        _make_structure(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, low=1.1040, high=1.1045, bar_index=12),
    ]

    outcome = evaluate_decision(
        structures=structures,
        context=_context(),
        config=cfg,
        entry_price=1.1000,
        atr_override=0.001,
    )

    assert outcome.final_decision == FinalDecision.EXECUTE
    risk = outcome.risk_verdict
    assert risk is not None
    assert risk.approved is True

    # Evidence: balance=10000, risk_per_trade_pct=0.4, entry=1.1000, SL from exit plan
    entry = 1.1000
    sl = outcome.exit_plan.stop_loss
    sl_distance = abs(entry - sl)
    balance = 10000.0
    risk_pct = 0.4
    risk_amount = balance * (risk_pct / 100.0)  # $40

    # Print evidence for review
    print(f"\n  [RISK SIZING EVIDENCE]")
    print(f"    balance/equity: ${balance:,.2f}")
    print(f"    risk_per_trade_pct: {risk_pct}%")
    print(f"    entry_price: {entry}")
    print(f"    stop_loss: {sl}")
    points = sl_distance / 0.00001
    pips = points / 10
    print(f"    SL_distance_price: {sl_distance:.5f}")
    print(f"    SL_distance_points: {points:.0f}")
    print(f"    SL_distance_pips: {pips:.1f}")
    print(f"    risk_amount: ${risk_amount:.2f}")
    print(f"    calculated_lot_size: {risk.lot_size}")
    print(f"    intended_risk_pct: {risk.intended_risk_pct}%")
    print(f"    actual_risk_pct: {risk.actual_risk_pct}%")

    assert risk.lot_size > 0
    assert risk.lot_size >= 0.01  # min_lot
    assert risk.lot_size <= 100.0  # max_lot


def test_paper_execution_simulation_no_broker() -> None:
    """Paper fill simulation without any broker interaction."""
    cfg = _paper_config()

    structures = [
        _make_structure(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0980, high=1.1008, bar_index=10),
        _make_structure(StructureType.BREAK_OF_STRUCTURE, Direction.BULLISH, low=1.1032, high=1.1038, timeframe=Timeframe.H1, bar_index=11),
        _make_structure(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, low=1.1040, high=1.1045, bar_index=12),
    ]

    outcome = evaluate_decision(
        structures=structures,
        context=_context(),
        config=cfg,
        entry_price=1.1000,
        atr_override=0.001,
    )

    assert outcome.final_decision == FinalDecision.EXECUTE
    intent = outcome.trade_intent
    assert intent is not None

    # Paper adapter: no broker methods, purely synthetic
    adapter = PaperExecutionAdapter()
    assert not hasattr(adapter, "_broker")
    assert not hasattr(adapter, "_mt5")
    assert not hasattr(adapter, "_connection")

    fill = adapter.execute(intent, spread_at_decision=0.0002)

    print(f"\n  [PAPER EXECUTION EVIDENCE]")
    print(f"    decision_id/trade_id: {fill.decision_id}")
    print(f"    ticket: {fill.ticket} (synthetic)")
    print(f"    side: {fill.side}")
    print(f"    intended_entry: {fill.intended_entry}")
    print(f"    actual_fill: {fill.actual_fill}")
    print(f"    slippage: {fill.slippage}")
    print(f"    paper_retcode: {fill.paper_retcode} (synthetic placeholder)")
    print(f"    order_status: {fill.order_status}")

    assert fill.decision_id == intent.trade_id
    assert fill.ticket >= 100000  # synthetic
    assert fill.side == "BUY"
    assert fill.order_status == "FILLED"
    assert fill.paper_retcode == 10009  # synthetic placeholder


def test_paper_full_flow_telemetry(tmp_path) -> None:
    """End-to-end paper flow with telemetry write and validation."""
    cfg = _paper_config()

    structures = [
        _make_structure(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0980, high=1.1008, bar_index=10),
        _make_structure(StructureType.BREAK_OF_STRUCTURE, Direction.BULLISH, low=1.1032, high=1.1038, timeframe=Timeframe.H1, bar_index=11),
        _make_structure(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, low=1.1040, high=1.1045, bar_index=12),
    ]

    outcome = evaluate_decision(
        structures=structures,
        context=_context(),
        config=cfg,
        entry_price=1.1000,
        atr_override=0.001,
    )

    assert outcome.final_decision == FinalDecision.EXECUTE

    writer = TelemetryWriter(logs_root=str(tmp_path / "logs"), namespace=Namespace.EVAL)
    writer.write_decision_outcome(
        run_id="paper_run_001",
        scan_id="paper_scan_001",
        config_hash="cfg_paper_001",
        snapshot_id="snap_001",
        context=_context(),
        outcome=outcome,
        decision_id="dec_paper_001",
        timestamp=datetime(2026, 4, 30, 8, 0, tzinfo=UTC),
        entry_price=1.1000,
        instrument_point=0.00001,
    )

    # Read and validate telemetry
    decisions_files = sorted((tmp_path / "logs" / "eval").glob("decisions_*.jsonl"))
    assert decisions_files
    decisions_path = decisions_files[-1]
    rows = decisions_path.read_text(encoding="utf-8").strip().splitlines()
    payload = json.loads(rows[-1])

    print(f"\n  [TELEMETRY EVIDENCE]")
    print(f"    decision_id: {payload['decision_id']}")
    print(f"    final_decision: {payload['final_decision']}")
    print(f"    stage_entered: {payload['stage_entered']}")
    print(f"    stage_failed: {payload['stage_failed']}")
    print(f"    failure_code: {payload['failure_code']}")
    print(f"    record_valid: {payload['record_valid']}")
    print(f"    record_invalid_reasons: {payload['record_invalid_reasons']}")
    print(f"    snapshot_id: {payload['snapshot_id']}")
    print(f"    tp_debug schema_version: {payload['tp_debug'].get('schema_version')}")
    print(f"    sl_distance_price: {payload.get('sl_distance_price')}")
    print(f"    sl_distance_points: {payload.get('sl_distance_points')}")
    print(f"    sl_distance_pips: {payload.get('sl_distance_pips')}")

    assert payload["decision_id"] == "dec_paper_001"
    assert payload["final_decision"] == "EXECUTE"
    assert payload["stage_entered"] == "EXECUTION"
    assert payload["stage_failed"] == ""
    assert payload["failure_code"] == "approved"
    assert payload["record_valid"] is True
    assert payload["record_invalid_reasons"] == []
    assert payload["snapshot_id"] == "snap_001"
    assert "tp_debug" in payload
    assert payload["tp_debug"].get("schema_version") is not None
    assert payload.get("sl_distance_price") is not None
    assert payload.get("sl_distance_points") is not None
    assert payload.get("sl_distance_pips") is not None
    assert payload["sl_distance_price"] > 0
    assert payload["sl_distance_points"] > 0
    assert payload["sl_distance_pips"] > 0


def test_live_mode_rejected_by_execution_gate() -> None:
    """Live mode must be rejected even with full pipeline enabled and armed."""
    from src.core.arming import ArmingService

    cfg = _paper_config()
    cfg["runtime"] = dict(cfg["runtime"])
    cfg["execution"] = dict(cfg["execution"])
    cfg["runtime"]["mode"] = "live"
    cfg["execution"]["live_confirmed"] = True

    arming = ArmingService()
    arming.arm(run_id="run_001", armed_by="op", reason="test", symbols=["EURUSD"], max_orders=1)

    structures = [
        _make_structure(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0980, high=1.1008, bar_index=10),
        _make_structure(StructureType.BREAK_OF_STRUCTURE, Direction.BULLISH, low=1.1032, high=1.1038, timeframe=Timeframe.H1, bar_index=11),
        _make_structure(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, low=1.1040, high=1.1045, bar_index=12),
    ]

    outcome = evaluate_decision(
        structures=structures,
        context=_context(),
        config=cfg,
        entry_price=1.1000,
        atr_override=0.001,
        arming_service=arming,
    )

    print(f"\n  [LIVE SAFETY EVIDENCE]")
    print(f"    final_decision: {outcome.final_decision.value}")
    print(f"    failure_code: {outcome.failure_code}")

    assert outcome.final_decision == FinalDecision.REJECTED_EXECUTION
    assert outcome.failure_code == "live_execution_not_allowed_phase1"
    assert outcome.trade_intent is None


def test_supervisor_blocks_exceeded_max_orders() -> None:
    """Supervisor gate rejects when current_orders_this_run >= max_orders_per_run."""
    cfg = _paper_config()
    cfg["current_orders_this_run"] = 1  # already placed 1 this run
    cfg["execution"] = dict(cfg["execution"])
    cfg["execution"]["max_orders_per_run"] = 1  # max is 1

    structures = [
        _make_structure(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0980, high=1.1008, bar_index=10),
        _make_structure(StructureType.BREAK_OF_STRUCTURE, Direction.BULLISH, low=1.1032, high=1.1038, timeframe=Timeframe.H1, bar_index=11),
        _make_structure(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, low=1.1040, high=1.1045, bar_index=12),
    ]

    outcome = evaluate_decision(
        structures=structures,
        context=_context(),
        config=cfg,
        entry_price=1.1000,
        atr_override=0.001,
    )

    assert outcome.final_decision == FinalDecision.REJECTED_COMPLIANCE
    assert outcome.failure_code == "max_orders_per_run_exceeded"


def test_risk_rejection_blocks_trade_intent() -> None:
    """Risk rejection prevents TradeIntent creation."""
    cfg = _paper_config()

    structures = [
        _make_structure(StructureType.ORDER_BLOCK, Direction.BULLISH, low=1.0980, high=1.1008, bar_index=10),
        _make_structure(StructureType.BREAK_OF_STRUCTURE, Direction.BULLISH, low=1.1032, high=1.1038, timeframe=Timeframe.H1, bar_index=11),
        _make_structure(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, low=1.1040, high=1.1045, bar_index=12),
    ]

    outcome = evaluate_decision(
        structures=structures,
        context=_context(),
        config=cfg,
        entry_price=1.1000,
        atr_override=0.001,
        risk_state={"open_positions_total": 3},
    )

    assert outcome.final_decision == FinalDecision.REJECTED_RISK
    assert outcome.risk_verdict is not None
    assert outcome.risk_verdict.approved is False
    assert outcome.trade_intent is None
