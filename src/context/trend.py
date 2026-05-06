from __future__ import annotations

from collections.abc import Sequence

from src.core.enums import Direction, HTFAgreement


def ema(values: Sequence[float], period: int) -> list[float]:
    if period <= 0:
        raise ValueError("period_must_be_positive")
    if not values:
        return []

    multiplier = 2.0 / (period + 1.0)
    series: list[float] = [float(values[0])]
    for value in values[1:]:
        previous = series[-1]
        series.append((float(value) - previous) * multiplier + previous)
    return series


def ema50_delta_slope_norm(closes: Sequence[float], atr: float, lookback: int, period: int = 50) -> float:
    if atr <= 0 or lookback <= 0:
        return 0.0
    ema_series = ema(closes, period)
    if len(ema_series) <= lookback:
        return 0.0

    delta = ema_series[-1] - ema_series[-1 - lookback]
    return delta / (atr * lookback)


def ema50_slope_normalized(closes: Sequence[float], atr: float, lookback: int, period: int = 50) -> float:
    return ema50_delta_slope_norm(closes=closes, atr=atr, lookback=lookback, period=period)


def classify_ema_trend(
    closes: Sequence[float],
    atr: float,
    ema_periods: tuple[int, int, int] = (21, 50, 200),
    slope_lookback: int = 5,
    slope_threshold_atr_mult: float = 0.002,
) -> tuple[Direction, float]:
    if len(closes) < 3 or atr <= 0:
        return (Direction.NEUTRAL, 0.0)

    fast_period, mid_period, slow_period = ema_periods
    ema_fast = ema(closes, fast_period)[-1]
    ema_mid = ema(closes, mid_period)[-1]
    ema_slow = ema(closes, slow_period)[-1]
    price = closes[-1]
    slope = ema50_delta_slope_norm(closes, atr=atr, lookback=slope_lookback, period=mid_period)

    bullish_stack = ema_fast > ema_mid > ema_slow
    bearish_stack = ema_fast < ema_mid < ema_slow
    bullish_price_position = price > ema_fast
    bearish_price_position = price < ema_fast

    if bullish_stack and bullish_price_position and slope >= slope_threshold_atr_mult:
        return (Direction.BULLISH, slope)
    if bearish_stack and bearish_price_position and slope <= -slope_threshold_atr_mult:
        return (Direction.BEARISH, slope)
    return (Direction.NEUTRAL, slope)


def classify_htf_agreement(trend_m15: Direction, trend_h1: Direction) -> HTFAgreement:
    if trend_h1 == Direction.NEUTRAL or trend_m15 == Direction.NEUTRAL:
        return HTFAgreement.NEUTRAL
    if trend_h1 == trend_m15:
        return HTFAgreement.AGREES
    return HTFAgreement.CONTRADICTS


def higher_timeframe_authority(trend_m15: Direction, trend_h1: Direction) -> Direction:
    if trend_h1 != Direction.NEUTRAL:
        return trend_h1
    return trend_m15
