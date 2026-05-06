"""Minimal paper position tracker.

Tracks open/closed paper positions with basic PnL fields.
No trailing, partials, or broker sync.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class PaperPosition:
    """A single paper position record."""

    trade_id: str
    decision_id: str
    ticket: int
    symbol: str
    side: str
    open_price: float
    close_price: float | None = None
    lot_size: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    status: str = "OPEN"  # "OPEN" | "CLOSED"
    open_time: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())
    close_time: str | None = None
    close_reason: str | None = None
    realized_pnl: float | None = None
    unrealized_pnl: float | None = None


class PaperPositionTracker:
    """In-memory tracker for paper positions. No persistence."""

    def __init__(self) -> None:
        self._positions: dict[str, PaperPosition] = {}

    def open_position(self, *, fill: Any) -> PaperPosition:
        """Create a new open position from a PaperFillResult."""
        pos = PaperPosition(
            trade_id=fill.trade_id,
            decision_id=fill.decision_id,
            ticket=fill.ticket,
            symbol=fill.symbol,
            side=fill.side,
            open_price=fill.actual_fill,
            lot_size=0.0,  # filled externally if available
            sl=fill.planned_sl,
            tp=fill.planned_tp,
            status="OPEN",
        )
        self._positions[fill.trade_id] = pos
        return pos

    def update_lot_size(self, trade_id: str, lot_size: float) -> None:
        """Update lot size from risk sizing (called after fill creation)."""
        if trade_id in self._positions:
            self._positions[trade_id].lot_size = lot_size

    def close_position(
        self,
        trade_id: str,
        close_price: float,
        reason: str,
    ) -> PaperPosition | None:
        """Close an open position. Computes realized PnL."""
        pos = self._positions.get(trade_id)
        if pos is None or pos.status != "OPEN":
            return None

        pos.close_price = close_price
        pos.close_time = datetime.now(tz=UTC).isoformat()
        pos.close_reason = reason
        pos.status = "CLOSED"

        # Minimal PnL: points * lot_size * contract_size
        # Caller can compute contract_size externally
        points = (
            (close_price - pos.open_price)
            if pos.side == "BUY"
            else (pos.open_price - close_price)
        )
        # Store raw points; realized_pnl computed externally with contract size
        pos.realized_pnl = points

        return pos

    def compute_unrealized_pnl(self, trade_id: str, current_price: float) -> float | None:
        """Compute unrealized PnL for an open position at current price."""
        pos = self._positions.get(trade_id)
        if pos is None or pos.status != "OPEN":
            return None

        points = (
            (current_price - pos.open_price)
            if pos.side == "BUY"
            else (pos.open_price - current_price)
        )
        pos.unrealized_pnl = points
        return points

    def get_open_positions(self) -> list[PaperPosition]:
        return [p for p in self._positions.values() if p.status == "OPEN"]

    def get_closed_positions(self) -> list[PaperPosition]:
        return [p for p in self._positions.values() if p.status == "CLOSED"]

    def get_all_positions(self) -> list[PaperPosition]:
        return list(self._positions.values())

    def get_position(self, trade_id: str) -> PaperPosition | None:
        return self._positions.get(trade_id)
