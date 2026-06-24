"""Option B: a scaled position's final trade_close must carry full-position PnL.

_enrich_close_from_history sums realized profit across ALL out deals (partial
legs + runner), so the close record reconciles to the broker's full-position
result instead of only the final leg. Single-exit trades are unchanged (parity).
"""
from __future__ import annotations

from src.execution.live_position_tracker import LivePositionTracker


class _Pos:
    def __init__(self, ticket):
        self.ticket = ticket
        self.symbol = "GBPJPY"
        self.type = 1  # SELL
        self.volume = 3.22
        self.price_open = 190.00
        self.price_current = 189.50
        self.sl = 191.0
        self.tp = 188.0
        self.profit = 0.0
        self.swap = 0.0
        self.comment = "devi:run:t"


class _Deal:
    def __init__(self, entry=1, price=189.5, profit=0.0, reason=5, time=1_700_000_000):
        self.entry = entry
        self.price = price
        self.profit = profit
        self.reason = reason
        self.time = time


class _MT5:
    def __init__(self, positions, deals):
        self._positions = positions
        self._deals = deals

    def positions_get(self):
        return self._positions

    def history_deals_get(self, *, position):
        return self._deals


def _close_and_get(ticket, deals):
    pos = _Pos(ticket)
    mt5 = _MT5([pos], deals)
    tr = LivePositionTracker(mt5)
    tr.sync_positions()      # sees it open
    mt5._positions = []       # now gone
    tr.sync_positions()      # detects close + enriches
    return tr.get_position(ticket)


def test_scaled_close_sums_all_out_deals():
    # partial leg +222.00, runner +346.43 -> full position 568.43
    partial = _Deal(entry=1, price=189.7, profit=222.00, reason=3)
    runner = _Deal(entry=1, price=189.5, profit=346.43, reason=5)
    lp = _close_and_get(3001, [partial, runner])
    assert round(lp.close_pnl, 2) == 568.43
    # reason/price come from the final (last) out deal
    assert lp.close_reason == "tp_hit"
    assert lp.close_price == 189.5


def test_single_exit_close_is_unchanged():
    lp = _close_and_get(3002, [_Deal(entry=1, price=189.5, profit=-141.25, reason=4)])
    assert lp.close_pnl == -141.25
    assert lp.close_reason == "sl_hit"


def test_in_deals_ignored_only_out_summed():
    # entry (IN) deal must not be added into realized PnL
    in_deal = _Deal(entry=0, price=190.0, profit=0.0, reason=0)
    out_deal = _Deal(entry=1, price=189.5, profit=100.0, reason=5)
    lp = _close_and_get(3003, [in_deal, out_deal])
    assert lp.close_pnl == 100.0
