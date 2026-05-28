"""TrailingManager — position lifecycle management after entry.

Handles three sequential phases for each live position:

  Phase 1: Partial close at 1R
    When price moves 1R in favour, close 50% of the position at market.
    If the position lot is below 2 * min_lot, the partial close is skipped
    and we proceed directly to Phase 2.

  Phase 2: Breakeven
    After the partial close confirms (retcode 10009), move SL to open_price
    (+ 1 pip buffer on the safe side). If partial close was skipped, still
    move SL to breakeven once 1R is hit.

  Phase 3: Trail
    After breakeven is set, trail the SL at trail_distance_r * initial_sl_distance
    behind the best price seen. SL is updated each time price beats the previous
    best price. Trail runs until TP or trailing SL is hit.

MT5 calls made here:
  - TRADE_ACTION_DEAL   (partial close — market order, opposite side, position=ticket)
  - TRADE_ACTION_SLTP   (modify SL/TP on existing position)

Both calls log full request/response and retcode. Neither is gated by the
entry-order supervisor (these are exits/modifications, not new positions).
A failed partial close blocks the breakeven SL move — we never move SL to
breakeven unless we have confirmed the position is smaller (or have decided
to skip the partial close).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.execution.live_position_tracker import LivePosition

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MT5 integer constants — defined locally so the module stays importable
# without the MetaTrader5 package installed (keeps tests clean).
# Values match the official MetaTrader5 Python library docs.
# ---------------------------------------------------------------------------
_TRADE_ACTION_DEAL = 1   # market execution
_TRADE_ACTION_SLTP = 6   # modify SL/TP on an open position
_ORDER_TYPE_BUY = 0
_ORDER_TYPE_SELL = 1
_ORDER_FILLING_IOC = 1
_RETCODE_DONE = 10009    # successful execution


# ---------------------------------------------------------------------------
# TrailEvent
# ---------------------------------------------------------------------------


@dataclass
class TrailEvent:
    """A single action taken by TrailingManager on a position."""

    ticket: int
    symbol: str
    event_type: str  # see _EVENT_TYPES below
    timestamp: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())
    detail: dict = field(default_factory=dict)

# Valid event_type values:
# "partial_close_sent"     — partial close confirmed (retcode 10009)
# "partial_close_failed"   — partial close attempted, retcode != 10009
# "partial_close_skipped"  — lot too small, went straight to breakeven
# "breakeven_set"          — SL moved to breakeven (retcode 10009)
# "breakeven_failed"       — SLTP sent, retcode != 10009
# "trail_moved"            — trailing SL updated (retcode 10009)
# "trail_failed"           — trailing SL update retcode != 10009


# ---------------------------------------------------------------------------
# TrailingManager
# ---------------------------------------------------------------------------


class TrailingManager:
    """Processes open positions and sends position-management orders.

    Call process_positions(open_positions) once per scan loop cycle,
    AFTER sync_positions() has refreshed current_price on each position.

    Args:
        mt5_client:         The MT5 adapter (same one used by LiveSession).
        trail_distance_r:   Trail distance as a multiple of initial SL distance.
                            0.5 = trail at 0.5R behind best price (default).
        partial_close_ratio: Fraction of position to close at 1R (default 0.5 = 50%).
        min_lot:            Minimum lot size accepted by broker (default 0.01).
        lot_step:           Lot size increment (default 0.01).
        breakeven_buffer:   Small buffer added to breakeven SL so spread does not
                            immediately stop us out on a scratch (default 0.0001
                            = 1 pip for 5-decimal pairs).
        magic_number:       Magic number for modification orders (should match entries).
        deviation:          Slippage tolerance in points for market orders (default 10).
    """

    def __init__(
        self,
        mt5_client: Any,
        *,
        trail_distance_r: float = 0.5,
        partial_close_ratio: float = 0.5,
        min_lot: float = 0.01,
        lot_step: float = 0.01,
        breakeven_buffer: float = 0.0001,
        magic_number: int = 234000,
        deviation: int = 10,
    ) -> None:
        self._mt5 = mt5_client
        self._trail_distance_r = trail_distance_r
        self._partial_close_ratio = partial_close_ratio
        self._min_lot = min_lot
        self._lot_step = lot_step
        self._breakeven_buffer = breakeven_buffer
        self._magic_number = magic_number
        self._deviation = deviation

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def process_positions(self, positions: list[LivePosition]) -> list[TrailEvent]:
        """Evaluate each OPEN position and take any required management actions.

        Returns a list of TrailEvents (may be empty if no action needed).
        """
        events: list[TrailEvent] = []
        for pos in positions:
            if pos.status != "OPEN":
                continue
            try:
                events.extend(self._process_one(pos))
            except Exception as exc:
                logger.error(
                    "trailing_manager: unexpected error on ticket=%s: %s",
                    pos.ticket,
                    exc,
                )
        return events

    # ------------------------------------------------------------------
    # Per-position logic
    # ------------------------------------------------------------------

    def _process_one(self, pos: LivePosition) -> list[TrailEvent]:
        events: list[TrailEvent] = []

        if not pos.breakeven_set:
            # Phase 1 + 2: check for 1R hit
            if self._one_r_hit(pos):
                logger.info(
                    "trailing_manager: 1R hit ticket=%s symbol=%s current=%.5f",
                    pos.ticket,
                    pos.symbol,
                    pos.current_price,
                )
                partial_vol = self._compute_partial_volume(pos.lot_size)

                if partial_vol is not None:
                    # Attempt partial close first
                    pc_event = self._send_partial_close(pos, partial_vol)
                    events.append(pc_event)
                    if pc_event.event_type != "partial_close_sent":
                        # Partial close failed — do NOT move SL. Abort.
                        logger.warning(
                            "trailing_manager: partial close failed ticket=%s — "
                            "skipping breakeven to avoid unprotected full exposure",
                            pos.ticket,
                        )
                        return events
                else:
                    # Position too small for partial close — skip straight to breakeven
                    events.append(TrailEvent(
                        ticket=pos.ticket,
                        symbol=pos.symbol,
                        event_type="partial_close_skipped",
                        detail={"lot_size": pos.lot_size, "min_lot": self._min_lot},
                    ))

                # Phase 2: move SL to breakeven
                be_event = self._set_breakeven(pos)
                events.append(be_event)

                if be_event.event_type == "breakeven_set":
                    initial_sl_distance = abs(pos.open_price - pos.sl)
                    pos.breakeven_set = True
                    pos.trail_active = True
                    pos.trail_distance = initial_sl_distance * self._trail_distance_r
                    pos.best_price = pos.current_price
                    if partial_vol is not None:
                        pos.partial_closed = True
                        pos.partial_close_volume = partial_vol
                        pos.partial_close_price = pos.current_price
                else:
                    logger.warning(
                        "trailing_manager: breakeven failed ticket=%s — "
                        "trail will retry on next cycle",
                        pos.ticket,
                    )

        elif pos.trail_active:
            # Phase 3: update trail if price has improved
            trail_events = self._update_trail(pos)
            events.extend(trail_events)

        return events

    # ------------------------------------------------------------------
    # 1R detection
    # ------------------------------------------------------------------

    def _one_r_hit(self, pos: LivePosition) -> bool:
        """Return True when price has moved at least 1R in the trade's favour."""
        sl_distance = abs(pos.open_price - pos.sl)
        if sl_distance == 0:
            return False
        if pos.side == "BUY":
            return pos.current_price >= pos.open_price + sl_distance
        else:
            return pos.current_price <= pos.open_price - sl_distance

    # ------------------------------------------------------------------
    # Lot computation
    # ------------------------------------------------------------------

    def _compute_partial_volume(self, lot_size: float) -> float | None:
        """Return the partial close volume or None if below min_lot.

        Rounds DOWN to the nearest lot_step so we never exceed the
        position size. Returns None if the resulting volume < min_lot.
        """
        raw = lot_size * self._partial_close_ratio
        steps = math.floor(raw / self._lot_step)
        volume = round(steps * self._lot_step, 8)
        if volume < self._min_lot:
            return None
        return volume

    # ------------------------------------------------------------------
    # MT5 operations
    # ------------------------------------------------------------------

    def _send_partial_close(self, pos: LivePosition, volume: float) -> TrailEvent:
        """Send a market order to partially close the position.

        Uses TRADE_ACTION_DEAL with the opposite side and position=ticket.
        """
        if self._mt5 is None or not callable(getattr(self._mt5, "order_send", None)):
            return TrailEvent(
                ticket=pos.ticket,
                symbol=pos.symbol,
                event_type="partial_close_failed",
                detail={"reason": "no_order_send", "volume": volume},
            )

        close_type = (
            _ORDER_TYPE_SELL if pos.side == "BUY" else _ORDER_TYPE_BUY
        )
        type_filling = getattr(self._mt5, "ORDER_FILLING_IOC", _ORDER_FILLING_IOC)

        request = {
            "action": getattr(self._mt5, "TRADE_ACTION_DEAL", _TRADE_ACTION_DEAL),
            "symbol": pos.symbol,
            "volume": volume,
            "type": close_type,
            "position": pos.ticket,
            "price": pos.current_price,
            "deviation": self._deviation,
            "magic": self._magic_number,
            "comment": f"devi:partial:{pos.ticket}"[:27],
            "type_filling": type_filling,
        }

        logger.info(
            "trailing_manager: partial_close ticket=%s symbol=%s volume=%.2f price=%.5f",
            pos.ticket, pos.symbol, volume, pos.current_price,
        )

        retcode, result_detail = self._order_send(request)

        if retcode == _RETCODE_DONE:
            logger.info("trailing_manager: partial_close confirmed ticket=%s", pos.ticket)
            return TrailEvent(
                ticket=pos.ticket,
                symbol=pos.symbol,
                event_type="partial_close_sent",
                detail={"volume": volume, "price": pos.current_price, "retcode": retcode, **result_detail},
            )
        else:
            logger.warning(
                "trailing_manager: partial_close failed ticket=%s retcode=%s",
                pos.ticket, retcode,
            )
            return TrailEvent(
                ticket=pos.ticket,
                symbol=pos.symbol,
                event_type="partial_close_failed",
                detail={"volume": volume, "retcode": retcode, **result_detail},
            )

    def _set_breakeven(self, pos: LivePosition) -> TrailEvent:
        """Send TRADE_ACTION_SLTP to move SL to open_price ± breakeven_buffer."""
        if self._mt5 is None or not callable(getattr(self._mt5, "order_send", None)):
            return TrailEvent(
                ticket=pos.ticket,
                symbol=pos.symbol,
                event_type="breakeven_failed",
                detail={"reason": "no_order_send"},
            )

        # Buffer direction: for BUY, add buffer BELOW open_price (slightly below
        # breakeven so spread doesn't stop us out on a scratch); for SELL, add above.
        if pos.side == "BUY":
            new_sl = round(pos.open_price - self._breakeven_buffer, 5)
        else:
            new_sl = round(pos.open_price + self._breakeven_buffer, 5)

        request = {
            "action": getattr(self._mt5, "TRADE_ACTION_SLTP", _TRADE_ACTION_SLTP),
            "symbol": pos.symbol,
            "position": pos.ticket,
            "sl": new_sl,
            "tp": pos.tp,
        }

        logger.info(
            "trailing_manager: set_breakeven ticket=%s new_sl=%.5f",
            pos.ticket, new_sl,
        )

        retcode, result_detail = self._order_send(request)

        if retcode == _RETCODE_DONE:
            logger.info("trailing_manager: breakeven_set ticket=%s sl=%.5f", pos.ticket, new_sl)
            return TrailEvent(
                ticket=pos.ticket,
                symbol=pos.symbol,
                event_type="breakeven_set",
                detail={"new_sl": new_sl, "retcode": retcode, **result_detail},
            )
        else:
            logger.warning(
                "trailing_manager: breakeven failed ticket=%s retcode=%s",
                pos.ticket, retcode,
            )
            return TrailEvent(
                ticket=pos.ticket,
                symbol=pos.symbol,
                event_type="breakeven_failed",
                detail={"new_sl": new_sl, "retcode": retcode, **result_detail},
            )

    def _update_trail(self, pos: LivePosition) -> list[TrailEvent]:
        """Move trail SL if price has improved beyond the last best price."""
        if pos.best_price is None or pos.trail_distance == 0:
            return []

        if pos.side == "BUY":
            if pos.current_price <= pos.best_price:
                return []  # no improvement
            pos.best_price = pos.current_price
            new_sl = round(pos.best_price - pos.trail_distance, 5)
            if new_sl <= pos.sl:
                return []  # new SL not better than current

        else:  # SELL
            if pos.current_price >= pos.best_price:
                return []
            pos.best_price = pos.current_price
            new_sl = round(pos.best_price + pos.trail_distance, 5)
            if new_sl >= pos.sl:
                return []

        # Send SLTP with new trailing SL
        request = {
            "action": getattr(self._mt5, "TRADE_ACTION_SLTP", _TRADE_ACTION_SLTP),
            "symbol": pos.symbol,
            "position": pos.ticket,
            "sl": new_sl,
            "tp": pos.tp,
        }

        logger.info(
            "trailing_manager: trail_move ticket=%s new_sl=%.5f best_price=%.5f",
            pos.ticket, new_sl, pos.best_price,
        )

        retcode, result_detail = self._order_send(request)

        if retcode == _RETCODE_DONE:
            pos.sl = new_sl  # update local state; MT5 sync will confirm next cycle
            logger.info("trailing_manager: trail_moved ticket=%s sl=%.5f", pos.ticket, new_sl)
            return [TrailEvent(
                ticket=pos.ticket,
                symbol=pos.symbol,
                event_type="trail_moved",
                detail={"new_sl": new_sl, "best_price": pos.best_price, "retcode": retcode, **result_detail},
            )]
        else:
            logger.warning(
                "trailing_manager: trail_move failed ticket=%s retcode=%s",
                pos.ticket, retcode,
            )
            return [TrailEvent(
                ticket=pos.ticket,
                symbol=pos.symbol,
                event_type="trail_failed",
                detail={"new_sl": new_sl, "retcode": retcode, **result_detail},
            )]

    # ------------------------------------------------------------------
    # MT5 send helper
    # ------------------------------------------------------------------

    def _order_send(self, request: dict) -> tuple[int, dict]:
        """Call mt5.order_send and return (retcode, detail_dict).

        Returns retcode=-1 on exception or None result.
        """
        try:
            result = self._mt5.order_send(request)
        except Exception as exc:
            logger.error("trailing_manager: order_send raised: %s", exc)
            return -1, {"exception": str(exc)}

        if result is None:
            last_error = None
            if callable(getattr(self._mt5, "last_error", None)):
                try:
                    last_error = str(self._mt5.last_error())
                except Exception:
                    pass
            return -1, {"result": None, "last_error": last_error}

        retcode = int(getattr(result, "retcode", -1))
        detail = {
            "comment": getattr(result, "comment", None),
            "order": getattr(result, "order", None),
            "request_id": getattr(result, "request_id", None),
        }
        return retcode, detail
