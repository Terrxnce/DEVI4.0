from __future__ import annotations

from src.core.enums import Direction, HTFAgreement
from src.context.trend import (
    classify_ema_trend,
    classify_htf_agreement,
    ema,
    ema50_delta_slope_norm,
    ema50_slope_normalized,
    higher_timeframe_authority,
)


def test_ema_calculation_uses_standard_multiplier() -> None:
    values = [10.0, 11.0, 12.0]

    series = ema(values, period=3)

    assert series[0] == 10.0
    assert series[1] == 10.5
    assert series[2] == 11.25


def test_ema_trend_classification_bullish_bearish_neutral() -> None:
    bullish_closes = [100 + i * 0.2 for i in range(250)]
    bearish_closes = [100 - i * 0.2 for i in range(250)]
    flat_closes = [100.0 for _ in range(250)]

    bull, _ = classify_ema_trend(bullish_closes, atr=0.5)
    bear, _ = classify_ema_trend(bearish_closes, atr=0.5)
    neutral, _ = classify_ema_trend(flat_closes, atr=0.5)

    assert bull == Direction.BULLISH
    assert bear == Direction.BEARISH
    assert neutral == Direction.NEUTRAL


def test_ema50_slope_normalization() -> None:
    closes = [100 + i * 0.1 for i in range(300)]

    slope = ema50_delta_slope_norm(closes=closes, atr=0.5, lookback=5, period=50)
    legacy_alias = ema50_slope_normalized(closes=closes, atr=0.5, lookback=5, period=50)

    assert slope > 0
    assert slope == legacy_alias
    assert round(slope, 6) == round(slope, 6)


def test_bullish_stack_fails_if_price_below_ema21() -> None:
    closes = [100 + i * 0.2 for i in range(260)]
    closes[-1] = closes[-2] - 6.0

    trend, _ = classify_ema_trend(closes, atr=0.5)

    assert trend == Direction.NEUTRAL


def test_bearish_stack_fails_if_price_above_ema21() -> None:
    closes = [100 - i * 0.2 for i in range(260)]
    closes[-1] = closes[-2] + 6.0

    trend, _ = classify_ema_trend(closes, atr=0.5)

    assert trend == Direction.NEUTRAL


def test_htf_agreement_states() -> None:
    assert classify_htf_agreement(Direction.BULLISH, Direction.BULLISH) == HTFAgreement.AGREES
    assert classify_htf_agreement(Direction.BULLISH, Direction.NEUTRAL) == HTFAgreement.NEUTRAL
    assert classify_htf_agreement(Direction.BULLISH, Direction.BEARISH) == HTFAgreement.CONTRADICTS


def test_h1_direction_keeps_higher_authority() -> None:
    assert higher_timeframe_authority(Direction.BEARISH, Direction.BULLISH) == Direction.BULLISH
    assert higher_timeframe_authority(Direction.BEARISH, Direction.NEUTRAL) == Direction.BEARISH
