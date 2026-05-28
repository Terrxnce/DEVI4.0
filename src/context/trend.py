from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from src.core.enums import Direction, HTFAgreement

if TYPE_CHECKING:
    from src.core.models import Bar


# ---------------------------------------------------------------------------
# EMA utilities — kept for regime classification in builder.py
# ---------------------------------------------------------------------------

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
    """Normalised EMA slope — used by regime classification."""
    if atr <= 0 or lookback <= 0:
        return 0.0
    ema_series = ema(closes, period)
    if len(ema_series) <= lookback:
        return 0.0
    delta = ema_series[-1] - ema_series[-1 - lookback]
    return delta / (atr * lookback)


def ema50_slope_normalized(closes: Sequence[float], atr: float, lookback: int, period: int = 50) -> float:
    """Legacy alias for ema50_delta_slope_norm."""
    return ema50_delta_slope_norm(closes=closes, atr=atr, lookback=lookback, period=period)


# ---------------------------------------------------------------------------
# ADX trend direction
# ---------------------------------------------------------------------------

def _wilder_smooth(values: list[float], period: int) -> list[float]:
    """Wilder's smoothing: initial sum, then rolling (prev - prev/N + new)."""
    if len(values) < period:
        return []
    smoothed: list[float] = [sum(values[:period])]
    for v in values[period:]:
        smoothed.append(smoothed[-1] - smoothed[-1] / period + v)
    return smoothed


def compute_adx(
    bars: Sequence[Bar],
    period: int = 14,
) -> tuple[float, float, float]:
    """Compute (ADX, +DI, -DI) using Wilder's smoothing.

    Returns (0.0, 0.0, 0.0) when bars < 2 * period + 1.
    """
    if len(bars) < period * 2 + 1:
        return (0.0, 0.0, 0.0)

    trs: list[float] = []
    plus_dms: list[float] = []
    minus_dms: list[float] = []

    for i in range(1, len(bars)):
        high = float(bars[i].high)
        low = float(bars[i].low)
        prev_close = float(bars[i - 1].close)
        prev_high = float(bars[i - 1].high)
        prev_low = float(bars[i - 1].low)

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        up_move = high - prev_high
        down_move = prev_low - low

        plus_dm = up_move if (up_move > down_move and up_move > 0.0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0.0) else 0.0

        trs.append(tr)
        plus_dms.append(plus_dm)
        minus_dms.append(minus_dm)

    smoothed_tr = _wilder_smooth(trs, period)
    smoothed_plus_dm = _wilder_smooth(plus_dms, period)
    smoothed_minus_dm = _wilder_smooth(minus_dms, period)

    if not smoothed_tr:
        return (0.0, 0.0, 0.0)

    plus_di_series = [
        100.0 * pm / tr if tr > 0.0 else 0.0
        for pm, tr in zip(smoothed_plus_dm, smoothed_tr)
    ]
    minus_di_series = [
        100.0 * mm / tr if tr > 0.0 else 0.0
        for mm, tr in zip(smoothed_minus_dm, smoothed_tr)
    ]

    dx_series: list[float] = [
        100.0 * abs(pd - md) / (pd + md) if (pd + md) > 0.0 else 0.0
        for pd, md in zip(plus_di_series, minus_di_series)
    ]

    # ADX uses a different smoothing to TR/DM.
    # TR/DM: initialize with sum (scale cancels when computing DI ratio).
    # ADX:   initialize with mean of first period DX values, then apply
    #        adx += (dx - adx) / period — keeps result bounded in 0–100.
    if len(dx_series) < period:
        return (0.0, plus_di_series[-1], minus_di_series[-1])

    adx = sum(dx_series[:period]) / period
    for dx in dx_series[period:]:
        adx += (dx - adx) / period

    return (adx, plus_di_series[-1], minus_di_series[-1])


def classify_adx_trend(
    bars: Sequence[Bar],
    period: int = 14,
    adx_threshold: float = 20.0,
) -> tuple[Direction, float]:
    """Classify trend direction using ADX + Directional Indicators.

    Returns (Direction, adx_value).

    ADX > threshold AND +DI > -DI  -> BULLISH
    ADX > threshold AND -DI > +DI  -> BEARISH
    ADX <= threshold               -> NEUTRAL
    """
    adx, plus_di, minus_di = compute_adx(bars, period)

    if adx > adx_threshold:
        if plus_di > minus_di:
            return (Direction.BULLISH, adx)
        return (Direction.BEARISH, adx)

    return (Direction.NEUTRAL, adx)


# ---------------------------------------------------------------------------
# HTF agreement helpers — unchanged
# ---------------------------------------------------------------------------

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
