from __future__ import annotations

import math
from datetime import UTC, datetime

from src.core.enums import Direction, HTFAgreement, Timeframe
from src.core.models import Bar
from src.context.trend import (
    classify_adx_trend,
    classify_htf_agreement,
    compute_adx,
    ema,
    ema50_delta_slope_norm,
    ema50_slope_normalized,
    higher_timeframe_authority,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bars(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
) -> list[Bar]:
    bars = []
    for i, c in enumerate(closes):
        h = highs[i] if highs else c + 0.0005
        lo = lows[i] if lows else c - 0.0005
        bars.append(Bar(
            symbol="EURUSD",
            timeframe=Timeframe.H1,
            time=datetime.now(tz=UTC),
            open=c,
            high=h,
            low=lo,
            close=c,
            volume=1000.0,
            bar_index=i,
        ))
    return bars


def _trending_bars(n: int = 60, *, direction: str = "up", step: float = 0.001) -> list[Bar]:
    base = 1.1000
    closes, highs, lows = [], [], []
    for i in range(n):
        c = base + i * step if direction == "up" else base - i * step
        closes.append(c)
        highs.append(c + 0.0003)
        lows.append(c - 0.0003)
    return _make_bars(closes, highs, lows)


def _flat_bars(n: int = 60) -> list[Bar]:
    # Identical bars: zero directional movement on every bar.
    # up_move = high[i] - high[i-1] = 0, down_move = low[i-1] - low[i] = 0
    # so +DM = -DM = 0, DX = 0, ADX = 0 regardless of n or period.
    # This is the only construction guaranteed to give NEUTRAL without
    # being sensitive to where the series happens to end.
    closes = [1.1000] * n
    highs = [1.1005] * n
    lows = [1.0995] * n
    return _make_bars(closes, highs, lows)


# ---------------------------------------------------------------------------
# EMA utilities (still used by regime)
# ---------------------------------------------------------------------------

def test_ema_calculation_uses_standard_multiplier() -> None:
    values = [10.0, 11.0, 12.0]
    series = ema(values, period=3)
    assert series[0] == 10.0
    assert series[1] == 10.5
    assert series[2] == 11.25


def test_ema50_slope_normalized_alias_matches() -> None:
    closes = [100 + i * 0.1 for i in range(300)]
    slope = ema50_delta_slope_norm(closes=closes, atr=0.5, lookback=5, period=50)
    alias = ema50_slope_normalized(closes=closes, atr=0.5, lookback=5, period=50)
    assert slope > 0
    assert slope == alias


# ---------------------------------------------------------------------------
# compute_adx
# ---------------------------------------------------------------------------

def test_compute_adx_returns_zero_when_insufficient_bars() -> None:
    bars = _trending_bars(n=10)
    adx, plus_di, minus_di = compute_adx(bars, period=14)
    assert adx == 0.0
    assert plus_di == 0.0
    assert minus_di == 0.0


def test_compute_adx_bullish_trend_plus_di_dominates() -> None:
    bars = _trending_bars(n=80, direction="up", step=0.001)
    adx, plus_di, minus_di = compute_adx(bars, period=14)
    assert adx > 0.0
    assert plus_di > minus_di, f"+DI {plus_di:.2f} should exceed -DI {minus_di:.2f} in uptrend"


def test_compute_adx_bearish_trend_minus_di_dominates() -> None:
    bars = _trending_bars(n=80, direction="down", step=0.001)
    adx, plus_di, minus_di = compute_adx(bars, period=14)
    assert adx > 0.0
    assert minus_di > plus_di, f"-DI {minus_di:.2f} should exceed +DI {plus_di:.2f} in downtrend"


def test_compute_adx_strong_trend_exceeds_threshold() -> None:
    bars = _trending_bars(n=80, direction="up", step=0.002)
    adx, _, _ = compute_adx(bars, period=14)
    assert adx > 20.0, f"Strong trend ADX should exceed 20, got {adx:.2f}"


# ---------------------------------------------------------------------------
# classify_adx_trend
# ---------------------------------------------------------------------------

def test_classify_adx_trend_bullish() -> None:
    bars = _trending_bars(n=80, direction="up", step=0.002)
    direction, adx_val = classify_adx_trend(bars, period=14, adx_threshold=20.0)
    assert direction == Direction.BULLISH
    assert adx_val > 20.0


def test_classify_adx_trend_bearish() -> None:
    bars = _trending_bars(n=80, direction="down", step=0.002)
    direction, adx_val = classify_adx_trend(bars, period=14, adx_threshold=20.0)
    assert direction == Direction.BEARISH
    assert adx_val > 20.0


def test_classify_adx_trend_neutral_when_flat() -> None:
    bars = _flat_bars(n=80)
    direction, _ = classify_adx_trend(bars, period=14, adx_threshold=20.0)
    assert direction == Direction.NEUTRAL


def test_classify_adx_trend_neutral_when_insufficient_bars() -> None:
    bars = _trending_bars(n=10, direction="up")
    direction, adx_val = classify_adx_trend(bars, period=14, adx_threshold=20.0)
    assert direction == Direction.NEUTRAL
    assert adx_val == 0.0


def test_classify_adx_trend_neutral_at_extreme_threshold() -> None:
    bars = _trending_bars(n=80, direction="up", step=0.001)
    direction, _ = classify_adx_trend(bars, period=14, adx_threshold=999.0)
    assert direction == Direction.NEUTRAL


# ---------------------------------------------------------------------------
# HTF agreement helpers
# ---------------------------------------------------------------------------

def test_htf_agreement_states() -> None:
    assert classify_htf_agreement(Direction.BULLISH, Direction.BULLISH) == HTFAgreement.AGREES
    assert classify_htf_agreement(Direction.BULLISH, Direction.NEUTRAL) == HTFAgreement.NEUTRAL
    assert classify_htf_agreement(Direction.BULLISH, Direction.BEARISH) == HTFAgreement.CONTRADICTS


def test_h1_direction_keeps_higher_authority() -> None:
    assert higher_timeframe_authority(Direction.BEARISH, Direction.BULLISH) == Direction.BULLISH
    assert higher_timeframe_authority(Direction.BEARISH, Direction.NEUTRAL) == Direction.BEARISH
