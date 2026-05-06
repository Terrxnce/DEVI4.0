"""LiveOrderWrapper — single entry point for all live execution.

This is the ONLY file in the codebase that calls mt5.order_send.
All broker calls are gated by: arming token, kill switch, max orders,
and pre-trade rechecks. Telemetry is written for every attempt.

Gate order (fail-fast):
  1. Arming token valid?
  2. Kill switch clear?
  3. max_orders_per_run not exceeded?
  4. Pre-trade rechecks pass?
  5. mt5.order_send (broker execution)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.core.arming import ArmingService
from src.core.enums import Direction
from src.core.kill_switch import KillSwitch
from src.core.models import TradeIntent
from src.core.runtime_state import RuntimeState
from src.execution.recheck import PreTradeRecheck
from src.execution.retcode import map_retcode, should_record_failure

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveOrderResult:
    """Result of attempting a live order via the wrapper.

    `sent` is True only when the broker confirms fill (retcode 10009).
    `ticket` links the internal decision/trade to the broker order ticket.
    """

    sent: bool
    status: str
    reason: str
    decision_id: str
    symbol: str
    side: str
    lot_size: float
    entry_price: float
    stop_loss: float
    take_profit: float
    ticket: int | None = None
    broker_retcode: int | None = None
    slippage: float | None = None
    execution_time: str | None = None
    broker_present: bool | None = None
    broker_type: str | None = None
    has_order_send: bool | None = None
    order_send_invoked: bool = False
    mt5_initialized: bool | None = None
    mt5_last_error: str | None = None


class LiveOrderWrapper:
    """Skeleton wrapper for live order execution.

    Usage:
        wrapper = LiveOrderWrapper(data_source=mt5_data_source)
        result = wrapper.send(
            intent,
            arming_service=arming_service,
            kill_switch=kill_switch,
            runtime_state=runtime_state,
            decision_spread=0.0002,
            max_orders_per_run=1,
        )
        if result.sent:
            # real broker call happened (future)
        else:
            # blocked by gate or mocked
    """

    def __init__(
        self,
        *,
        data_source: Any | None = None,
        telemetry_writer: Any | None = None,
    ) -> None:
        self._data = data_source
        self._recheck = PreTradeRecheck(data_source=data_source)
        self._telemetry = telemetry_writer

    def send(
        self,
        intent: TradeIntent,
        *,
        arming_service: ArmingService,
        kill_switch: KillSwitch,
        runtime_state: RuntimeState,
        decision_spread: float,
        max_orders_per_run: int,
        risk_dynamic_lot_sizing: bool = True,
        risk_fixed_lot_size: float | None = None,
    ) -> LiveOrderResult:
        """Validate all gates and either mock-send or block."""

        # Gate 1: Arming token
        token = arming_service.get_valid_token()
        if token is None:
            return self._blocked(
                intent, "blocked_not_armed", "No valid live arming token"
            )

        if intent.symbol not in token.symbols:
            return self._blocked(
                intent,
                "blocked_symbol_not_authorized",
                f"Symbol {intent.symbol} not in armed token symbols",
            )

        # Gate 2: Kill switch
        ks_verdict = kill_switch.evaluate()
        if ks_verdict.triggered:
            return self._blocked(
                intent,
                f"blocked_kill_switch:{ks_verdict.reason}",
                f"Kill switch active: {ks_verdict.reason}",
            )

        # Gate 3: Max orders per run
        if runtime_state.orders_this_run >= max_orders_per_run:
            return self._blocked(
                intent,
                "blocked_max_orders_exceeded",
                f"Orders this run ({runtime_state.orders_this_run}) >= max ({max_orders_per_run})",
            )

        # Gate 4: Pre-trade rechecks
        recheck_verdict = self._recheck.run_all(
            intent,
            decision_spread=decision_spread,
            dynamic_lot_sizing=risk_dynamic_lot_sizing,
            fixed_lot_size=risk_fixed_lot_size,
        )
        if not recheck_verdict.passed:
            return self._blocked(
                intent,
                f"blocked_recheck:{recheck_verdict.reason}",
                f"Pre-trade recheck failed: {recheck_verdict.reason}",
            )

        # All gates passed — execution attempt consumes arming token.
        result = self._execute(intent, token, kill_switch)
        arming_service.consume_token(str(token.token_id), reason="execution_attempt")
        return result

    def _execute(
        self,
        intent: TradeIntent,
        token: Any,
        kill_switch: KillSwitch,
    ) -> LiveOrderResult:
        """Build MT5 request, call order_send, map retcode, log telemetry."""
        now = datetime.now(tz=UTC)
        exec_time = now.isoformat()

        broker_present = False
        broker_type: str | None = None
        has_order_send = False
        order_send_invoked = False
        mt5_initialized = bool(getattr(self._data, "initialized", False))
        mt5_last_error: str | None = None

        if self._data is None or not hasattr(self._data, "mt5_client"):
            return self._blocked(
                intent,
                "blocked_no_mt5_client",
                "Data source missing or has no mt5_client attribute",
                broker_present=broker_present,
                broker_type=broker_type,
                has_order_send=has_order_send,
                order_send_invoked=order_send_invoked,
                mt5_initialized=mt5_initialized,
                mt5_last_error=mt5_last_error,
            )

        mt5 = self._data.mt5_client
        broker_present = mt5 is not None
        broker_type = None if mt5 is None else type(mt5).__name__
        if mt5 is None:
            return self._blocked(
                intent,
                "blocked_no_mt5_client",
                "Data source mt5_client is None",
                broker_present=broker_present,
                broker_type=broker_type,
                has_order_send=has_order_send,
                order_send_invoked=order_send_invoked,
                mt5_initialized=mt5_initialized,
                mt5_last_error=mt5_last_error,
            )

        has_order_send = callable(getattr(mt5, "order_send", None))
        if not has_order_send:
            return self._blocked(
                intent,
                "blocked_no_order_send_method",
                "Broker adapter has no callable order_send",
                broker_present=broker_present,
                broker_type=broker_type,
                has_order_send=has_order_send,
                order_send_invoked=order_send_invoked,
                mt5_initialized=mt5_initialized,
                mt5_last_error=mt5_last_error,
            )

        if callable(getattr(mt5, "last_error", None)):
            try:
                mt5_last_error = str(mt5.last_error())
            except Exception:
                mt5_last_error = "last_error_unavailable"

        order_type = (
            mt5.ORDER_TYPE_BUY
            if intent.direction == Direction.BULLISH
            else mt5.ORDER_TYPE_SELL
        )

        # Ensure symbol is selected before sending
        if callable(getattr(mt5, "symbol_select", None)):
            try:
                mt5.symbol_select(intent.symbol, True)
            except Exception:
                pass

        type_filling = getattr(mt5, "ORDER_FILLING_IOC", 1)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": intent.symbol,
            "volume": float(intent.risk_verdict.lot_size),
            "type": order_type,
            "price": float(intent.entry_price),
            "sl": float(intent.exit_plan.stop_loss),
            "tp": float(intent.exit_plan.take_profit),
            "deviation": 10,
            "magic": int(str(token.token_id)[:8], 16) & 0x7FFFFFFF,
            "comment": f"devi:{token.run_id}:{intent.trade_id}"[:27],
            "type_filling": type_filling,
        }

        try:
            order_send_invoked = True
            result = mt5.order_send(request)
        except Exception as exc:
            logger.error("mt5.order_send raised exception: %s", exc)
            return self._blocked(
                intent,
                "blocked_broker_exception",
                f"Broker exception: {exc}",
                broker_present=broker_present,
                broker_type=broker_type,
                has_order_send=has_order_send,
                order_send_invoked=order_send_invoked,
                mt5_initialized=mt5_initialized,
                mt5_last_error=mt5_last_error,
            )

        if result is None:
            if callable(getattr(mt5, "last_error", None)):
                try:
                    mt5_last_error = str(mt5.last_error())
                except Exception:
                    mt5_last_error = "last_error_unavailable"
            return self._blocked(
                intent,
                "blocked_broker_none",
                f"mt5.order_send returned None (last_error={mt5_last_error})",
                broker_present=broker_present,
                broker_type=broker_type,
                has_order_send=has_order_send,
                order_send_invoked=order_send_invoked,
                mt5_initialized=mt5_initialized,
                mt5_last_error=mt5_last_error,
            )

        retcode = int(getattr(result, "retcode", -1))
        mapping = map_retcode(retcode)

        if should_record_failure(retcode):
            kill_switch.record_failure()

        ticket = getattr(result, "order", None)
        actual_price = getattr(result, "price", intent.entry_price)
        slippage = float(actual_price) - float(intent.entry_price)

        order_result = LiveOrderResult(
            sent=mapping.status.value == "FILLED",
            status=mapping.status.value,
            reason=f"{mapping.description} (retcode={retcode})",
            decision_id=intent.trade_id,
            symbol=intent.symbol,
            side=intent.direction.value,
            lot_size=intent.risk_verdict.lot_size,
            entry_price=intent.entry_price,
            stop_loss=intent.exit_plan.stop_loss,
            take_profit=intent.exit_plan.take_profit,
            ticket=ticket,
            broker_retcode=retcode,
            slippage=round(slippage, 5),
            execution_time=exec_time,
            broker_present=broker_present,
            broker_type=broker_type,
            has_order_send=has_order_send,
            order_send_invoked=order_send_invoked,
            mt5_initialized=mt5_initialized,
            mt5_last_error=mt5_last_error,
        )

        self._log_telemetry(intent, token, order_result, request)
        return order_result

    def _log_telemetry(
        self,
        intent: TradeIntent,
        token: Any,
        result: LiveOrderResult,
        request: dict,
    ) -> None:
        if self._telemetry is None:
            return
        payload = {
            "timestamp": result.execution_time,
            "run_id": token.run_id,
            "token_id": str(token.token_id),
            "decision_id": intent.trade_id,
            "symbol": intent.symbol,
            "side": result.side,
            "lot_size": result.lot_size,
            "entry_price": result.entry_price,
            "stop_loss": result.stop_loss,
            "take_profit": result.take_profit,
            "sent": result.sent,
            "status": result.status,
            "reason": result.reason,
            "ticket": result.ticket,
            "broker_retcode": result.broker_retcode,
            "slippage": result.slippage,
            "request": request,
        }
        try:
            self._telemetry.write_live_order(payload)
        except Exception as exc:
            logger.warning("Live order telemetry write failed: %s", exc)

    def _blocked(
        self,
        intent: TradeIntent,
        status: str,
        reason: str,
        broker_present: bool | None = None,
        broker_type: str | None = None,
        has_order_send: bool | None = None,
        order_send_invoked: bool = False,
        mt5_initialized: bool | None = None,
        mt5_last_error: str | None = None,
    ) -> LiveOrderResult:
        return LiveOrderResult(
            sent=False,
            status=status,
            reason=reason,
            decision_id=intent.trade_id,
            symbol=intent.symbol,
            side=intent.direction.value,
            lot_size=intent.risk_verdict.lot_size,
            entry_price=intent.entry_price,
            stop_loss=intent.exit_plan.stop_loss,
            take_profit=intent.exit_plan.take_profit,
            broker_present=broker_present,
            broker_type=broker_type,
            has_order_send=has_order_send,
            order_send_invoked=order_send_invoked,
            mt5_initialized=mt5_initialized,
            mt5_last_error=mt5_last_error,
        )
