"""Live session runner: full M15 scan cycle with real execution via LiveOrderWrapper.

Safety rules:
- Requires valid arming token before execution
- Kill switch checked at decision time
- Max orders enforced via RuntimeState
- Pre-trade rechecks in LiveOrderWrapper before order_send
- LivePositionTracker syncs with MT5 after send
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

from src.context.builder import build_context_snapshot, classify_session
from src.context.references import compute_reference_levels
from src.context.session_levels import SessionLevelTracker
from src.core.arming import ArmingService, LiveArmingToken
from src.core.enums import FinalDecision, Namespace, Session, Timeframe
from src.core.kill_switch import KillSwitch
from src.core.models import Bar, DetectedStructure, SnapshotRecord, to_primitive
from src.core.runtime_state import RuntimeState
from src.core.enums import StructureType
from src.data.base import DataSourceError
from src.data.mt5_source import MT5DataSource
from src.decision.engine import evaluate_decision
from src.execution.live_position_tracker import LivePosition, LivePositionTracker
from src.execution.trailing_manager import TrailingManager
from src.execution.structure_detectors import (
    run_all_detectors,
    scale_detection_cfg_for_higher_tf,
)
from src.execution.live_wrapper import LiveOrderWrapper
from src.execution.paper_session import SymbolResult
from src.ops.telemetry import TelemetryWriter
from src.ops.supabase_writer import SupabaseWriter
from src.ops.ftmo_risk_monitor import FTMORiskMonitor
from src.ops.economic_calendar import EconomicCalendar
from src.context.regime import simple_atr
from src.zones.tracker import ZoneTracker
from src.risk.usd_correlation import count_jpy_positions, count_usd_positions


_DEFAULT_M15_BAR_COUNT = 250
_DEFAULT_H1_BAR_COUNT = 400   # ~16 days of hourly context
_DEFAULT_H4_BAR_COUNT = 300   # ~50 days of 4H context — captures structures 7 weeks back


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


def _session_name_at(dt: datetime, sessions_cfg: dict) -> str | None:
    """Return the session name ('ASIA', 'LONDON', etc.) active at dt, or None."""
    t = dt.time()
    for name in ("ASIA", "LONDON", "NY_AM", "NY_PM"):
        spec = sessions_cfg.get(name)
        if not isinstance(spec, dict):
            continue
        sh, sm = (int(x) for x in spec.get("start", "00:00").split(":"))
        eh, em = (int(x) for x in spec.get("end", "00:00").split(":"))
        from datetime import time as _time
        start_t = _time(sh, sm)
        end_t = _time(eh, em)
        if start_t <= t < end_t:
            return name
    return None


def _origin_session_ended(pos: "LivePosition", now: datetime, sessions_cfg: dict) -> bool:
    """Return True if the session the position was opened in has since ended.

    Uses the position's open_time to determine which session it belongs to,
    then checks whether that session's end time has passed today (UTC).
    """
    try:
        open_dt = datetime.fromisoformat(pos.open_time)
    except (ValueError, TypeError):
        return False
    origin = _session_name_at(open_dt, sessions_cfg)
    if origin is None:
        return False  # opened outside sessions — leave it alone
    spec = sessions_cfg.get(origin, {})
    eh, em = (int(x) for x in spec.get("end", "00:00").split(":"))
    session_end = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    return now >= session_end


class LiveSession:
    """Run a complete live session: decision pipeline + real execution."""

    def __init__(
        self,
        *,
        config: dict[str, Any],
        logs_root: str,
        namespace: Namespace,
        symbols: list[str] | None = None,
        arming_service: ArmingService | None = None,
        kill_switch: KillSwitch | None = None,
    ) -> None:
        self.config = config
        self.symbols = symbols or ["EURUSD"]
        self.data = MT5DataSource(
            broker_utc_offset_hours=int(config.get("broker_utc_offset_hours", 0))
        )

        # Supabase remote writer — optional, gated by config and env vars.
        # If SUPABASE_URL / SUPABASE_KEY are not set, or supabase.enabled is
        # false in config, SupabaseWriter disables itself silently.
        _sb_cfg = config.get("supabase", {})
        _sb_writer: SupabaseWriter | None = None
        if _sb_cfg.get("enabled", False):
            _sb_url = os.environ.get("SUPABASE_URL", "")
            _sb_key = os.environ.get("SUPABASE_KEY", "")
            if _sb_url and _sb_key:
                _sb_writer = SupabaseWriter(
                    url=_sb_url,
                    key=_sb_key,
                    account_id=_sb_cfg.get("account_id", "default"),
                )
            else:
                logger.warning(
                    "live_session: supabase.enabled=true but SUPABASE_URL / "
                    "SUPABASE_KEY env vars not set — remote writes disabled."
                )
        self._supabase: SupabaseWriter | None = _sb_writer

        self.writer = TelemetryWriter(
            logs_root=logs_root,
            namespace=namespace,
            supabase_writer=_sb_writer,
        )
        self.wrapper = LiveOrderWrapper(data_source=self.data, telemetry_writer=self.writer)
        self.runtime_state = RuntimeState()
        self.position_tracker: LivePositionTracker | None = None
        self.arming_service = arming_service or ArmingService()
        self.kill_switch = kill_switch or KillSwitch()
        # Session-level OB zone invalidation: tracks (symbol, direction, price_low, price_high)
        # tuples for every OB zone where a trade was placed this session.
        # Cleared only when the LiveSession object is re-instantiated (process restart).
        self._used_ob_zones: list[tuple[str, str, float, float]] = []

        # Zone tracker: persistent zone registry for the session.
        # Tracks OB/FVG mitigation (price-based) and BOS one-time consumption
        # across scan iterations. max_zone_age_bars mirrors the OB max_age_bars
        # default — zones older than this are expired regardless of price action.
        zone_age = int(
            self.config.get("detection", {})
            .get("order_block", {})
            .get("max_age_bars", 50)
        )
        self._zone_tracker: ZoneTracker = ZoneTracker(max_zone_age_bars=zone_age)

    def close(self) -> None:
        self.data.close()

    def _close_position_at_market(
        self,
        pos: LivePosition,
        run_id: str,
        reason: str = "session_close_exit",
    ) -> bool:
        """Send a market order to fully close an open position.

        Uses TRADE_ACTION_DEAL with the opposite side and position=ticket.
        Returns True on confirmed fill (retcode 10009), False otherwise.
        Silent on missing broker — never raises.
        """
        mt5 = self.data.mt5_client
        if mt5 is None or not callable(getattr(mt5, "order_send", None)):
            logger.warning(
                "session_close: no mt5 client — cannot close ticket=%s", pos.ticket
            )
            return False

        is_long = pos.side in ("BUY", "BULLISH")
        close_type = (
            getattr(mt5, "ORDER_TYPE_SELL", 1)
            if is_long
            else getattr(mt5, "ORDER_TYPE_BUY", 0)
        )
        request = {
            "action": getattr(mt5, "TRADE_ACTION_DEAL", 1),
            "symbol": pos.symbol,
            "volume": pos.lot_size,
            "type": close_type,
            "position": pos.ticket,
            "price": pos.current_price,
            "deviation": 10,
            "magic": self.config.get("magic_number", 234000),
            "comment": f"devi:{reason}:{pos.ticket}"[:27],
            "type_filling": getattr(mt5, "ORDER_FILLING_IOC", 1),
        }

        logger.info(
            "session_close: closing ticket=%s symbol=%s side=%s lot=%.2f reason=%s",
            pos.ticket, pos.symbol, pos.side, pos.lot_size, reason,
        )

        try:
            result = mt5.order_send(request)
        except Exception as exc:
            logger.error("session_close: order_send raised ticket=%s: %s", pos.ticket, exc)
            return False

        if result is None:
            logger.error("session_close: order_send returned None ticket=%s", pos.ticket)
            return False

        retcode = int(getattr(result, "retcode", -1))
        confirmed = retcode == 10009

        self.writer.write_position_event({
            "event": reason,
            "ticket": pos.ticket,
            "symbol": pos.symbol,
            "side": pos.side,
            "lot_size": pos.lot_size,
            "retcode": retcode,
            "confirmed": confirmed,
            "reason": reason,
            "run_id": run_id,
            "timestamp": datetime.now(tz=UTC).isoformat(),
        })

        if confirmed:
            logger.info("session_close: confirmed ticket=%s", pos.ticket)
        else:
            logger.warning(
                "session_close: not confirmed ticket=%s retcode=%s", pos.ticket, retcode
            )
        return confirmed

    def _record_used_ob_zone(
        self,
        symbol: str,
        direction: str,
        price_low: float,
        price_high: float,
    ) -> None:
        """Record an OB zone as used after a trade is placed at it."""
        self._used_ob_zones.append((symbol, direction, price_low, price_high))

    def _is_ob_zone_invalidated(
        self,
        symbol: str,
        direction: str,
        price_low: float,
        price_high: float,
        atr: float,
    ) -> bool:
        """Return True if this OB zone overlaps a zone already used this session.

        Overlap is checked with 1-ATR tolerance on each side so nearby zones
        also get blocked. This prevents re-entry at essentially the same level.
        """
        tolerance = atr
        for used_sym, used_dir, used_low, used_high in self._used_ob_zones:
            if used_sym != symbol or used_dir != direction:
                continue
            # Expand used zone by tolerance and check overlap with candidate
            expanded_low = used_low - tolerance
            expanded_high = used_high + tolerance
            if price_low < expanded_high and price_high > expanded_low:
                return True
        return False

    def _filter_session_invalidated_structures(
        self,
        symbol: str,
        structures: list[DetectedStructure],
        atr: float,
    ) -> list[DetectedStructure]:
        """Remove OB structures that overlap a zone already used this session.

        Non-OB structures are passed through unchanged.
        """
        filtered: list[DetectedStructure] = []
        for s in structures:
            if s.structure_type != StructureType.ORDER_BLOCK:
                filtered.append(s)
                continue
            if self._is_ob_zone_invalidated(
                symbol=symbol,
                direction=s.direction.value,
                price_low=s.price_low,
                price_high=s.price_high,
                atr=atr,
            ):
                continue  # drop this OB — zone already used
            filtered.append(s)
        return filtered

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
        _tracker_state_path = self.writer.guard.namespace_path(
            self.writer.namespace, "position_state.json"
        )
        self.position_tracker = LivePositionTracker(
            self.data.mt5_client,
            state_path=_tracker_state_path,
        )
        _mgmt_cfg = self.config.get("exits", {}).get("management", {})
        _partials_enabled = bool(_mgmt_cfg.get("partials_enabled", False))
        trailing_manager = TrailingManager(
            self.data.mt5_client,
            magic_number=self.config.get("magic_number", 234000),
            min_lot=self.config.get("min_lot", 0.01),
            lot_step=self.config.get("lot_step", 0.01),
            trail_distance_r=self.config.get("trail_distance_r", 0.5),
            breakeven_buffer=self.config.get("breakeven_buffer", 0.0001),
            partial_close_ratio=0.5 if _partials_enabled else 0.0,
        )

        account = self.data.fetch_account_info()

        # Sync open positions from MT5 at the START of the run so we can skip
        # symbols that already have open positions and build real risk state.
        # Also detects and logs any positions that closed since the last scan.
        open_positions = self.position_tracker.sync_positions()
        for closed_pos in self.position_tracker.get_newly_closed():
            _close_ts = datetime.now(tz=UTC).isoformat()
            self.writer.write_position_event({
                "event": "position_close",
                "ticket": closed_pos.ticket,
                "symbol": closed_pos.symbol,
                "side": closed_pos.side,
                "lot_size": closed_pos.lot_size,
                "open_price": closed_pos.open_price,
                "close_price": closed_pos.close_price,
                "close_time": closed_pos.close_time,
                "close_reason": closed_pos.close_reason,
                "close_pnl": closed_pos.close_pnl,
                "trade_id": closed_pos.trade_id,
                "decision_id": closed_pos.decision_id,
                "run_id": run_id,
                "timestamp": _close_ts,
            })
            # Update the trades JSONL so the audit trail reflects the final state.
            # The original write_trade record has status='open'; this close record
            # has status='closed' with full exit data. Consumers take the last
            # record per trade_id to resolve final state.
            if closed_pos.trade_id:
                self.writer.write_trade_close({
                    "event": "trade_close",
                    "trade_id": closed_pos.trade_id,
                    "decision_id": closed_pos.decision_id,
                    "ticket": closed_pos.ticket,
                    "symbol": closed_pos.symbol,
                    "side": closed_pos.side,
                    "lot_size": closed_pos.lot_size,
                    "open_price": closed_pos.open_price,
                    "close_price": closed_pos.close_price,
                    "close_time": closed_pos.close_time,
                    "close_reason": closed_pos.close_reason,
                    "close_pnl": closed_pos.close_pnl,
                    "status": "closed",
                    "run_id": run_id,
                    "timestamp": _close_ts,
                })

        # Run trailing / breakeven / partial-close management on all open positions.
        trail_events = trailing_manager.process_positions(open_positions)
        for ev in trail_events:
            self.writer.write_position_event({
                "event": ev.event_type,
                "ticket": ev.ticket,
                "symbol": ev.symbol,
                "run_id": run_id,
                "timestamp": ev.timestamp,
                **ev.detail,
            })

        # Session close exit — force-close any position whose origin session has ended.
        # Runs after trailing manager so trail/breakeven events are recorded first.
        _session_closes_sent = False
        if _mgmt_cfg.get("session_close_exit", False):
            _sessions_cfg = self.config.get("sessions", {})
            _now = datetime.now(tz=UTC)
            _still_open: list[LivePosition] = []
            for _pos in open_positions:
                if _origin_session_ended(_pos, _now, _sessions_cfg):
                    logger.info(
                        "session_close_exit: origin session ended for ticket=%s symbol=%s "
                        "opened=%s",
                        _pos.ticket, _pos.symbol, _pos.open_time,
                    )
                    self._close_position_at_market(_pos, run_id, reason="session_close_exit")
                    _session_closes_sent = True
                else:
                    _still_open.append(_pos)
            open_positions = _still_open

        # Re-fetch account after session closes so the FTMO snapshot and daily
        # summary reflect the settled post-close balance, not the pre-close value
        # captured at cycle start. Only re-fetches when closes were actually sent
        # to avoid an unnecessary MT5 call on quiet cycles.
        if _session_closes_sent:
            account = self.data.fetch_account_info()

        balance = float(account["balance"])
        equity = float(account.get("equity", balance))

        # --- FTMO risk monitor -------------------------------------------
        ftmo_cfg = self.config.get("ftmo", {})
        _ftmo_initial = float(ftmo_cfg.get("initial_balance", balance))
        _ftmo_state_path = self.writer.guard.namespace_path(
            self.writer.namespace, "ftmo_state.json"
        )
        ftmo_monitor = FTMORiskMonitor(
            initial_balance=_ftmo_initial,
            max_daily_loss_pct=float(ftmo_cfg.get("max_daily_loss_pct", 0.05)),
            max_total_loss_pct=float(ftmo_cfg.get("max_total_loss_pct", 0.10)),
            daily_buffer_pct=float(ftmo_cfg.get("daily_buffer_pct", 0.005)),
            total_buffer_pct=float(ftmo_cfg.get("total_buffer_pct", 0.005)),
            state_path=_ftmo_state_path,
        )
        ftmo_monitor.start_of_day_snapshot(balance)
        ftmo_result = ftmo_monitor.evaluate(equity=equity, balance=balance)

        # Write FTMO snapshot to telemetry every cycle for audit trail
        self.writer.write_position_event({
            "event": "ftmo_risk_snapshot",
            "run_id": run_id,
            "daily_ok": ftmo_result.daily_ok,
            "total_ok": ftmo_result.total_ok,
            "daily_pnl_pct": ftmo_result.daily_pnl_pct,
            "total_pnl_pct": ftmo_result.total_pnl_pct,
            "daily_floor": ftmo_result.daily_floor,
            "total_floor": ftmo_result.total_floor,
            "day_start_balance": ftmo_result.day_start_balance,
            "equity": equity,
            "balance": balance,
            "reason": ftmo_result.reason,
            "timestamp": datetime.now(tz=UTC).isoformat(),
        })

        # Mirror FTMO state to Supabase and emit a heartbeat so the dashboard
        # can confirm D.E.V.I is alive and show current risk position.
        if self._supabase is not None:
            self._supabase.write_ftmo_snapshot(
                run_id=run_id,
                daily_pnl_pct=ftmo_result.daily_pnl_pct,
                total_pnl_pct=ftmo_result.total_pnl_pct,
                daily_ok=ftmo_result.daily_ok,
                total_ok=ftmo_result.total_ok,
                daily_floor=ftmo_result.daily_floor,
                total_floor=ftmo_result.total_floor,
                equity=equity,
                balance=balance,
                reason=ftmo_result.reason,
            )
            self._supabase.write_heartbeat(
                run_id=run_id,
                open_positions=len(open_positions),
                daily_pnl_pct=ftmo_result.daily_pnl_pct,
                total_pnl_pct=ftmo_result.total_pnl_pct,
                daily_ok=ftmo_result.daily_ok,
                total_ok=ftmo_result.total_ok,
                equity=equity,
                balance=balance,
            )
            # Sync live position state so the dashboard can show floating P&L.
            # Uses the final open_positions list (post session_close_exit) so
            # positions closed this cycle are excluded from the snapshot.
            self._supabase.sync_live_positions([
                {
                    "ticket": p.ticket,
                    "trade_id": p.trade_id,
                    "decision_id": p.decision_id,
                    "symbol": p.symbol,
                    "side": p.side,
                    "lot_size": p.lot_size,
                    "open_price": p.open_price,
                    "current_price": p.current_price,
                    "sl": p.sl,
                    "tp": p.tp,
                    "profit": p.profit,
                    "swap": p.swap,
                    "open_time": p.open_time,
                }
                for p in open_positions
            ])
        # ----------------------------------------------------------------

        base_risk_state: dict[str, Any] = {
            "account_balance": balance,
            "daily_pnl_pct": ftmo_result.daily_pnl_pct,
            "total_pnl_pct": ftmo_result.total_pnl_pct,
            "open_positions_total": len(open_positions),
            "new_trades_session": self.runtime_state.trade_count,
            "correlated_positions": 0,
            "same_direction_correlated_positions": 0,
            "usd_correlated_positions": count_usd_positions(
                [p.symbol for p in open_positions]
            ),
            "jpy_correlated_positions": count_jpy_positions(
                [p.symbol for p in open_positions]
            ),
        }

        # FTMO floor breach — halt all new trade evaluation immediately.
        # Existing positions are still managed by the trailing manager above.
        if not ftmo_result.daily_ok:
            logger.critical(
                "live_session: FTMO daily floor breached — skipping all symbol evaluation. %s",
                ftmo_result.reason,
            )
            return LiveSessionResult(
                run_id=run_id,
                symbol_results={},
                account_balance=balance,
                account_equity=equity,
                decision_count=self.runtime_state.decision_count,
                trade_count=self.runtime_state.trade_count,
                open_position_count=len([p for p in open_positions if p.status == "OPEN"]),
                live_positions=open_positions,
                execution_summary={"halted": "ftmo_daily_floor_breached", "reason": ftmo_result.reason},
            )

        if not ftmo_result.total_ok:
            logger.critical(
                "live_session: FTMO total floor breached — skipping all symbol evaluation. %s",
                ftmo_result.reason,
            )
            return LiveSessionResult(
                run_id=run_id,
                symbol_results={},
                account_balance=balance,
                account_equity=equity,
                decision_count=self.runtime_state.decision_count,
                trade_count=self.runtime_state.trade_count,
                open_position_count=len([p for p in open_positions if p.status == "OPEN"]),
                live_positions=open_positions,
                execution_summary={"halted": "ftmo_total_floor_breached", "reason": ftmo_result.reason},
            )

        # Compute current session once — used for per-symbol session filtering
        current_session = classify_session(datetime.now(tz=UTC), self.config.get("sessions", {}))

        # Economic calendar — refresh if stale, then check per symbol in loop
        _cal_cfg = self.config.get("economic_calendar", {})
        _cal_enabled = bool(_cal_cfg.get("enabled", True))
        _cal_cache_path = self.writer.guard.namespace_path(
            self.writer.namespace, "calendar_cache.json"
        )
        economic_calendar = EconomicCalendar(
            cache_path=_cal_cache_path,
            pre_event_minutes=int(_cal_cfg.get("pre_event_minutes", 30)),
            post_event_minutes=int(_cal_cfg.get("post_event_minutes", 15)),
            block_on_fetch_failure=bool(_cal_cfg.get("block_on_fetch_failure", True)),
            cache_ttl_hours=float(_cal_cfg.get("cache_ttl_hours", 12.0)),
        )
        if _cal_enabled:
            economic_calendar.refresh_if_stale()

        now_utc = datetime.now(tz=UTC)
        symbol_results: dict[str, SymbolResult] = {}
        execution_summary: dict[str, Any] = {
            "token_id": str(token.token_id),
            "armed": True,
            "orders_attempted": 0,
            "orders_filled": 0,
            "orders_rejected": 0,
        }

        for symbol in sorted(self.symbols):
            # Check economic calendar before evaluating the symbol
            if _cal_enabled:
                _news_blocked, _news_reason = economic_calendar.is_news_blocked(symbol, now_utc)
                if _news_blocked:
                    logger.info(
                        "live_session: symbol=%s skipped — %s", symbol, _news_reason
                    )
                    symbol_results[symbol] = SymbolResult(
                        symbol=symbol,
                        decision=FinalDecision.HOLD,
                        failure_code="news_blackout",
                        bars_m15_count=0,
                        bars_h1_count=0,
                        tick_bid=0.0,
                        tick_ask=0.0,
                        paper_fill=None,
                        snapshot_id="",
                        skipped_reason=_news_reason,
                    )
                    continue

            result = self._run_symbol(
                symbol=symbol,
                run_id=run_id,
                account_balance=balance,
                token=token,
                risk_state=base_risk_state,
                current_session=current_session,
            )
            symbol_results[symbol] = result

            if result.paper_fill is not None:
                execution_summary["orders_attempted"] += 1
                if result.paper_fill.get("order_status") == "FILLED":
                    execution_summary["orders_filled"] += 1
                else:
                    execution_summary["orders_rejected"] += 1

        # Final sync to capture any positions opened this cycle
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
            open_position_count=len([p for p in live_positions if p.status == "OPEN"]),
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
        risk_state: dict[str, Any],
        current_session: Session,
    ) -> SymbolResult:
        """Run one decision cycle for a single symbol with live execution."""
        # 0a. Per-symbol session filter.
        #     symbol_sessions maps each symbol to its allowed sessions.
        #     Falls back to "default" entry, then allows all if neither is defined.
        sym_sess_cfg = self.config.get("symbol_sessions", {})
        default_sessions: list[str] = sym_sess_cfg.get("default", ["ASIA", "LONDON", "NY_AM", "NY_PM"])
        allowed_sessions: list[str] = sym_sess_cfg.get(symbol, default_sessions)
        if current_session.value not in allowed_sessions:
            return SymbolResult(
                symbol=symbol,
                decision=FinalDecision.HOLD,
                failure_code=f"outside_symbol_session:{current_session.value}",
                bars_m15_count=0,
                bars_h1_count=0,
                tick_bid=0.0,
                tick_ask=0.0,
                paper_fill=None,
                snapshot_id=f"{run_id}_{symbol}_snapshot",
                skipped_reason=f"outside_symbol_session:{current_session.value}",
            )

        # 0b. Skip if MT5 already has an open position for this symbol.
        #     Position tracker was synced at the start of run() — reflects current broker state.
        if self.position_tracker is not None and self.position_tracker.has_open_position(symbol):
            return SymbolResult(
                symbol=symbol,
                decision=FinalDecision.HOLD,
                failure_code="existing_open_position",
                bars_m15_count=0,
                bars_h1_count=0,
                tick_bid=0.0,
                tick_ask=0.0,
                paper_fill=None,
                snapshot_id=f"{run_id}_{symbol}_snapshot",
                skipped_reason="existing_open_position",
            )

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
        # Filter structures through zone tracker then session-level OB invalidation.
        # Zone tracker step: update mitigation (price-closed-through OBs/FVGs),
        # expire old zones, register fresh detections. Returns only ACTIVE zones.
        raw_structures = [*m15_structures, *h1_structures]
        current_bar = m15_bars[-1] if m15_bars else None
        if current_bar is not None:
            zone_changes = self._zone_tracker.scan(symbol, raw_structures, current_bar)
            logger.debug(
                "zone_tracker: symbol=%s expired=%d mitigated=%d registered=%d active=%d",
                symbol,
                zone_changes["expired"],
                zone_changes["mitigated"],
                zone_changes["registered"],
                zone_changes["active"],
            )
            zone_filtered = self._zone_tracker.get_active_structures(symbol)
        else:
            zone_filtered = raw_structures
        # Session-level OB invalidation: blocks re-entry at same price zone this session.
        structures = self._filter_session_invalidated_structures(symbol, zone_filtered, atr_m15)

        # 3b. Wider TP structure pool for TP target anchoring only
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
            bars_h4=h4_bars,
        )
        references = compute_reference_levels(m15_bars)

        # 5. Evaluate decision
        entry_price = float(tick["ask"]) if context.trend_m15.value == "BULLISH" else float(tick["bid"])
        symbol_cfg = dict(self.config)
        broker_max_lot = float(profile["max_lot"] if isinstance(profile, dict) else profile.max_lot)
        scale_factor = float(self.config.get("risk", {}).get("max_lot_scale_factor", 0.0))
        if scale_factor > 0.0:
            balance_cap = risk_state["account_balance"] * scale_factor
            effective_max_lot = min(broker_max_lot, balance_cap)
        else:
            effective_max_lot = broker_max_lot

        symbol_cfg["instrument"] = {
            "symbol": symbol,
            "digits": profile["digits"] if isinstance(profile, dict) else profile.digits,
            "point": profile["point"] if isinstance(profile, dict) else profile.point,
            "tick_size": profile["tick_size"] if isinstance(profile, dict) else profile.tick_size,
            "tick_value": profile["tick_value"] if isinstance(profile, dict) else profile.tick_value,
            "lot_step": profile["lot_step"] if isinstance(profile, dict) else profile.lot_step,
            "min_lot": profile["min_lot"] if isinstance(profile, dict) else profile.min_lot,
            "max_lot": effective_max_lot,
            "contract_size": profile["contract_size"] if isinstance(profile, dict) else profile.contract_size,
            "instrument_class": (profile["instrument_class"] if isinstance(profile, dict) else profile.instrument_class),
        }

        # Build symbol-specific risk state with per-symbol open position count
        open_for_symbol = (
            sum(1 for p in self.position_tracker.get_open_positions() if p.symbol == symbol)
            if self.position_tracker is not None
            else 0
        )
        symbol_risk_state = {**risk_state, "open_positions_symbol": open_for_symbol}

        outcome = evaluate_decision(
            structures=structures,
            context=context,
            config=symbol_cfg,
            entry_price=entry_price,
            references=references,
            atr_override=None,
            risk_state=symbol_risk_state,
            runtime_state=self.runtime_state,
            tp_structures=tp_structures,
            arming_service=self.arming_service,
            kill_switch=self.kill_switch,
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

        # 8. Live execution if EXECUTE
        live_fill: dict[str, Any] | None = None
        if outcome.final_decision == FinalDecision.EXECUTE and outcome.trade_intent is not None:
            intent = outcome.trade_intent

            # Compute absolute spread cap from config spread_max_pips and symbol point.
            # 1 pip = 10 points for standard FX (e.g. EURUSD point=0.00001, pip=0.0001).
            # The same 10x ratio holds for JPY pairs (point=0.001, pip=0.01) and most
            # commodities, so multiplying by 10 is the correct universal conversion.
            instrument_point = profile["point"] if isinstance(profile, dict) else getattr(profile, "point", 1e-5)
            spread_max_pips = float(self.config.get("execution", {}).get("spread_max_pips", 0.0))
            spread_max_price = (spread_max_pips * 10.0 * instrument_point) if spread_max_pips > 0 else None

            order_result = self.wrapper.send(
                intent=intent,
                arming_service=self.arming_service,
                kill_switch=self.kill_switch,
                runtime_state=self.runtime_state,
                decision_spread=spread,
                max_orders_per_run=self.config.get("execution", {}).get("max_orders_per_run", 1),
                kill_switch_enabled=bool(self.config.get("execution", {}).get("kill_switch_enabled", False)),
                risk_dynamic_lot_sizing=bool(self.config.get("risk", {}).get("dynamic_lot_sizing", True)),
                risk_fixed_lot_size=float(self.config.get("risk", {}).get("fixed_lot_size", intent.risk_verdict.lot_size)),
                risk_per_trade_pct=float(self.config.get("risk", {}).get("risk_per_trade_pct", 0.01)) / 100.0,
                spread_max_price=spread_max_price,
            )

            live_fill = {
                # Linkage chain: decision_id → trade_id → mt5_ticket
                "decision_id": decision_id,
                "trade_id": intent.trade_id,
                "ticket": order_result.ticket,
                # Position identity
                "run_id": run_id,
                "symbol": symbol,
                "side": intent.direction.value,
                "lot_size": intent.risk_verdict.lot_size,
                "sl": intent.exit_plan.stop_loss,
                "tp": intent.exit_plan.take_profit,
                "setup_class": intent.setup_class.value if intent.setup_class else "",
                "confidence_tier": intent.confidence_tier.value if intent.confidence_tier else "",
                # Execution details
                "timestamp": datetime.now(tz=UTC).isoformat(),
                "intended_entry": intent.entry_price,
                "actual_fill": order_result.entry_price if order_result.entry_price else intent.entry_price,
                "slippage": order_result.slippage,
                "spread_at_fill": spread,
                "order_status": order_result.status,
                "broker_retcode": order_result.broker_retcode,
                # Lifecycle state — updated by position tracker when position closes
                "status": "open" if order_result.sent else "rejected",
            }
            self.writer.write_trade(live_fill)
            self.runtime_state.record_trade(intent.trade_id)

            # Log position open event for lifecycle tracking
            logger.info(
                "position_event_check: sent=%s ticket=%s symbol=%s",
                order_result.sent,
                order_result.ticket,
                symbol,
            )
            if order_result.sent and order_result.ticket is not None:
                try:
                    self.writer.write_position_event({
                        "event": "position_open",
                        "ticket": order_result.ticket,
                        "decision_id": decision_id,
                        "trade_id": intent.trade_id,
                        "run_id": run_id,
                        "symbol": symbol,
                        "side": intent.direction.value,
                        "lot_size": intent.risk_verdict.lot_size,
                        "open_price": live_fill["actual_fill"],
                        "sl": intent.exit_plan.stop_loss,
                        "tp": intent.exit_plan.take_profit,
                        "setup_class": live_fill.get("setup_class", ""),
                        "confidence_tier": live_fill.get("confidence_tier", ""),
                        "timestamp": live_fill["timestamp"],
                    })
                    logger.info("position_event_written: ticket=%s", order_result.ticket)
                except Exception as exc:
                    logger.error("position_event_write_failed: %s", exc)
            else:
                logger.info(
                    "position_event_skipped: sent=%s ticket=%s",
                    order_result.sent,
                    order_result.ticket,
                )

            # Record the OB zone used so it is blocked from re-entry this session.
            if outcome.confluence is not None and outcome.confluence.primary_trigger is not None:
                pt = outcome.confluence.primary_trigger
                if pt.structure_type == StructureType.ORDER_BLOCK:
                    self._record_used_ob_zone(
                        symbol=intent.symbol,
                        direction=intent.direction.value,
                        price_low=pt.price_low,
                        price_high=pt.price_high,
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
                            symbol=intent.symbol,
                            bar_index=s.bar_index,
                            timeframe=s.timeframe,
                            current_bar_index=current_bar.bar_index,
                        )

            # Record in position tracker
            if self.position_tracker is not None and order_result.ticket is not None:
                self.position_tracker.record_sent_order(
                    ticket=order_result.ticket,
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
