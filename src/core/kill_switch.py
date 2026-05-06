"""Kill switch — immediate, irreversible halt of all new live trades.

The kill switch is checked before every decision and inside the live order
wrapper. Once triggered, the system remains in HOLD state until explicitly
disarmed and re-armed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar


@dataclass(frozen=True)
class KillSwitchVerdict:
    triggered: bool
    reason: str = ""


class KillSwitch:
    """Global kill switch for live execution.

    Tracks:
      - Explicit config flag (kill_switch_enabled)
      - Manual operator trigger
      - Broker error rate (failed orders in trailing window)
      - Drawdown threshold

    The switch latches: once triggered, it stays triggered until reset().
    """

    _lookback_minutes: ClassVar[int] = 10
    _max_failures: ClassVar[int] = 3

    def __init__(self) -> None:
        self._triggered: bool = False
        self._reason: str = ""
        self._failed_orders: list[datetime] = []
        self._manual_trigger: bool = False

    def trigger(self, reason: str) -> None:
        """Manually trigger the kill switch."""
        self._triggered = True
        self._reason = reason
        self._manual_trigger = True

    def reset(self) -> None:
        """Reset the kill switch. Only call after disarm + operator review."""
        self._triggered = False
        self._reason = ""
        self._failed_orders.clear()
        self._manual_trigger = False

    def record_failure(self) -> None:
        """Record a broker order failure for error-rate tracking."""
        self._failed_orders.append(datetime.now(tz=UTC))
        self._prune_old_failures()

    def _prune_old_failures(self) -> None:
        cutoff = datetime.now(tz=UTC) - timedelta(minutes=self._lookback_minutes)
        self._failed_orders = [t for t in self._failed_orders if t > cutoff]

    def evaluate(
        self,
        *,
        config_kill_switch_enabled: bool = False,
        current_equity: float | None = None,
        initial_balance: float | None = None,
        drawdown_threshold: float = 0.20,
    ) -> KillSwitchVerdict:
        """Evaluate whether the kill switch should trigger.

        Checks in order:
          1. Already latched (triggered previously)
          2. Config flag
          3. Manual trigger
          4. Broker error rate
          5. Drawdown breach
        """
        if self._triggered:
            return KillSwitchVerdict(triggered=True, reason=self._reason)

        if config_kill_switch_enabled:
            self._triggered = True
            self._reason = "config_kill_switch"
            return KillSwitchVerdict(triggered=True, reason=self._reason)

        if self._manual_trigger:
            self._triggered = True
            self._reason = "manual_trigger"
            return KillSwitchVerdict(triggered=True, reason=self._reason)

        self._prune_old_failures()
        if len(self._failed_orders) >= self._max_failures:
            self._triggered = True
            self._reason = "broker_error_rate_exceeded"
            return KillSwitchVerdict(triggered=True, reason=self._reason)

        if current_equity is not None and initial_balance is not None and initial_balance > 0:
            if current_equity <= initial_balance * (1 - drawdown_threshold):
                self._triggered = True
                self._reason = "drawdown_breach"
                return KillSwitchVerdict(triggered=True, reason=self._reason)

        return KillSwitchVerdict(triggered=False, reason="")

    @property
    def is_triggered(self) -> bool:
        return self._triggered

    @property
    def reason(self) -> str:
        return self._reason
