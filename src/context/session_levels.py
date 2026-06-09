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
    """A sweep of a prior-session extreme detected within the lookback window.

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
    sweep: SessionSweep | None  # most recent sweep within lookback window, or None


@dataclass(frozen=True)
class SessionLevelTracker:
    """Stateless tracker — call compute() each cycle.

    Parameters
    ----------
    lookback_sessions:
        How many completed sessions to retain (default 3).
    sweep_lookback_bars:
        How many bars back to search for a qualifying sweep (default 20).
        On M15 bars, 20 bars = 5 hours — covers the full sweep → displacement →
        retrace sequence before the signal expires.

        Background: a SWEEP_REVERSAL entry fires on the retrace into the post-sweep
        FVG, which typically happens 3–15 bars after the sweep itself. Checking only
        the current bar (the original behaviour) means the sweep is gone from
        session_levels.sweep by the time price retraces, producing zero candidates.
        A configurable lookback window keeps the sweep alive across that gap.
    """

    lookback_sessions: int = 3
    sweep_lookback_bars: int = 20

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
            sessions, and the most recent SessionSweep within sweep_lookback_bars
            if one exists.
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

        # Most recent sweep detected within the lookback window.
        # Updated inline as we process bars so that the prior-session reference
        # is always correct at each bar's point in time.
        latest_sweep: SessionSweep | None = None

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

            # Sweep detection — runs for every non-CLOSED bar using the most
            # recently completed session as the reference. This is the correct
            # reference because `completed` reflects session state at this
            # exact point in time (not the end of the bar array), so we never
            # compare against a session that hadn't finished yet.
            if completed:
                prior = completed[-1]

                # Bullish sweep: wick below prior session low, close back above.
                if bar.low < prior.low and bar.close > prior.low:
                    latest_sweep = SessionSweep(
                        direction=Direction.BULLISH,
                        swept_level=prior.low,
                        swept_session=prior.session,
                        bar_index=bar.bar_index,
                        bar_time=bar.time,
                    )

                # Bearish sweep: wick above prior session high, close back below.
                elif bar.high > prior.high and bar.close < prior.high:
                    latest_sweep = SessionSweep(
                        direction=Direction.BEARISH,
                        swept_level=prior.high,
                        swept_session=prior.session,
                        bar_index=bar.bar_index,
                        bar_time=bar.time,
                    )

        # After the loop the last active block is the current (incomplete) session.
        current_session = _active_session if _active_session is not None else Session.CLOSED
        current_high = _active_high if current_session != Session.CLOSED else 0.0
        current_low = _active_low if current_session != Session.CLOSED and _active_low != float("inf") else 0.0

        # Most-recent completed sessions, capped at lookback_sessions.
        prior_sessions = list(reversed(completed))
        prior_sessions = prior_sessions[: self.lookback_sessions]

        # Apply lookback cutoff: discard sweep if it happened more than
        # sweep_lookback_bars ago relative to the most recent bar.
        current_bar_index = bars_m15[-1].bar_index
        if latest_sweep is not None:
            bars_since_sweep = current_bar_index - latest_sweep.bar_index
            if bars_since_sweep > self.sweep_lookback_bars:
                latest_sweep = None

        return SessionLevels(
            current_session=current_session,
            current_session_high=current_high,
            current_session_low=current_low,
            prior_completed_sessions=prior_sessions,
            sweep=latest_sweep,
        )
