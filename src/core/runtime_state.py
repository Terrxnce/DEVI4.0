"""Per-run runtime state tracking.

In-memory only. Reset on each new run. Not persisted across process restarts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class RuntimeState:
    """Tracks decisions, trades, and orders within a single run."""

    run_id: str = ""
    start_time: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    orders_this_run: int = 0
    decisions_this_run: list[str] = field(default_factory=list)
    trades_this_run: list[str] = field(default_factory=list)

    def record_decision(self, decision_id: str) -> None:
        self.decisions_this_run.append(decision_id)

    def record_trade(self, trade_id: str) -> None:
        self.orders_this_run += 1
        self.trades_this_run.append(trade_id)

    def can_place_order(self, max_orders: int) -> bool:
        return self.orders_this_run < max_orders

    @property
    def decision_count(self) -> int:
        return len(self.decisions_this_run)

    @property
    def trade_count(self) -> int:
        return len(self.trades_this_run)

    def has_decision(self, decision_id: str) -> bool:
        return decision_id in self.decisions_this_run

    def has_trade(self, trade_id: str) -> bool:
        return trade_id in self.trades_this_run
