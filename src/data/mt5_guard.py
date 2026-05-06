"""MT5 safety guard for paper mode.

Wraps the real MT5 client and blocks all broker execution methods.
Allowed: data reading only (bars, symbol info, account info, ticks)
Forbidden: any method that can place, modify, or close trades.
"""
from __future__ import annotations

from typing import Any

FORBIDDEN_METHODS = frozenset({
    "order_send",
    "order_check",
    "order_calc_margin",
    "order_calc_profit",
    "order_calc_profit_rate",
    "order_calc_profit_rate_by_volume",
    "order_calc_profit_by_volume",
    "order_calc_margin_by_volume",
    "order_modify",
    "order_cancel",
    "order_get",
    "order_get_total",
    "position_close",
    "position_modify",
    "position_get",
    "position_get_total",
    "positions_total",
    "trade_order_send",
    "trade_order_modify",
    "trade_position_close",
    "trade_position_modify",
})


class MT5PaperGuard:
    """Wraps an MT5 client and raises if any forbidden broker method is called.

    Usage:
        raw_mt5 = MetaTrader5
        safe_mt5 = MT5PaperGuard(raw_mt5)
        safe_mt5.symbol_info("EURUSD")  # OK
        safe_mt5.order_send(...)        # Raises MT5BrokerMethodForbidden
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def __getattr__(self, name: str) -> Any:
        if name in FORBIDDEN_METHODS:
            raise MT5BrokerMethodForbidden(
                f"MT5 broker method '{name}' is forbidden in paper mode. "
                f"Paper mode is data-source only."
            )
        return getattr(self._client, name)

    @property
    def __class__(self):
        return self._client.__class__


class MT5BrokerMethodForbidden(RuntimeError):
    """Raised when a forbidden MT5 broker method is called in paper mode."""
    pass


def create_paper_safe_mt5() -> MT5PaperGuard:
    """Import MetaTrader5 and wrap it with the paper safety guard."""
    import MetaTrader5 as mt5  # type: ignore[import-untyped]
    return MT5PaperGuard(mt5)
