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
from src.context.references import compute_reference_levels
from src.context.session_levels import SessionLevelTracker
from src.core.enums import FinalDecision, Namespace, Timeframe
from src.core.models import Bar, SnapshotRecord, to_primitive
from src.data.mt5_source import MT5DataSource
from src.decision.engine import evaluate_decision
from src.core.runtime_state import RuntimeState
from src.data.base import DataSourceError
from src.execution.paper_adapter import PaperExecutionAdapter
from src.execution.position_tracker import PaperPositionTracker
from src.ops.telemetry import TelemetryWriter
from src.context.regime import simple_atr
from src.execution.structure_detectors import (
    run_all_detectors,
    scale_detection_cfg_for_higher_tf,
)
from src.core.enums import StructureType
from src.risk.usd_correlation import count_usd_positions
from src.zones.tracker import ZoneTracker


_DEFAULT_M15_BAR_COUNT = 250
_DEFAULT_H1_BAR_COUNT = 250
_DEFAULT_H4_BAR_COUNT = 300


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
    tp_debug: dict[str, Any] | None = None


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
        data_source: Any | None = None,
    ) -> None:
        self.config = config
        self.symbols = symbols or ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"]
        self.data = data_source if data_source is not None else MT5DataSource(
            broker_utc_offset_hours=int(config.get("broker_utc_offset_hours", 0))
        )
        self.writer = TelemetryWriter(logs_root=logs_root, namespace=namespace)
        self.adapter = PaperExecutionAdapter()
        self.runtime_state = RuntimeState()
        self.position_tracker = PaperPositionTracker()
        zone_age = int(
            self.config.get("detection", {})
            .get("order_block", {})
            .get("max_age_bars", 50)
        )
        self._zone_tracker: ZoneTracker = ZoneTracker(max_zone_age_bars=zone_age)

    def close(self) -> None:
        self.data.close()

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
            m15_bars = self.data.fetch_bars(symbol, Timeframe.M15, count=_DEFAULT_M15_BAR_COUNT)
            h1_bars = self.data.fetch_bars(symbol, Timeframe.H1, count=_DEFAULT_H1_BAR_COUNT)
            h4_bars = self.data.fetch_bars(symbol, Timeframe.H4, count=_DEFAULT_H4_BAR_COUNT)
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

        # 3. Detect structures on M15 and H1
        det_cfg = self.config["detection"]
        atr_period = int(det_cfg["atr_period"])
        atr_m15 = simple_atr(m15_bars, atr_period) if len(m15_bars) >= atr_period else 0.001
        m15_structures = run_all_detectors(detection_cfg=det_cfg, bars=m15_bars, atr=atr_m15)

        h1_age_mult = float(det_cfg.get("h1_detection_age_multiplier", 2.5))
        h1_det_cfg = scale_detection_cfg_for_higher_tf(det_cfg, h1_age_mult)
        atr_h1 = simple_atr(h1_bars, atr_period) if len(h1_bars) >= atr_period else atr_m15
        h1_structures = run_all_detectors(detection_cfg=h1_det_cfg, bars=h1_bars, atr=atr_h1)
        raw_structures = [*m15_structures, *h1_structures]

        # Zone tracker: update mitigation, expire old zones, register fresh detections.
        # Returns only ACTIVE structures (OBs not closed through, BOS not yet consumed).
        current_bar = m15_bars[-1] if m15_bars else None
        if current_bar is not None:
            self._zone_tracker.scan(symbol, raw_structures, current_bar)
            structures = self._zone_tracker.get_active_structures(symbol)
        else:
            structures = raw_structures

        # 3b. Wider TP structure pool — same detectors, larger max_age_bars.
        # Used only for TP target anchoring. SL and confluence stay on the regular pool.
        tp_age_mult = float(det_cfg.get("tp_detection_age_multiplier", 4.0))
        tp_m15_det_cfg = scale_detection_cfg_for_higher_tf(det_cfg, tp_age_mult)
        tp_m15_structures = run_all_detectors(detection_cfg=tp_m15_det_cfg, bars=m15_bars, atr=atr_m15)
        tp_h1_det_cfg = scale_detection_cfg_for_higher_tf(det_cfg, h1_age_mult * tp_age_mult)
        tp_h1_structures = run_all_detectors(detection_cfg=tp_h1_det_cfg, bars=h1_bars, atr=atr_h1)
        tp_structures = [*tp_m15_structures, *tp_h1_structures]

        # 3c. Build session levels for narrative layer
        _session_tracker = SessionLevelTracker()
        session_levels = _session_tracker.compute(m15_bars, self.config.get("sessions", {}))

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
        references = compute_reference_levels(m15_bars)

        # 5. Evaluate decision with runtime state
        entry_price = float(tick["ask"]) if context.trend_m15.value == "BULLISH" else float(tick["bid"])
        outcome = evaluate_decision(
            structures=structures,
            context=context,
            config=self.config,
            entry_price=entry_price,
            references=references,
            atr_override=None,
            risk_state={
                "account_balance": account_balance,
                "open_positions_total": len(self.position_tracker.get_open_positions()),
                "new_trades_session": self.runtime_state.trade_count,
                "correlated_positions": 0,
                "same_direction_correlated_positions": 0,
                "usd_correlated_positions": count_usd_positions(
                    [p.symbol for p in self.position_tracker.get_open_positions()]
                ),
            },
            runtime_state=self.runtime_state,
            tp_structures=tp_structures,
            session_levels=session_levels,
            bars_h4=h4_bars,
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
            atr_h1=atr_h1,
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

            # Mark all BOS structures in the winning confluence as CONSUMED.
            if outcome.confluence is not None and current_bar is not None:
                all_confluence_structures = [
                    outcome.confluence.primary_trigger,
                    *outcome.confluence.structural_confirmations,
                ]
                for s in all_confluence_structures:
                    if s is not None and s.structure_type == StructureType.BREAK_OF_STRUCTURE:
                        self._zone_tracker.mark_bos_consumed(
                            symbol=symbol,
                            bar_index=s.bar_index,
                            timeframe=s.timeframe,
                            current_bar_index=current_bar.bar_index,
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
