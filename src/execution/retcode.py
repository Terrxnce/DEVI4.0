"""MT5 retcode mapping — translate broker return codes to internal statuses.

Reference: MetaTrader 5 TRADE_RETCODE_* constants.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BrokerStatus(str, Enum):
    """Canonical internal status for a broker order response."""

    FILLED = "FILLED"
    REQUOTE = "REQUOTE"
    INVALID_STOPS = "INVALID_STOPS"
    TRADE_DISABLED = "TRADE_DISABLED"
    MARKET_CLOSED = "MARKET_CLOSED"
    NO_MONEY = "NO_MONEY"
    PRICE_OFF = "PRICE_OFF"
    REJECT = "REJECT"
    TIMEOUT = "TIMEOUT"
    CONNECTION = "CONNECTION"
    UNKNOWN = "UNKNOWN"


class RetryPolicy(str, Enum):
    """What to do when a retcode is received."""

    OK = "ok"              # success, no retry
    RETRY = "retry"        # transient, safe to retry
    NO_RETRY = "no_retry"  # fatal, do not retry
    HALT = "halt"          # system-level issue, trigger kill switch


@dataclass(frozen=True)
class RetcodeMapping:
    retcode: int
    status: BrokerStatus
    policy: RetryPolicy
    description: str


# ---------------------------------------------------------------------------
# Retcode matrix
# ---------------------------------------------------------------------------

_RETCODE_MAP: dict[int, RetcodeMapping] = {
    # Success
    10009: RetcodeMapping(10009, BrokerStatus.FILLED, RetryPolicy.OK, "Request completed"),
    10008: RetcodeMapping(10008, BrokerStatus.FILLED, RetryPolicy.OK, "Order placed"),

    # Requotes / price issues — retry once
    10004: RetcodeMapping(10004, BrokerStatus.REQUOTE, RetryPolicy.RETRY, "Requote"),
    10011: RetcodeMapping(10011, BrokerStatus.REQUOTE, RetryPolicy.RETRY, "Requote"),
    10012: RetcodeMapping(10012, BrokerStatus.REQUOTE, RetryPolicy.RETRY, "Prices changed"),
    10024: RetcodeMapping(10024, BrokerStatus.PRICE_OFF, RetryPolicy.RETRY, "Price off"),

    # Invalid parameters — no retry
    10014: RetcodeMapping(10014, BrokerStatus.INVALID_STOPS, RetryPolicy.NO_RETRY, "Invalid stops"),
    10015: RetcodeMapping(10015, BrokerStatus.REJECT, RetryPolicy.NO_RETRY, "Invalid trade volume"),
    10016: RetcodeMapping(10016, BrokerStatus.TRADE_DISABLED, RetryPolicy.NO_RETRY, "Trade disabled"),
    10017: RetcodeMapping(10017, BrokerStatus.REJECT, RetryPolicy.NO_RETRY, "Market order disabled"),
    10018: RetcodeMapping(10018, BrokerStatus.REJECT, RetryPolicy.NO_RETRY, "Position not found"),
    10025: RetcodeMapping(10025, BrokerStatus.REJECT, RetryPolicy.NO_RETRY, "Invalid order type"),
    10026: RetcodeMapping(10026, BrokerStatus.REJECT, RetryPolicy.NO_RETRY, "Invalid symbol"),

    # Account / market state — no retry, may need attention
    10021: RetcodeMapping(10021, BrokerStatus.MARKET_CLOSED, RetryPolicy.NO_RETRY, "Market closed"),
    10027: RetcodeMapping(10027, BrokerStatus.NO_MONEY, RetryPolicy.NO_RETRY, "Not enough money"),
    10028: RetcodeMapping(10028, BrokerStatus.NO_MONEY, RetryPolicy.NO_RETRY, "Not enough money"),
    10029: RetcodeMapping(10029, BrokerStatus.REJECT, RetryPolicy.NO_RETRY, "Order locked"),

    # Connection / server errors — halt
    10006: RetcodeMapping(10006, BrokerStatus.REJECT, RetryPolicy.NO_RETRY, "Request rejected"),
    10007: RetcodeMapping(10007, BrokerStatus.REJECT, RetryPolicy.NO_RETRY, "Request canceled"),
    10010: RetcodeMapping(10010, BrokerStatus.CONNECTION, RetryPolicy.HALT, "Request processing error"),
    10022: RetcodeMapping(10022, BrokerStatus.CONNECTION, RetryPolicy.HALT, "Connection error"),
    10023: RetcodeMapping(10023, BrokerStatus.TIMEOUT, RetryPolicy.HALT, "Connection timeout"),
}


def map_retcode(retcode: int) -> RetcodeMapping:
    """Return the canonical mapping for an MT5 retcode.

    Unknown retcodes map to UNKNOWN with NO_RETRY policy.
    """
    return _RETCODE_MAP.get(retcode, RetcodeMapping(
        retcode=retcode,
        status=BrokerStatus.UNKNOWN,
        policy=RetryPolicy.NO_RETRY,
        description=f"Unknown retcode: {retcode}",
    ))


def is_success(retcode: int) -> bool:
    """Return True if the retcode indicates a successful fill."""
    return map_retcode(retcode).status == BrokerStatus.FILLED


def should_record_failure(retcode: int) -> bool:
    """Return True if this retcode should count toward broker error rate."""
    mapping = map_retcode(retcode)
    return mapping.policy in (RetryPolicy.RETRY, RetryPolicy.HALT, RetryPolicy.NO_RETRY) and mapping.status != BrokerStatus.FILLED
