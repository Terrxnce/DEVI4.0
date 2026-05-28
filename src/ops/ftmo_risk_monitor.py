"""FTMORiskMonitor — enforces FTMO daily and overall loss limits.

FTMO loss rules (standard challenge):
  - Maximum daily loss:   5% of initial account balance
  - Maximum total loss:  10% of initial account balance

The daily loss floor is dynamic:
  floor_today = day_start_balance - (initial_balance * max_daily_loss_pct)

  This means the floor rises as your balance grows (a day starting at $10,200
  has a floor of $10,200 - $500 = $9,700, not $9,500), but a bad day can never
  set the floor lower than it was — the floor is always calculated from the
  previous midnight balance, not from today's losses.

FTMO resets the daily window at midnight Prague time (CET/CEST, Europe/Prague).

Usage:
    monitor = FTMORiskMonitor(
        initial_balance=10000.0,
        state_path="logs/prod/ftmo_state.json",
    )
    # Call once per run at the start (before trading)
    monitor.start_of_day_snapshot(current_balance=account["balance"])
    # Then evaluate before placing any trade
    result = monitor.evaluate(equity=account["equity"], balance=account["balance"])
    if not result.daily_ok:
        # Do not trade — daily limit reached or breached
        ...

State file (JSON):
    {
      "initial_balance":     10000.0,
      "day_start_balance":   10200.0,
      "snapshot_date":       "2026-05-14",   -- Prague date string
      "snapshot_timestamp":  "2026-05-14T00:00:12+02:00"
    }
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prague timezone handling
# ---------------------------------------------------------------------------

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # type: ignore[import]
    try:
        _PRAGUE_TZ = ZoneInfo("Europe/Prague")
        _ZONEINFO_OK = True
    except (ZoneInfoNotFoundError, KeyError):
        _PRAGUE_TZ = None  # type: ignore[assignment]
        _ZONEINFO_OK = False
        logger.warning(
            "ftmo_risk_monitor: ZoneInfo('Europe/Prague') not available — "
            "install tzdata (`pip install tzdata`) for correct DST handling. "
            "Falling back to fixed UTC+2 (CEST). Winter sessions will be off by 1 hour."
        )
except ImportError:
    _PRAGUE_TZ = None  # type: ignore[assignment]
    _ZONEINFO_OK = False
    logger.warning(
        "ftmo_risk_monitor: zoneinfo module not available — using fixed UTC+2 fallback."
    )


def _now_prague() -> datetime:
    """Return current datetime in Prague timezone (CET/CEST)."""
    utc_now = datetime.now(tz=UTC)
    if _ZONEINFO_OK and _PRAGUE_TZ is not None:
        return utc_now.astimezone(_PRAGUE_TZ)
    # Fallback: fixed UTC+2 (CEST). Correct for roughly 7 months of the year.
    return utc_now.astimezone(timezone(timedelta(hours=2)))


def _prague_today() -> str:
    """Return today's date string in Prague time (YYYY-MM-DD)."""
    return _now_prague().date().isoformat()


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FTMORiskResult:
    """Outcome of a risk evaluation check."""

    daily_ok: bool          # True = still within daily loss limit
    total_ok: bool          # True = still within total loss limit
    daily_pnl_pct: float    # current day PnL as % of initial balance (negative = loss)
    total_pnl_pct: float    # total PnL since challenge start as % of initial balance
    daily_floor: float      # absolute equity floor for today
    total_floor: float      # absolute equity floor for the challenge
    day_start_balance: float
    initial_balance: float
    reason: str             # human-readable status


# ---------------------------------------------------------------------------
# FTMORiskMonitor
# ---------------------------------------------------------------------------


