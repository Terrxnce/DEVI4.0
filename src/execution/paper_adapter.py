from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from src.core.enums import Direction
from src.core.models import TradeIntent


@dataclass(frozen=True)
class PaperFillResult:
    decision_id: str
    # TODO(Phase 2): trade_id should be distinct from decision_id, with a link field.
    trade_id: str
    ticket: int
    symbol: str
    side: str
    intended_entry: float
    actual_fill: float
    planned_sl: float
    planned_tp: float
    slippage: float
    spread_at_decision: float
    spread_at_fill: float
    # Paper-only synthetic retcode. Never from a real broker.
    paper_retcode: int
    order_status: str
    execution_time: str


class PaperExecutionAdapter:
    """Simulates trade fills using MT5-derived bid/ask/spread.

    No broker methods are called. All values are synthetic.
    Fill logic:
      BUY fills at ask  = intended_entry + spread
      SELL fills at bid = intended_entry - spread
    """

    def __init__(self) -> None:
        self._ticket_counter = 100000

    def execute(
        self,
        intent: TradeIntent,
        spread_at_decision: float,
        spread_at_fill: float | None = None,
    ) -> PaperFillResult:
        spread_fill = spread_at_fill if spread_at_fill is not None else spread_at_decision

        if intent.direction == Direction.BULLISH:
            # BUY fills at ask = bid (intended entry) + full spread
            fill_price = intent.entry_price + spread_fill
            side = "BUY"
        else:
            # SELL fills at bid = ask (intended entry) - full spread
            fill_price = intent.entry_price - spread_fill
            side = "SELL"

        fill_price = float(fill_price)
        slippage = fill_price - intent.entry_price
        self._ticket_counter += 1

        return PaperFillResult(
            decision_id=intent.trade_id,
            trade_id=intent.trade_id,
            ticket=self._ticket_counter,
            symbol=intent.symbol,
            side=side,
            intended_entry=float(intent.entry_price),
            actual_fill=fill_price,
            planned_sl=float(intent.exit_plan.stop_loss),
            planned_tp=float(intent.exit_plan.take_profit),
            slippage=float(slippage),
            spread_at_decision=float(spread_at_decision),
            spread_at_fill=float(spread_fill),
            paper_retcode=10009,
            order_status="FILLED",
            execution_time=datetime.now(tz=UTC).isoformat(),
        )
