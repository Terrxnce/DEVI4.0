from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from src.core.models import Bar


@dataclass(frozen=True)
class PriceReferenceLevels:
    prior_day_high: float | None
    prior_day_low: float | None
    prior_session_high: float | None
    prior_session_low: float | None
    prominent_swing_high: float | None
    prominent_swing_low: float | None


def _session_tag(bar_time) -> str:
    t = bar_time.time()
    if time(0, 0) <= t < time(7, 0):
        return "ASIA"
    if time(7, 0) <= t < time(12, 0):
        return "LONDON"
    if time(12, 0) <= t < time(16, 0):
        return "NY_AM"
    if time(16, 0) <= t < time(20, 0):
        return "NY_PM"
    return "CLOSED"


def _previous_session_tag(current_tag: str) -> str:
    ordered = ["ASIA", "LONDON", "NY_AM", "NY_PM"]
    if current_tag not in ordered:
        return "NY_PM"
    idx = ordered.index(current_tag)
    return ordered[idx - 1] if idx > 0 else ordered[-1]


def _swing_points(bars: list[Bar]) -> tuple[list[Bar], list[Bar]]:
    swing_highs: list[Bar] = []
    swing_lows: list[Bar] = []
    if len(bars) < 7:
        return (swing_highs, swing_lows)

    for i in range(3, len(bars) - 3):
        center = bars[i]

        if all(center.high > bars[i - j].high and center.high > bars[i + j].high for j in (1, 2, 3)):
            swing_highs.append(center)
        if all(center.low < bars[i - j].low and center.low < bars[i + j].low for j in (1, 2, 3)):
            swing_lows.append(center)

    return (swing_highs, swing_lows)


def compute_reference_levels(bars: list[Bar]) -> PriceReferenceLevels:
    if not bars:
        return PriceReferenceLevels(None, None, None, None, None, None)

    current = bars[-1]
    current_day = current.time.date()

    prior_day_bars = [bar for bar in bars[:-1] if bar.time.date() < current_day]
    prior_day_high = max((bar.high for bar in prior_day_bars), default=None)
    prior_day_low = min((bar.low for bar in prior_day_bars), default=None)

    current_session = _session_tag(current.time)
    previous_session = _previous_session_tag(current_session)
    prior_session_bars = [bar for bar in bars[:-1] if _session_tag(bar.time) == previous_session]
    prior_session_high = max((bar.high for bar in prior_session_bars), default=None)
    prior_session_low = min((bar.low for bar in prior_session_bars), default=None)

    swing_highs, swing_lows = _swing_points(bars[:-1])
    prominent_swing_high = max((bar.high for bar in swing_highs), default=None)
    prominent_swing_low = min((bar.low for bar in swing_lows), default=None)

    return PriceReferenceLevels(
        prior_day_high=prior_day_high,
        prior_day_low=prior_day_low,
        prior_session_high=prior_session_high,
        prior_session_low=prior_session_low,
        prominent_swing_high=prominent_swing_high,
        prominent_swing_low=prominent_swing_low,
    )
