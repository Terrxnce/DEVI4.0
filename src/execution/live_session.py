"""Live session runner: full M15 scan cycle with real execution via LiveOrderWrapper.

Safety rules:
- Requires valid arming token before execution
- Kill switch checked at decision time
- Max orders enforced via RuntimeState
- Pre-trade rechecks in LiveOrderWrapper before order_send
- LivePositionTracker syncs with MT5 after send
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.context.builder import build_context_snapshot
from src.core.arming import ArmingService, LiveArmingToken
from src.core.enums import FinalDecision, Namespace, Timeframe
from src.core.kill_switch import KillSwitch
from src.core.models import Bar, DetectedStructure, SnapshotRecord, to_primitive
from src.core.runtime_state import RuntimeState
from src.data.base import DataSourceError
from src.data.mt5_source import MT5DataSource
from src.decision.engine import evaluate_decision
from src.detectors.break_of_structure import BreakOfStructureDetector
from src.detectors.engulfing import EngulfingDetector
from src.detectors.fair_value_gap import FairValueGapDetector
from src.detectors.liquidity_sweep import LiquiditySweepDetector
from src.detectors.order_block import OrderBlockDetector
from src.detectors.rejection import RejectionDetector
from src.execution.live_position_tracker import LivePosition, LivePositionTracker
from src.execution.live_wrapper import LiveOrderWrapper
from src.execution.paper_session import SymbolResult
from src.ops.telemetry import TelemetryWriter
from src.context.regime import simple_atr


@dataclass(frozen=True)
class LiveSessionResult:
    run_id: str
    symbol_results: dict[str, SymbolResult]
    account_balance: float
    account_equity: float
    decision_count: int
    trade_count: int
    open_position_count: int
    live_positions: list[LivePosition]
    execution_summary: dict[str, Any]


class LiveSession:
    """Run a complete live session: decision pipeline + real execution."""

    def __init__(
        self,
        *,
        config: dict[str, Any],
        logs_root: str,
        namespace: Namespace,
        symbols: list[str] | None = None,
    ) -> None:
        self.config = config
        self.symbols = symbols or ["EURUSD"]
        self.data = MT5DataSource()
        self.writer = TelemetryWriter(logs_root=logs_root, namespace=namespace)
        self.wrapper = LiveOrderWrapper(data_source=self.data, telemetry_writer=self.writer)
        self.runtime_state = RuntimeState()
        self.position_tracker: LivePositionTracker | None = None
        self.arming_service = ArmingService()
        self.kill_switch = KillSwitch()

    def close(self) -> None:
        self.data.close()

    def _run_detectors(self, bars: list[Bar], atr: float) -> list[DetectedStructure]:
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

    def run(
        self,
        *,
        run_id: str,
        token: LiveArmingToken,
    ) -> LiveSessionResult:
        """Execute one full live session for all configured symbols."""
        self.runtime_state = RuntimeState(run_id=run_id)
        self.position_tracker = LivePositionTracker(self.data.mt5_client)
        account = self.data.fetch_account_info()
        symbol_results: dict[str, SymbolResult] = {}
        execution_summary: dict[str, Any] = {
            "token_id": str(token.token_id),
            "armed": True,
            "orders_attempted": 0,
            "orders_filled": 0,
            "orders_rejected": 0,
        }

        for symbol in sorted(self.symbols):
            result = self._run_symbol(
                symbol=symbol,
                run_id=run_id,
                account_balance=account["balance"],
                token=token,
            )
            symbol_results[symbol] = result

            if result.paper_fill is not None:
                execution_summary["orders_attempted"] += 1
                if result.paper_fill.get("order_status") == "FILLED":
                    execution_summary["orders_filled"] += 1
                else:
                    execution_summary["orders_rejected"] += 1

        # Sync positions from MT5
        live_positions: list[LivePosition] = []
        if self.position_tracker is not None:
            live_positions = self.position_tracker.sync_positions()

        return LiveSessionResult(
            run_id=run_id,
            symbol_results=symbol_results,
            account_balance=account["balance"],
            account_equity=account["equity"],
            decision_count=self.runtime_state.decision_count,
            trade_count=self.runtime_state.trade_count,
            open_position_count=len(
                [p for p in live_positions if p.status == "OPEN"]
            ),
            live_positions=live_positions,
            execution_summary=execution_summary,
        )

    def _run_symbol(
        self,
        *,
        symbol: str,
        run_id: str,
        account_balance: float,
        token: LiveArmingToken,
    ) -> SymbolResult:
        """Run one decision cycle for a single symbol with live execution."""
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

        # 8. LIVE execution if EXECUTE
        live_fill: dict[str, Any] | None = None
        if outcome.final_decision == FinalDecision.EXECUTE and outcome.trade_intent is not None:
            intent = outcome.trade_intent

            # Call LiveOrderWrapper with dynamic lot sizing params
            result = self.wrapper.send(
                intent=intent,
                arming_service=self.arming_service,
                kill_switch=self.kill_switch,
                runtime_state=self.runtime_state,
                decision_spread=spread,
                max_orders_per_run=self.config.get("execution", {}).get("max_orders_per_run", 1),
                risk_dynamic_lot_sizing=bool(self.config.get("risk", {}).get("dynamic_lot_sizing", True)),
                risk_fixed_lot_size=float(self.config.get("risk", {}).get("fixed_lot_size", intent.risk_verdict.lot_size)),
            )

            live_fill = {
                "decision_id": decision_id,
                "trade_id": intent.trade_id,
                "ticket": result.ticket,
                "side": intent.direction.value,
                "intended_entry": intent.entry_price,
                "actual_fill": result.entry_price if result.entry_price else intent.entry_price,
                "slippage": result.slippage,
                "spread_at_fill": spread,
                "order_status": result.status,
                "broker_retcode": result.broker_retcode,
            }
            self.writer.write_trade(live_fill)
            self.runtime_state.record_trade(intent.trade_id)

            # Record in position tracker
            if self.position_tracker is not None and result.ticket is not None:
                self.position_tracker.record_sent_order(
                    ticket=result.ticket,
                    trade_id=intent.trade_id,
                    decision_id=decision_id,
                    symbol=intent.symbol,
                    side=intent.direction.value,
                    lot_size=intent.risk_verdict.lot_size,
                    open_price=live_fill["actual_fill"],
                    sl=intent.exit_plan.stop_loss,
                    tp=intent.exit_plan.take_profit,
                )

        return SymbolResult(
            symbol=symbol,
            decision=outcome.final_decision,
            failure_code=outcome.failure_code,
            bars_m15_count=len(m15_bars),
            bars_h1_count=len(h1_bars),
            tick_bid=tick["bid"],
            tick_ask=tick["ask"],
            paper_fill=live_fill,
            snapshot_id=f"{run_id}_{symbol}_snapshot",
        )
