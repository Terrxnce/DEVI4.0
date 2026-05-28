from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.context.session_levels import SessionLevelTracker, SessionSweep
from src.core.enums import Direction, Session, Timeframe
from src.core.models import Bar


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SESSIONS_CFG = {
    "ASIA":   {"start": "00:00", "end": "06:00"},
    "LONDON": {"start": "07:00", "end": "11:30"},
    "NY_AM":  {"start": "13:00", "end": "16:00"},
    "NY_PM":  {"start": "16:00", "end": "19:00"},
}

TRACKER = SessionLevelTracker()


def _bar(
    bar_index: int,
    hour: int,
    minute: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    day: int = 20,
) -> Bar:
    ts = datetime(2026, 5, day, hour, minute, tzinfo=UTC)
    return Bar(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        time=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        bar_index=bar_index,
    )


def _bars_for_range(
    start_hour: int,
    start_minute: int,
    n_bars: int,
    base_price: float = 1.1000,
    bar_range: float = 0.0010,
) -> list[Bar]:
    """Generate n_bars of M15 bars starting at start_hour:start_minute."""
    bars = []
    for i in range(n_bars):
        total_minutes = start_hour * 60 + start_minute + i * 15
        h = (total_minutes // 60) % 24
        m = total_minutes % 60
        bars.append(
            _bar(
                bar_index=i,
                hour=h,
                minute=m,
                open_=base_price,
                high=base_price + bar_range,
                low=base_price - bar_range,
                close=base_price,
            )
        )
    return bars


# ---------------------------------------------------------------------------
# Empty / degenerate input
# ---------------------------------------------------------------------------


def test_empty_bars_returns_closed_session():
    result = TRACKER.compute([], SESSIONS_CFG)
    assert result.current_session == Session.CLOSED
    assert result.sweep is None
    assert result.prior_completed_sessions == []


# ---------------------------------------------------------------------------
# Session classification
# ---------------------------------------------------------------------------


def test_current_session_asia_identified():
    bars = _bars_for_range(start_hour=1, start_minute=0, n_bars=4)
    result = TRACKER.compute(bars, SESSIONS_CFG)
    assert result.current_session == Session.ASIA


def test_current_session_london_identified():
    bars = _bars_for_range(start_hour=8, start_minute=0, n_bars=4)
    result = TRACKER.compute(bars, SESSIONS_CFG)
    assert result.current_session == Session.LONDON


def test_current_session_closed_identified():
    # 06:00–06:59 is the gap between ASIA and LONDON — classified as CLOSED.
    bars = _bars_for_range(start_hour=6, start_minute=15, n_bars=2)
    result = TRACKER.compute(bars, SESSIONS_CFG)
    assert result.current_session == Session.CLOSED


# ---------------------------------------------------------------------------
# Session H/L tracking
# ---------------------------------------------------------------------------


def test_current_session_high_low_tracked():
    bars = [
        _bar(0, 8,  0, 1.1000, 1.1050, 1.0980, 1.1010),
        _bar(1, 8, 15, 1.1010, 1.1080, 1.0970, 1.1020),
        _bar(2, 8, 30, 1.1020, 1.1040, 1.0990, 1.1015),
    ]
    result = TRACKER.compute(bars, SESSIONS_CFG)
    assert result.current_session == Session.LONDON
    assert result.current_session_high == pytest.approx(1.1080)
    assert result.current_session_low == pytest.approx(1.0970)


# ---------------------------------------------------------------------------
# Prior completed sessions
# ---------------------------------------------------------------------------


def test_prior_completed_session_captured():
    # Asia bars followed by London bar — Asia should appear as completed.
    asia_bars = [
        _bar(0, 1, 0, 1.1000, 1.1030, 1.0970, 1.1010),
        _bar(1, 2, 0, 1.1010, 1.1050, 1.0960, 1.1020),
    ]
    # CLOSED gap bar
    gap_bar = _bar(2, 6, 15, 1.1020, 1.1025, 1.1015, 1.1020)
    # London bar
    london_bar = _bar(3, 8, 0, 1.1020, 1.1040, 1.1005, 1.1025)
    bars = asia_bars + [gap_bar, london_bar]
    result = TRACKER.compute(bars, SESSIONS_CFG)
    assert result.current_session == Session.LONDON
    assert len(result.prior_completed_sessions) == 1
    prior = result.prior_completed_sessions[0]
    assert prior.session == Session.ASIA
    assert prior.high == pytest.approx(1.1050)
    assert prior.low == pytest.approx(1.0960)


def test_lookback_sessions_capped():
    # Build Asia + London + NY_AM sessions, then check that with lookback=2 only 2 are returned.
    tracker = SessionLevelTracker(lookback_sessions=2)
    bars = [
        # Asia
        _bar(0, 1,  0, 1.1000, 1.1030, 1.0970, 1.1010),
        # London
        _bar(1, 8,  0, 1.1010, 1.1050, 1.0960, 1.1020),
        # NY_AM
        _bar(2, 13, 0, 1.1020, 1.1060, 1.0950, 1.1030),
        # NY_PM (current session)
        _bar(3, 17, 0, 1.1030, 1.1070, 1.0940, 1.1040),
    ]
    result = tracker.compute(bars, SESSIONS_CFG)
    assert len(result.prior_completed_sessions) <= 2


# ---------------------------------------------------------------------------
# Sweep detection — bullish (swept lows)
# ---------------------------------------------------------------------------


def test_bullish_sweep_detected_when_bar_wicks_below_prior_low_and_closes_above():
    # Asia: low at 1.0950
    asia_bars = [
        _bar(0, 1, 0, 1.1000, 1.1030, 1.0970, 1.1010),
        _bar(1, 2, 0, 1.1010, 1.1040, 1.0950, 1.1015),
    ]
    gap = _bar(2, 6, 30, 1.1015, 1.1020, 1.1010, 1.1015)
    # London bar: wick below 1.0950, closes back above
    sweep_bar = _bar(3, 8, 0, 1.1015, 1.1020, 1.0940, 1.0960)
    bars = asia_bars + [gap, sweep_bar]
    result = TRACKER.compute(bars, SESSIONS_CFG)
    assert result.sweep is not None
    assert result.sweep.direction == Direction.BULLISH
    assert result.sweep.swept_level == pytest.approx(1.0950)
    assert result.sweep.swept_session == Session.ASIA
    assert result.sweep.bar_index == 3


def test_no_bullish_sweep_when_bar_closes_below_prior_low():
    # Bar wicks AND closes below the prior low — continuation, not a sweep.
    asia_bars = [
        _bar(0, 1, 0, 1.1000, 1.1030, 1.0950, 1.1010),
    ]
    gap = _bar(1, 6, 30, 1.1010, 1.1015, 1.1005, 1.1010)
    # London bar: closes BELOW prior low
    continuation_bar = _bar(2, 8, 0, 1.1010, 1.1015, 1.0940, 1.0930)
    bars = asia_bars + [gap, continuation_bar]
    result = TRACKER.compute(bars, SESSIONS_CFG)
    assert result.sweep is None


def test_no_bullish_sweep_when_bar_low_stays_above_prior_low():
    asia_bars = [_bar(0, 1, 0, 1.1000, 1.1030, 1.0950, 1.1010)]
    gap = _bar(1, 6, 30, 1.1010, 1.1015, 1.1005, 1.1010)
    # London bar: never dips to prior low
    normal_bar = _bar(2, 8, 0, 1.1010, 1.1020, 1.0960, 1.1015)
    bars = asia_bars + [gap, normal_bar]
    result = TRACKER.compute(bars, SESSIONS_CFG)
    assert result.sweep is None


# ---------------------------------------------------------------------------
# Sweep detection — bearish (swept highs)
# ---------------------------------------------------------------------------


def test_bearish_sweep_detected_when_bar_wicks_above_prior_high_and_closes_below():
    # Asia: high at 1.1060
    asia_bars = [
        _bar(0, 1, 0, 1.1000, 1.1060, 1.0970, 1.1040),
        _bar(1, 2, 0, 1.1040, 1.1055, 1.0980, 1.1030),
    ]
    gap = _bar(2, 6, 30, 1.1030, 1.1035, 1.1025, 1.1030)
    # London bar: wick above 1.1060, closes back below
    sweep_bar = _bar(3, 8, 0, 1.1030, 1.1070, 1.1010, 1.1050)
    bars = asia_bars + [gap, sweep_bar]
    result = TRACKER.compute(bars, SESSIONS_CFG)
    assert result.sweep is not None
    assert result.sweep.direction == Direction.BEARISH
    assert result.sweep.swept_level == pytest.approx(1.1060)
    assert result.sweep.swept_session == Session.ASIA
    assert result.sweep.bar_index == 3


def test_no_bearish_sweep_when_bar_closes_above_prior_high():
    asia_bars = [_bar(0, 1, 0, 1.1000, 1.1060, 1.0970, 1.1040)]
    gap = _bar(1, 6, 30, 1.1040, 1.1045, 1.1035, 1.1040)
    # London bar: closes ABOVE prior high — continuation, not a sweep
    continuation_bar = _bar(2, 8, 0, 1.1040, 1.1075, 1.1030, 1.1070)
    bars = asia_bars + [gap, continuation_bar]
    result = TRACKER.compute(bars, SESSIONS_CFG)
    assert result.sweep is None


def test_no_sweep_when_no_prior_sessions():
    # Only London bars — no prior completed session to sweep.
    bars = [
        _bar(0, 8,  0, 1.1000, 1.1050, 1.0950, 1.1010),
        _bar(1, 8, 15, 1.1010, 1.1060, 1.0940, 1.1020),
    ]
    result = TRACKER.compute(bars, SESSIONS_CFG)
    assert result.sweep is None


# ---------------------------------------------------------------------------
# Multiple sessions — ordering
# ---------------------------------------------------------------------------


def test_prior_sessions_ordered_most_recent_first():
    # Asia → gap → London → gap → NY_AM → NY_PM (current)
    bars = [
        _bar(0, 1,  0, 1.1000, 1.1030, 1.0970, 1.1010),  # Asia
        _bar(1, 8,  0, 1.1010, 1.1050, 1.0960, 1.1020),  # London
        _bar(2, 13, 0, 1.1020, 1.1060, 1.0950, 1.1030),  # NY_AM
        _bar(3, 17, 0, 1.1030, 1.1070, 1.0940, 1.1040),  # NY_PM (current)
    ]
    result = TRACKER.compute(bars, SESSIONS_CFG)
    sessions = [sr.session for sr in result.prior_completed_sessions]
    # Most recent completed = NY_AM (index 0), then London, then Asia.
    assert sessions[0] == Session.NY_AM
    assert sessions[1] == Session.LONDON
    if len(sessions) > 2:
        assert sessions[2] == Session.ASIA


def test_sweep_uses_most_recent_prior_session():
    # Asia high = 1.1050, London high = 1.1080.
    # Current NY_AM bar sweeps London high (1.1080), not Asia.
    bars = [
        _bar(0, 1, 0,  1.1000, 1.1050, 1.0970, 1.1010),  # Asia
        _bar(1, 8, 0,  1.1010, 1.1080, 1.0960, 1.1020),  # London
        _bar(2, 13, 0, 1.1020, 1.1090, 1.1010, 1.1025),  # NY_AM: wick above 1.1080, close below
    ]
    result = TRACKER.compute(bars, SESSIONS_CFG)
    assert result.sweep is not None
    assert result.sweep.direction == Direction.BEARISH
    assert result.sweep.swept_level == pytest.approx(1.1080)
    assert result.sweep.swept_session == Session.LONDON
