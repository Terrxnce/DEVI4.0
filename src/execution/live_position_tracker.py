"""Live position tracker that queries MT5 for real positions.

Tracks open positions by ticket, monitors SL/TP hits, and records
external closures (manual, broker stop, etc.).

When sync_positions detects a position that has closed, it queries
MT5 deal history to capture close price, close reason, and PnL.
Newly closed positions are accessible via get_newly_closed().

State persistence: if state_path is provided, OPEN positions are saved
to a JSON file after every sync_positions() call and loaded on init.
This allows cross-run close detection even when the bot restarts between
the order being placed and the position being closed.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# MT5 deal reason constants (integer values from MetaTrader5 docs).
# Used without importing MetaTrader5 so the tracker stays testable.
_DEAL_REASON_SL = 4
_DEAL_REASON_TP = 5
_DEAL_REASON_SO = 6   # stop out
_DEAL_REASON_EXPERT = 3  # closed by EA / bot
_DEAL_ENTRY_OUT = 1   # deal closes a position


def _optional_float(value: object) -> float | None:
    """Return float(value) or None if value is None / not convertible."""
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _map_deal_reason(reason: int) -> str:
    """Map MT5 DEAL_REASON_* integer to a readable close reason string."""
    return {
        _DEAL_REASON_SL: "sl_hit",
        _DEAL_REASON_TP: "tp_hit",
        _DEAL_REASON_SO: "stop_out",
        _DEAL_REASON_EXPERT: "bot_closed",
    }.get(reason, "manually_closed")


@dataclass
class LivePosition:
    """A single live position synced from MT5."""

    ticket: int
    trade_id: str
    decision_id: str
    symbol: str
    side: str  # "BUY" | "SELL"
    lot_size: float
    open_price: float
    current_price: float
    sl: float
    tp: float
    profit: float
    swap: float
    status: str = "OPEN"  # "OPEN" | "CLOSED"
    close_price: float | None = None
    close_time: str | None = None
    close_reason: str | None = None
    close_pnl: float | None = None
    open_time: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())

    # Trail / partial-close lifecycle — managed by TrailingManager, not the tracker.
    # Preserved across MT5 re-syncs so state survives within a run.
    partial_closed: bool = False
    partial_close_volume: float = 0.0
    partial_close_price: float | None = None
    breakeven_set: bool = False
    trail_active: bool = False
    trail_distance: float = 0.0      # in price units, set when breakeven is confirmed
    best_price: float | None = None  # BUY: highest seen; SELL: lowest seen


class LivePositionTracker:
    """Tracks live positions by querying MT5 positions_total / positions_get.

    Does NOT place or close orders. Purely observational.

    When sync_positions detects a previously OPEN position that is no longer
    present in MT5, it queries history_deals_get to capture the close price,
    close reason (sl_hit / tp_hit / manually_closed / etc.), and realised PnL.
    Newly closed positions from the last sync are accessible via get_newly_closed().
    """

    def __init__(
        self,
        mt5_client: Any,
        *,
        state_path: Path | str | None = None,
    ) -> None:
        self._mt5 = mt5_client
        self._positions: dict[int, LivePosition] = {}
        self._newly_closed: list[LivePosition] = []
        self._state_path = Path(state_path) if state_path is not None else None
        if self._state_path is not None:
            self._load_state()

    def sync_positions(self) -> list[LivePosition]:
        """Query MT5 for current open positions and sync internal state.

        Returns list of currently open positions.
        """
        if self._mt5 is None:
            return []

        open_list: list[LivePosition] = []

        # Check if positions_get is available
        if not callable(getattr(self._mt5, "positions_get", None)):
            return []

        try:
            raw_positions = self._mt5.positions_get()
        except Exception:
            raw_positions = None

        if raw_positions is None:
            raw_positions = []

        current_tickets = set()
        for pos in raw_positions:
            ticket = int(getattr(pos, "ticket", 0))
            if ticket == 0:
                continue
            current_tickets.add(ticket)

            lp = LivePosition(
                ticket=ticket,
                trade_id=getattr(pos, "comment", "") or f"ticket_{ticket}",
                decision_id="",
                symbol=getattr(pos, "symbol", ""),
                side="BUY" if getattr(pos, "type", 0) == 0 else "SELL",
                lot_size=float(getattr(pos, "volume", 0.0)),
                open_price=float(getattr(pos, "price_open", 0.0)),
                current_price=float(getattr(pos, "price_current", 0.0)),
                sl=float(getattr(pos, "sl", 0.0)),
                tp=float(getattr(pos, "tp", 0.0)),
                profit=float(getattr(pos, "profit", 0.0)),
                swap=float(getattr(pos, "swap", 0.0)),
            )
            # Preserve trade identity and trail state from the previous sync.
            # trade_id / decision_id must come from record_sent_order(), NOT the
            # MT5 comment field — the comment is truncated to 27 chars and the
            # trade_id portion is fully lost behind the run_id prefix.
            # Losing trade_id here means write_trade_close() writes an orphan
            # close record that can never be matched to the original open row.
            if ticket in self._positions:
                prev = self._positions[ticket]
                lp.trade_id = prev.trade_id
                lp.decision_id = prev.decision_id
                lp.partial_closed = prev.partial_closed
                lp.partial_close_volume = prev.partial_close_volume
                lp.partial_close_price = prev.partial_close_price
                lp.breakeven_set = prev.breakeven_set
                lp.trail_active = prev.trail_active
                lp.trail_distance = prev.trail_distance
                lp.best_price = prev.best_price
                lp.open_time = prev.open_time

            self._positions[ticket] = lp
            open_list.append(lp)

        # Detect positions that were OPEN but are no longer in MT5
        self._newly_closed = []
        for ticket, pos in list(self._positions.items()):
            if ticket not in current_tickets and pos.status == "OPEN":
                pos.status = "CLOSED"
                pos.close_time = datetime.now(tz=UTC).isoformat()
                pos.close_reason = "external_close"
                # Query MT5 deal history for close details
                self._enrich_close_from_history(pos)
                self._newly_closed.append(pos)

        if self._state_path is not None:
            self._save_state()

        return open_list

    def _load_state(self) -> None:
        """Load OPEN positions from the state JSON file.

        Silently skips if the file does not exist or is corrupt.
        Only positions with status 'OPEN' are loaded — closed positions
        recorded in previous sessions are not carried forward.
        """
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            raw = self._state_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, list):
                logger.warning("position_state: expected list, got %s — skipping", type(data).__name__)
                return
            loaded = 0
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                if entry.get("status") != "OPEN":
                    continue
                try:
                    ticket = int(entry["ticket"])
                    lp = LivePosition(
                        ticket=ticket,
                        trade_id=entry.get("trade_id", f"ticket_{ticket}"),
                        decision_id=entry.get("decision_id", ""),
                        symbol=entry["symbol"],
                        side=entry["side"],
                        lot_size=float(entry["lot_size"]),
                        open_price=float(entry["open_price"]),
                        current_price=float(entry.get("current_price", entry["open_price"])),
                        sl=float(entry["sl"]),
                        tp=float(entry["tp"]),
                        profit=float(entry.get("profit", 0.0)),
                        swap=float(entry.get("swap", 0.0)),
                        status="OPEN",
                        open_time=entry.get("open_time", datetime.now(tz=UTC).isoformat()),
                        partial_closed=bool(entry.get("partial_closed", False)),
                        partial_close_volume=float(entry.get("partial_close_volume", 0.0)),
                        partial_close_price=_optional_float(entry.get("partial_close_price")),
                        breakeven_set=bool(entry.get("breakeven_set", False)),
                        trail_active=bool(entry.get("trail_active", False)),
                        trail_distance=float(entry.get("trail_distance", 0.0)),
                        best_price=_optional_float(entry.get("best_price")),
                    )
                    self._positions[ticket] = lp
                    loaded += 1
                except (KeyError, ValueError, TypeError) as exc:
                    logger.warning("position_state: skipping malformed entry: %s", exc)
            logger.info("position_state: loaded %d open position(s) from %s", loaded, self._state_path)
        except json.JSONDecodeError as exc:
            logger.warning("position_state: corrupt state file (%s) — starting fresh", exc)
        except OSError as exc:
            logger.warning("position_state: could not read state file (%s) — starting fresh", exc)

    def _save_state(self) -> None:
        """Persist all currently OPEN positions to the state JSON file.

        Only OPEN positions are saved. Closed positions are dropped from
        state so the file stays small and stale tickets don't accumulate.
        """
        if self._state_path is None:
            return
        open_positions = [p for p in self._positions.values() if p.status == "OPEN"]
        data = [
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
                "status": p.status,
                "open_time": p.open_time,
                "partial_closed": p.partial_closed,
                "partial_close_volume": p.partial_close_volume,
                "partial_close_price": p.partial_close_price,
                "breakeven_set": p.breakeven_set,
                "trail_active": p.trail_active,
                "trail_distance": p.trail_distance,
                "best_price": p.best_price,
            }
            for p in open_positions
        ]
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            logger.debug("position_state: saved %d open position(s) to %s", len(data), self._state_path)
        except OSError as exc:
            logger.error("position_state: failed to save state file: %s", exc)

    def _enrich_close_from_history(self, pos: LivePosition) -> None:
        """Query MT5 deal history to fill in close_price, close_reason, close_pnl.

        Silently skips if history_deals_get is unavailable or returns nothing.
        """
        if self._mt5 is None:
            return
        if not callable(getattr(self._mt5, "history_deals_get", None)):
            return
        try:
            deals = self._mt5.history_deals_get(position=pos.ticket)
        except Exception:
            return
        if not deals:
            return
        # Find the OUT deal (the one that closed the position)
        out_deals = [d for d in deals if int(getattr(d, "entry", -1)) == _DEAL_ENTRY_OUT]
        if not out_deals:
            return
        deal = out_deals[-1]  # last OUT deal
        raw_price = getattr(deal, "price", None)
        raw_pnl = getattr(deal, "profit", None)
        raw_reason = getattr(deal, "reason", None)
        raw_time = getattr(deal, "time", None)
        if raw_price is not None:
            pos.close_price = float(raw_price)
        if raw_pnl is not None:
            pos.close_pnl = float(raw_pnl)
        if raw_reason is not None:
            pos.close_reason = _map_deal_reason(int(raw_reason))
        if raw_time is not None:
            # MT5 returns time as a Unix timestamp (int)
            try:
                pos.close_time = datetime.fromtimestamp(int(raw_time), tz=UTC).isoformat()
            except Exception:
                pass

    def get_newly_closed(self) -> list[LivePosition]:
        """Return positions that closed during the most recent sync_positions call.

        Cleared on the next call to sync_positions.
        """
        return list(self._newly_closed)

    def get_open_positions(self) -> list[LivePosition]:
        return [p for p in self._positions.values() if p.status == "OPEN"]

    def get_closed_positions(self) -> list[LivePosition]:
        return [p for p in self._positions.values() if p.status == "CLOSED"]

    def get_position(self, ticket: int) -> LivePosition | None:
        return self._positions.get(ticket)

    def has_open_position(self, symbol: str) -> bool:
        return any(
            p.symbol == symbol and p.status == "OPEN"
            for p in self._positions.values()
        )

    def record_sent_order(
        self,
        *,
        ticket: int,
        trade_id: str,
        decision_id: str,
        symbol: str,
        side: str,
        lot_size: float,
        open_price: float,
        sl: float,
        tp: float,
    ) -> LivePosition:
        """Record a position we just sent (before MT5 positions_get picks it up)."""
        lp = LivePosition(
            ticket=ticket,
            trade_id=trade_id,
            decision_id=decision_id,
            symbol=symbol,
            side=side,
            lot_size=lot_size,
            open_price=open_price,
            current_price=open_price,
            sl=sl,
            tp=tp,
            profit=0.0,
            swap=0.0,
        )
        self._positions[ticket] = lp
        return lp