class FTMORiskMonitor:
    """Tracks FTMO-compliant daily and total loss limits.

    Args:
        initial_balance:       Account balance at challenge start (from config or
                               auto-detected on first run).
        max_daily_loss_pct:    Daily loss limit as a fraction (0.05 = 5%).
        max_total_loss_pct:    Total loss limit as a fraction (0.10 = 10%).
        state_path:            Path to persist the daily snapshot. Required for
                               correct cross-run behaviour.
        daily_buffer_pct:      Extra safety margin added to the daily floor.
                               0.005 = stop trading at 4.5% loss, not 5%.
                               Protects against slippage past the exact threshold.
        total_buffer_pct:      Same concept for total loss.
    """

    def __init__(
        self,
        *,
        initial_balance: float,
        max_daily_loss_pct: float = 0.05,
        max_total_loss_pct: float = 0.10,
        state_path: Path | str | None = None,
        daily_buffer_pct: float = 0.005,
        total_buffer_pct: float = 0.005,
    ) -> None:
        self._initial_balance = initial_balance
        self._max_daily_loss_pct = max_daily_loss_pct
        self._max_total_loss_pct = max_total_loss_pct
        self._state_path = Path(state_path) if state_path is not None else None
        self._daily_buffer_pct = daily_buffer_pct
        self._total_buffer_pct = total_buffer_pct

        # In-memory state (overwritten by _load_state if file exists).
        # _snapshot_date intentionally empty so the first start_of_day_snapshot
        # call always fires and saves state, even on first-ever run.
        self._day_start_balance: float = initial_balance
        self._snapshot_date: str = ""

        if self._state_path is not None:
            self._load_state()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start_of_day_snapshot(self, current_balance: float) -> None:
        """Record the start-of-day balance if we've crossed into a new Prague day.

        Call once per run cycle at the start, before any trading decisions.
        On the first call each Prague calendar day, the current balance is saved
        as the daily reference point.
        """
        today = _prague_today()
        if today != self._snapshot_date:
            old_date = self._snapshot_date
            old_balance = self._day_start_balance
            self._snapshot_date = today
            self._day_start_balance = current_balance
            self._save_state()
            logger.info(
                "ftmo_risk_monitor: new_day date=%s balance=%.2f "
                "(prev: date=%s balance=%.2f)",
                today, current_balance, old_date, old_balance,
            )
        else:
            logger.debug(
                "ftmo_risk_monitor: same_day date=%s day_start_balance=%.2f",
                today, self._day_start_balance,
            )

    def evaluate(self, *, equity: float, balance: float) -> FTMORiskResult:
        """Evaluate current account state against FTMO limits.

        Args:
            equity:   Current account equity (balance + floating PnL).
            balance:  Current account balance (realised).

        Returns:
            FTMORiskResult with daily_ok and total_ok flags.
        """
        initial = self._initial_balance
        day_start = self._day_start_balance

        # Effective floors include safety buffers
        daily_loss_amount = initial * (self._max_daily_loss_pct - self._daily_buffer_pct)
        total_loss_amount = initial * (self._max_total_loss_pct - self._total_buffer_pct)

        daily_floor = day_start - daily_loss_amount
        total_floor = initial - total_loss_amount

        # Use equity (includes open trade floating P&L) for the check.
        # FTMO measures both balance and equity — the lower of the two applies.
        check_value = min(equity, balance)

        daily_ok = check_value > daily_floor
        total_ok = check_value > total_floor

        daily_pnl_pct = round((check_value - day_start) / initial * 100, 4)
        total_pnl_pct = round((check_value - initial) / initial * 100, 4)

        if not daily_ok:
            reason = (
                f"daily_floor_breached: equity={check_value:.2f} "
                f"floor={daily_floor:.2f} day_start={day_start:.2f}"
            )
            logger.critical("ftmo_risk_monitor: DAILY FLOOR BREACHED — %s", reason)
        elif not total_ok:
            reason = (
                f"total_floor_breached: equity={check_value:.2f} "
                f"floor={total_floor:.2f} initial={initial:.2f}"
            )
            logger.critical("ftmo_risk_monitor: TOTAL FLOOR BREACHED — %s", reason)
        else:
            daily_remaining = check_value - daily_floor
            total_remaining = check_value - total_floor
            reason = (
                f"ok: daily_remaining={daily_remaining:.2f} "
                f"total_remaining={total_remaining:.2f}"
            )
            logger.debug("ftmo_risk_monitor: %s", reason)

        return FTMORiskResult(
            daily_ok=daily_ok,
            total_ok=total_ok,
            daily_pnl_pct=daily_pnl_pct,
            total_pnl_pct=total_pnl_pct,
            daily_floor=daily_floor,
            total_floor=total_floor,
            day_start_balance=day_start,
            initial_balance=initial,
            reason=reason,
        )

    def get_day_start_balance(self) -> float:
        return self._day_start_balance

    def get_snapshot_date(self) -> str:
        return self._snapshot_date

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            saved_initial = float(data.get("initial_balance", self._initial_balance))
            if abs(saved_initial - self._initial_balance) > 0.01:
                logger.warning(
                    "ftmo_risk_monitor: state file initial_balance=%.2f differs from "
                    "config initial_balance=%.2f — using config value",
                    saved_initial, self._initial_balance,
                )
            self._day_start_balance = float(data.get("day_start_balance", self._initial_balance))
            self._snapshot_date = str(data.get("snapshot_date", _prague_today()))
            logger.info(
                "ftmo_risk_monitor: loaded state date=%s day_start=%.2f",
                self._snapshot_date, self._day_start_balance,
            )
        except (json.JSONDecodeError, KeyError, ValueError, OSError) as exc:
            logger.warning("ftmo_risk_monitor: could not load state file (%s) — starting fresh", exc)

    def _save_state(self) -> None:
        if self._state_path is None:
            return
        data = {
            "initial_balance": self._initial_balance,
            "day_start_balance": self._day_start_balance,
            "snapshot_date": self._snapshot_date,
            "snapshot_timestamp": _now_prague().isoformat(),
        }
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.error("ftmo_risk_monitor: failed to save state: %s", exc)
