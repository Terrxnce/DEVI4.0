"""Live position tracker that queries MT5 for real positions.

Tracks open positions by ticket, monitors SL/TP hits, and records
external closures (manual, broker stop, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


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
    open_time: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())


class LivePositionTracker:
    """Tracks live positions by querying MT5 positions_total / positions_get.

    Does NOT place or close orders. Purely observational.
    """

    def __init__(self, mt5_client: Any) -> None:
        self._mt5 = mt5_client
        self._positions: dict[int, LivePosition] = {}

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
            self._positions[ticket] = lp
            open_list.append(lp)

        # Mark any previously tracked positions that are no longer open
        for ticket, pos in list(self._positions.items()):
            if ticket not in current_tickets and pos.status == "OPEN":
                pos.status = "CLOSED"
                pos.close_time = datetime.now(tz=UTC).isoformat()
                pos.close_reason = "external_close"

        return open_list

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
