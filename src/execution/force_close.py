"""Emergency force close for all D.E.V.I live positions.

Identifies D.E.V.I positions by the 'devi:' comment prefix written by
LiveOrderWrapper. Closes each position with a market order directly against
MT5. Does NOT require a running LiveSession — operates independently.

Use when:
- A trade is stuck or behaving unexpectedly
- The bot crashes with an open position
- The kill switch fires but a position is already open
- You need to flatten all D.E.V.I exposure immediately
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEVI_COMMENT_PREFIX = "devi:"


@dataclass
class ForceCloseResult:
    """Result of a single force-close attempt."""

    ticket: int
    symbol: str
    side: str
    volume: float
    open_price: float
    close_price: float | None
    retcode: int | None
    retcode_comment: str | None
    status: str  # "closed" | "failed" | "no_positions"
    reason: str
    timestamp: str


def is_devi_position(position: Any) -> bool:
    """Return True if this MT5 position was opened by D.E.V.I.

    D.E.V.I sets comment = 'devi:{run_id}:{trade_id}' on every order_send call.
    This is the only reliable identifier that survives across sessions since
    the magic number is derived from the arming token and changes each run.
    """
    comment = getattr(position, "comment", "") or ""
    return comment.startswith(DEVI_COMMENT_PREFIX)


def _build_close_request(position: Any, mt5: Any) -> dict:
    """Build a market close request for a single open position."""
    pos_type = int(getattr(position, "type", 0))
    symbol = str(getattr(position, "symbol", ""))
    volume = float(getattr(position, "volume", 0.0))
    ticket = int(getattr(position, "ticket", 0))

    # Closing a BUY requires a SELL market order and vice versa
    if pos_type == 0:  # BUY position
        close_type = mt5.ORDER_TYPE_SELL
        tick = mt5.symbol_info_tick(symbol)
        price = float(tick.bid) if tick is not None else 0.0
    else:  # SELL position
        close_type = mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(symbol)
        price = float(tick.ask) if tick is not None else 0.0

    type_filling = getattr(mt5, "ORDER_FILLING_IOC", 1)

    return {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": close_type,
        "position": ticket,
        "price": price,
        "deviation": 20,
        "comment": "devi_force_close",
        "type_filling": type_filling,
    }


def close_devi_positions(
    mt5: Any,
    *,
    log_path: str | None = None,
) -> list[ForceCloseResult]:
    """Find all D.E.V.I positions and close them with market orders.

    Args:
        mt5: Live MT5 client. Must expose positions_get, order_send,
             symbol_info_tick, ORDER_TYPE_BUY, ORDER_TYPE_SELL,
             TRADE_ACTION_DEAL, ORDER_FILLING_IOC.
        log_path: Optional JSONL path to append results to.

    Returns:
        List of ForceCloseResult, one per D.E.V.I position found.
        Returns empty list if MT5 is unavailable or no positions exist.
    """
    results: list[ForceCloseResult] = []

    if mt5 is None:
        logger.error("force_close: mt5 client is None — cannot proceed")
        return results

    if not callable(getattr(mt5, "positions_get", None)):
        logger.error("force_close: mt5 client has no positions_get method")
        return results

    # Fetch all open positions
    try:
        raw_positions = mt5.positions_get()
    except Exception as exc:
        logger.error("force_close: positions_get raised exception: %s", exc)
        return results

    if not raw_positions:
        logger.info("force_close: no open positions found")
        return results

    devi_positions = [p for p in raw_positions if is_devi_position(p)]

    if not devi_positions:
        logger.info(
            "force_close: no D.E.V.I positions found "
            "(checked %d total position(s))",
            len(raw_positions),
        )
        return results

    logger.warning(
        "force_close: found %d D.E.V.I position(s) — closing now",
        len(devi_positions),
    )

    for position in devi_positions:
        ticket = int(getattr(position, "ticket", 0))
        symbol = str(getattr(position, "symbol", ""))
        pos_type = int(getattr(position, "type", 0))
        volume = float(getattr(position, "volume", 0.0))
        open_price = float(getattr(position, "price_open", 0.0))
        side = "BUY" if pos_type == 0 else "SELL"
        ts = datetime.now(tz=UTC).isoformat()

        # Build close request
        try:
            request = _build_close_request(position, mt5)
        except Exception as exc:
            logger.error(
                "force_close: failed to build request for ticket %d: %s",
                ticket, exc,
            )
            results.append(ForceCloseResult(
                ticket=ticket, symbol=symbol, side=side, volume=volume,
                open_price=open_price, close_price=None,
                retcode=None, retcode_comment=None,
                status="failed", reason=f"request_build_error:{exc}",
                timestamp=ts,
            ))
            continue

        # Send close order
        try:
            result = mt5.order_send(request)
        except Exception as exc:
            logger.error(
                "force_close: order_send raised exception for ticket %d: %s",
                ticket, exc,
            )
            results.append(ForceCloseResult(
                ticket=ticket, symbol=symbol, side=side, volume=volume,
                open_price=open_price, close_price=None,
                retcode=None, retcode_comment=None,
                status="failed", reason=f"order_send_exception:{exc}",
                timestamp=ts,
            ))
            continue

        if result is None:
            logger.error(
                "force_close: order_send returned None for ticket %d", ticket,
            )
            results.append(ForceCloseResult(
                ticket=ticket, symbol=symbol, side=side, volume=volume,
                open_price=open_price, close_price=None,
                retcode=None, retcode_comment=None,
                status="failed", reason="order_send_returned_none",
                timestamp=ts,
            ))
            continue

        retcode = int(getattr(result, "retcode", -1))
        retcode_comment = str(getattr(result, "comment", ""))
        raw_price = getattr(result, "price", None)
        close_price = float(raw_price) if raw_price else None
        success = retcode == 10009  # TRADE_RETCODE_DONE

        fc_result = ForceCloseResult(
            ticket=ticket,
            symbol=symbol,
            side=side,
            volume=volume,
            open_price=open_price,
            close_price=close_price,
            retcode=retcode,
            retcode_comment=retcode_comment,
            status="closed" if success else "failed",
            reason="market_close_ok" if success else f"retcode={retcode}:{retcode_comment}",
            timestamp=ts,
        )
        results.append(fc_result)

        if success:
            logger.info(
                "force_close: CLOSED ticket=%d %s %s %.2f lots close_price=%s",
                ticket, symbol, side, volume, close_price,
            )
        else:
            logger.error(
                "force_close: FAILED ticket=%d %s retcode=%d comment=%s",
                ticket, symbol, retcode, retcode_comment,
            )

    if log_path and results:
        _write_log(results, log_path)

    return results


def _write_log(results: list[ForceCloseResult], log_path: str) -> None:
    """Append force close results to a JSONL log file."""
    try:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps(asdict(r)) + "\n")
        logger.info("force_close: results written to %s", log_path)
    except Exception as exc:
        logger.warning("force_close: log write failed: %s", exc)
