from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.context.builder import classify_session
from src.core.enums import Direction, Session
from src.core.models import Bar


@dataclass(frozen=True)
class SessionRange:
    """High and low captured for one contiguous session block."""

    session: Session
    high: float
    low: float
    start_bar_index: int
    end_bar_index: int
    start_time: datetime
    end_time: datetime


@dataclass(frozen=True)
class SessionSweep:
    """A sweep of a prior-session extreme detected on the current bar.

    direction=BULLISH  → price spiked below the prior session low and closed back above it
                          (setup context: bullish reversal from sweep of lows)
    direction=BEARISH  → price spiked above the prior session high and closed back below it
                          (setup context: bearish reversal from sweep of highs)
    """

    direction: Direction
    swept_level: float
    swept_session: Session
    bar_index: int
    bar_time: datetime


@dataclass(frozen=True)
class SessionLevels:
    """Session-level context computed from M15 bars for the current symbol/run."""

    current_session: Session
    current_session_high: float
    current_session_low: float
    prior_completed_sessions: list[SessionRange]  # most-recent first
    sweep: SessionSweep | None  # populated when current bar sweeps a prior-session extreme


@dataclass(frozen=True)
class SessionLevelTracker:
    """Stateless tracker — call compute() each cycle.

    Parameters
    ----------
    lookback_sessions:
        How many completed sessions to retain (default 3).
        The sweep check always uses the most-recent completed session.
    """

    lookback_sessions: int = 3

    def compute(
        self,
        bars_m15: list[Bar],
        sessions_cfg: dict[str, Any],
    ) -> SessionLevels:
        """Derive session levels and optional sweep detection from M15 bars.

        Args:
            bars_m15:     Ordered list of M15 bars (oldest first).
            sessions_cfg: The "sessions" block from the symbol/run config,
                          e.g. {"ASIA": {"start": "00:00", "end": "06:00"}, ...}

        Returns:
            SessionLevels populated with current session H/L, prior completed
            sessions, and a SessionSweep if the current bar swept a prior extreme.
        """
        if not bars_m15:
            return SessionLevels(
                current_session=Session.CLOSED,
                current_session_high=0.0,
                current_session_low=0.0,
                prior_completed_sessions=[],
                sweep=None,
            )

        completed: list[SessionRange] = []
        _active_session: Session | None = None
        _active_high: float = 0.0
        _active_low: float = float("inf")
        _active_start_idx: int = 0
        _active_start_time: datetime = bars_m15[0].time

        for bar in bars_m15:
            bar_session = classify_session(bar.time, sessions_cfg)

            # Skip CLOSED gaps — they don't belong to any session range.
            if bar_session == Session.CLOSED:
                # If we were tracking an active session, close it out first.
                if _active_session is not None and _active_session != Session.CLOSED:
                    completed.append(
                        SessionRange(
                            session=_active_session,
                            high=_active_high,
                            low=_active_low,
                            start_bar_index=_active_start_idx,
                            end_bar_index=bar.bar_index - 1,
                            start_time=_active_start_time,
                            end_time=bar.time,
                        )
                    )
                    _active_session = None
                continue

            if bar_session != _active_session:
                # Close out the previous active session if there was one.
                if _active_session is not None and _active_session != Session.CLOSED:
                    completed.append(
                        SessionRange(
                            session=_active_session,
                            high=_active_high,
                            low=_active_low,
                            start_bar_index=_active_start_idx,
                            end_bar_index=bar.bar_index - 1,
                            start_time=_active_start_time,
                            end_time=bar.time,
                        )
                    )
                # Start new session block.
                _active_session = bar_session
                _active_high = bar.high
                _active_low = bar.low
                _active_start_idx = bar.bar_index
                _active_start_time = bar.time
            else:
                # Continue building current session range.
                if bar.high > _active_high:
                    _active_high = bar.high
                if bar.low < _active_low:
                    _active_low = bar.low

        # After the loop the last active block is the current (incomplete) session.
        current_session = _active_session if _active_session is not None else Session.CLOSED
        current_high = _active_high if current_session != Session.CLOSED else 0.0
        current_low = _active_low if current_session != Session.CLOSED and _active_low != float("inf") else 0.0

        # Most-recent completed sessions, capped at lookback_sessions.
        prior_sessions = list(reversed(completed))
        prior_sessions = prior_sessions[: self.lookback_sessions]

        # Sweep detection on the current bar (last bar in the list).
        sweep = self._detect_sweep(bars_m15[-1], prior_sessions)

        return SessionLevels(
            current_session=current_session,
            current_session_high=current_high,
            current_session_low=current_low,
            prior_completed_sessions=prior_sessions,
            sweep=sweep,
        )

    @staticmethod
    def _detect_sweep(bar: Bar, prior_sessions: list[SessionRange]) -> SessionSweep | None:
        """Check if `bar` swept the high or low of the most-recent prior session.

        A sweep is confirmed when:
          - Bullish: bar.low < prior_session.low  AND  bar.close > prior_session.low
                     (price dipped below the level and rejected back above it)
          - Bearish: bar.high > prior_session.high AND bar.close < prior_session.high
                     (price spiked above the level and rejected back below it)

        Only the most-recent completed session is checked.
        """
        if not prior_sessions:
            return None

        prior = prior_sessions[0]

        # Bullish sweep of lows (price wicked below prior session low, closed back above).
        if bar.low < prior.low and bar.close > prior.low:
            return SessionSweep(
                direction=Direction.BULLISH,
                swept_level=prior.low,
                swept_session=prior.session,
                bar_index=bar.bar_index,
                bar_time=bar.time,
            )

        # Bearish sweep of highs (price wicked above prior session high, closed back below).
        if bar.high > prior.high and bar.close < prior.high:
            return SessionSweep(
                direction=Direction.BEARISH,
                swept_level=prior.high,
                swept_session=prior.session,
                bar_index=bar.bar_index,
                bar_time=bar.time,
            )

        return None
