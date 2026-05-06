from __future__ import annotations

from collections.abc import Sequence

from src.core.enums import Regime
from src.core.models import Bar


def true_range(current: Bar, previous_close: float | None) -> float:
    if previous_close is None:
        return current.high - current.low
    return max(current.high - current.low, abs(current.high - previous_close), abs(current.low - previous_close))


def simple_atr(bars: Sequence[Bar], period: int) -> float:
    if period <= 0 or len(bars) < period:
        return 0.0

    window = list(bars)[-period:]
    ranges: list[float] = []
    prev_close: float | None = None
    for bar in window:
        ranges.append(true_range(bar, prev_close))
        prev_close = bar.close
    return sum(ranges) / period


def atr_history_simple(bars: Sequence[Bar], period: int) -> list[float]:
    if period <= 0:
        return []
    history: list[float] = []
    bars_list = list(bars)
    for idx in range(period, len(bars_list) + 1):
        history.append(simple_atr(bars_list[:idx], period))
    return history


def atr_percentile(atr_current: float, atr_history: Sequence[float]) -> float:
    clean_history = [value for value in atr_history if value > 0]
    if atr_current <= 0 or not clean_history:
        return 0.0

    less_or_equal = sum(1 for value in clean_history if value <= atr_current)
    return less_or_equal / len(clean_history)


def classify_regime(
    atr_percentile_value: float,
    slope_magnitude: float,
    trending_threshold: float,
    expanding_threshold: float,
    ema_stack_aligned: bool,
    price_on_correct_side_of_ema21: bool,
    price_inside_or_crossing_stack: bool,
    atr_dead_threshold: float = 0.20,
    ranging_atr_threshold: float = 0.35,
    ranging_slope_threshold: float = 0.30,
) -> Regime:
    if atr_percentile_value >= expanding_threshold:
        return Regime.EXPANDING

    if (
        ema_stack_aligned
        and price_on_correct_side_of_ema21
        and slope_magnitude >= trending_threshold
        and atr_percentile_value > atr_dead_threshold
    ):
        return Regime.TRENDING

    if (
        atr_percentile_value <= ranging_atr_threshold
        and slope_magnitude <= ranging_slope_threshold
        and price_inside_or_crossing_stack
    ):
        return Regime.RANGING

    return Regime.NEUTRAL
