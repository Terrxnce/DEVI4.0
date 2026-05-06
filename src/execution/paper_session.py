"""Paper session runner: full M15 scan cycle using MT5 data with simulated execution.

Safety rules:
- MT5 is data source only
- No order_send, order_modify, position_close, or any broker execution
- Paper fills use MT5-derived bid/ask but never place real orders
- Live mode remains blocked
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.context.builder import build_context_snapshot
from src.core.enums import FinalDecision, Namespace, Timeframe
from src.core.models import Bar, DetectedStructure, SnapshotRecord, to_primitive
from src.data.mt5_source import MT5DataSource
from src.decision.engine import evaluate_decision
from src.detectors.break_of_structure import BreakOfStructureDetector
from src.detectors.engulfing import EngulfingDetector
from src.detectors.fair_value_gap import FairValueGapDetector
from src.detectors.liquidity_sweep import LiquiditySweepDetector
from src.detectors.order_block import OrderBlockDetector
from src.detectors.rejection import RejectionDetector
from src.core.runtime_state import RuntimeState
from src.data.base import DataSourceError
from src.execution.paper_adapter import PaperExecutionAdapter
from src.execution.position_tracker import PaperPositionTracker
from src.ops.telemetry import TelemetryWriter
from src.context.regime import simple_atr


@dataclass(frozen=True)
class SymbolResult:
    symbol: str
    decision: FinalDecision
    failure_code: str
    bars_m15_count: int
    bars_h1_count: int
    tick_bid: float
    tick_ask: float
    paper_fill: dict[str, Any] | None
    snapshot_id: str
    skipped_reason: str | None = None


@dataclass(frozen=True)
class PaperSessionResult:
    run_id: str
    symbol_results: dict[str, SymbolResult]
    account_balance: float
    account_equity: float
    decision_count: int
    trade_count: int
    open_position_count: int


class PaperSession:
    """Run a complete paper/eval session using MT5 data with simulated execution.

    Supports multi-symbol runs with runtime state tracking and minimal position management.
    Does NOT place real orders. Uses PaperExecutionAdapter for simulated fills only.
    """

    def __init__(
        self,
        *,
        config: dict[str, Any],
        logs_root: str,
        namespace: Namespace,
        symbols: list[str] | None = None,
    ) -> None:
        self.config = config
        self.symbols = symbols or ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"]
        self.data = MT5DataSource()
        self.writer = TelemetryWriter(logs_root=logs_root, namespace=namespace)
        self.adapter = PaperExecutionAdapter()
        self.runtime_state = RuntimeState()
        self.position_tracker = PaperPositionTracker()

    def close(self) -> None:
        self.data.close()

    def _run_detectors(self, bars: list[Bar], atr: float) -> list[DetectedStructure]:
        """Run all detectors on a single timeframe and collect structures."""
        cfg = self.config["detection"]
        current_idx = bars[-1].bar_index if bars else 0
        structures: list[DetectedStructure] = []

        ob = OrderBlockDetector(
            min_body_atr_mult=cfg["order_block"]["min_body_atr_mult"],
            max_age_bars=cfg["order_block"]["max_age_bars"],
            min_quality=cfg["order_block"]["min_quality"],
        )
        structures.extend(ob.detect(bars, atr, current_idx))

        bos = BreakOfStructureDetector(
            min_swing_atr_mult=cfg["break_of_structure"]["min_swing_atr_mult"],
            lookback_bars=cfg["break_of_structure"]["lookback_bars"],
            min_quality=cfg["break_of_structure"]["min_quality"],
        )
        structures.extend(bos.detect(bars, atr))

        fvg = FairValueGapDetector(
            min_gap_atr_mult=cfg["fair_value_gap"]["min_gap_atr_mult"],
            max_age_bars=cfg["fair_value_gap"]["max_age_bars"],
            min_quality=cfg["fair_value_gap"]["min_quality"],
        )
        structures.extend(fvg.detect(bars, atr, current_idx))

        sweep = LiquiditySweepDetector(
            max_wick_atr_mult=cfg["sweep"]["max_wick_atr_mult"],
            min_wick_body_ratio=cfg["sweep"]["min_wick_body_ratio"],
            max_age_bars=cfg["sweep"]["max_age_bars"],
            min_quality=cfg["sweep"]["min_quality"],
        )
        structures.extend(sweep.detect(bars, atr, current_idx))

        rej = RejectionDetector(
            min_wick_atr_mult=cfg["rejection"]["min_wick_atr_mult"],
            min_wick_body_ratio=cfg["rejection"]["min_wick_body_ratio"],
            max_age_bars=cfg["rejection"]["max_age_bars"],
            min_quality=cfg["rejection"]["min_quality"],
        )
        structures.extend(rej.detect(bars, atr, current_idx))

        eng = EngulfingDetector(
            min_body_atr_mult=cfg["engulfing"]["min_body_atr_mult"],
            max_age_bars=cfg["engulfing"]["max_age_bars"],
            min_quality=cfg["engulfing"]["min_quality"],
        )
        structures.extend(eng.detect(bars, atr, current_idx))

        return structures

    def _validate_profile(self, profile, symbol: str) -> str | None:
        """Validate critical instrument fields. Return skip reason or None if valid."""
        def _get(key: str):
            if isinstance(profile, dict):
                return profile.get(key)
            return getattr(profile, key, None)

        if not _get("point"):
            return f"missing_instrument_data:{symbol}:point"
        if not _get("contract_size"):
            return f"missing_instrument_data:{symbol}:contract_size"
        if not _get("lot_step"):
            return f"missing_instrument_data:{symbol}:lot_step"
        return None

    def run(self, *, run_id: str) -> PaperSessionResult:
        """Execute one full paper session for all configured symbols."""
        self.runtime_state = RuntimeState(run_id=run_id)
        self.position_tracker = PaperPositionTracker()
        account = self.data.fetch_account_info()
        symbol_results: dict[str, SymbolResult] = {}

        for symbol in sorted(self.symbols):
            result = self._run_symbol(
                symbol=symbol,
                run_id=run_id,
                account_balance=account["balance"],
            )
            symbol_results[symbol] = result

        return PaperSessionResult(
            run_id=run_id,
            symbol_results=symbol_results,
            account_balance=account["balance"],
            account_equity=account["equity"],
            decision_count=self.runtime_state.decision_count,
            trade_count=self.runtime_state.trade_count,
            open_position_count=len(self.position_tracker.get_open_positions()),
        )

    def _run_symbol(
        self,
        *,
        symbol: str,
        run_id: str,
        account_balance: float,
    ) -> SymbolResult:
        """Run one decision cycle for a single symbol."""
        # 1. Fetch instrument profile and validate
        try:
            profile = self.data.fetch_instrument_profile(symbol)
        except DataSourceError as exc:
            return SymbolResult(
                symbol=symbol,
                decision=FinalDecision.HOLD,
                failure_code=f"profile_fetch_failed:{exc}",
                bars_m15_count=0,
                bars_h1_count=0,
                tick_bid=0.0,
                tick_ask=0.0,
                paper_fill=None,
                snapshot_id=f"{run_id}_{symbol}_snapshot",
                skipped_reason=str(exc),
            )

        skip_reason = self._validate_profile(profile, symbol)
        if skip_reason:
            return SymbolResult(
                symbol=symbol,
                decision=FinalDecision.HOLD,
                failure_code=skip_reason,
                bars_m15_count=0,
                bars_h1_count=0,
                tick_bid=0.0,
                tick_ask=0.0,
                paper_fill=None,
                snapshot_id=f"{run_id}_{symbol}_snapshot",
                skipped_reason=skip_reason,
            )

        # 2. Fetch bars and tick
        try:
            m15_bars = self.data.fetch_bars(symbol, Timeframe.M15, count=100)
            h1_bars = self.data.fetch_bars(symbol, Timeframe.H1, count=50)
            tick = self.data.fetch_tick(symbol)
        except DataSourceError as exc:
            return SymbolResult(
                symbol=symbol,
                decision=FinalDecision.HOLD,
                failure_code=f"data_fetch_failed:{exc}",
                bars_m15_count=0,
                bars_h1_count=0,
                tick_bid=0.0,
                tick_ask=0.0,
                paper_fill=None,
                snapshot_id=f"{run_id}_{symbol}_snapshot",
                skipped_reason=str(exc),
            )

        # 3. Detect structures
        atr_period = int(self.config["detection"]["atr_period"])
        atr_m15 = simple_atr(m15_bars, atr_period) if len(m15_bars) >= atr_period else 0.001
        structures = self._run_detectors(m15_bars, atr_m15)

        # 4. Build context
        spread = abs(tick["ask"] - tick["bid"])
        context = build_context_snapshot(
            symbol=symbol,
            bars_m15=m15_bars,
            bars_h1=h1_bars,
            detected_structures=structures,
            spread=spread,
            config=self.config,
        )

        # 5. Evaluate decision with runtime state
        entry_price = float(tick["ask"]) if context.trend_m15.value == "BULLISH" else float(tick["bid"])
        outcome = evaluate_decision(
            structures=structures,
            context=context,
            config=self.config,
            entry_price=entry_price,
            atr_override=None,
            risk_state={"account_balance": account_balance},
            runtime_state=self.runtime_state,
        )

        decision_id = f"{run_id}_{symbol}_dec"
        self.runtime_state.record_decision(decision_id)

        # 6. Write telemetry
        self.writer.write_decision_outcome(
            run_id=run_id,
            scan_id=f"{run_id}_{symbol}_scan",
            config_hash="cfg_hash",
            snapshot_id=f"{run_id}_{symbol}_snapshot",
            context=context,
            outcome=outcome,
            entry_price=entry_price,
            instrument_point=profile["point"] if isinstance(profile, dict) else profile.point,
            decision_id=decision_id,
        )

        # 7. Write snapshot
        snapshot = SnapshotRecord(
            snapshot_id=f"{run_id}_{symbol}_snapshot",
            symbol=symbol,
            decision_timestamp=datetime.now(tz=UTC),
            session=context.session,
            m15_bars=m15_bars,
            h1_bars=h1_bars,
            atr_m15=atr_m15,
            atr_h1=0.0,
            spread=spread,
            detected_structures=structures,
            context_snapshot=context,
            config_hash="cfg_hash",
            symbol_profile=profile,
        )
        self.writer.write_snapshot(to_primitive(snapshot))

        # 8. Paper fill if EXECUTE
        paper_fill: dict[str, Any] | None = None
        if outcome.final_decision == FinalDecision.EXECUTE and outcome.trade_intent is not None:
            fill = self.adapter.execute(
                intent=outcome.trade_intent,
                spread_at_decision=spread,
            )
            paper_fill = {
                "decision_id": fill.decision_id,
                "trade_id": fill.trade_id,
                "ticket": fill.ticket,
                "side": fill.side,
                "intended_entry": fill.intended_entry,
                "actual_fill": fill.actual_fill,
                "slippage": fill.slippage,
                "spread_at_fill": fill.spread_at_fill,
                "order_status": fill.order_status,
            }
            self.writer.write_trade(paper_fill)
            self.runtime_state.record_trade(fill.trade_id)
            self.position_tracker.open_position(fill=fill)
            # Update lot size if available from risk verdict
            if outcome.trade_intent and outcome.trade_intent.risk_verdict:
                self.position_tracker.update_lot_size(
                    fill.trade_id,
                    outcome.trade_intent.risk_verdict.lot_size,
                )

        return SymbolResult(
            symbol=symbol,
            decision=outcome.final_decision,
            failure_code=outcome.failure_code,
            bars_m15_count=len(m15_bars),
            bars_h1_count=len(h1_bars),
            tick_bid=tick["bid"],
            tick_ask=tick["ask"],
            paper_fill=paper_fill,
            snapshot_id=f"{run_id}_{symbol}_snapshot",
        )
