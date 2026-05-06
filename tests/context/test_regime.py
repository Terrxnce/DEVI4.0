from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.core.models import Bar
from src.core.enums import Regime, Timeframe
from src.context.regime import atr_percentile, classify_regime, simple_atr


def test_simple_atr_uses_arithmetic_mean() -> None:
    base = datetime(2026, 4, 30, 8, 0, tzinfo=UTC)
    bars = [
        Bar(
            symbol="EURUSD",
            timeframe=Timeframe.M15,
            time=base,
            open=0.0,
            high=11.0,
            low=10.0,
            close=10.0,
            volume=0.0,
            bar_index=0,
        ),
        Bar(
            symbol="EURUSD",
            timeframe=Timeframe.M15,
            time=base + timedelta(minutes=15),
            open=0.0,
            high=12.0,
            low=10.0,
            close=11.0,
            volume=0.0,
            bar_index=1,
        ),
        Bar(
            symbol="EURUSD",
            timeframe=Timeframe.M15,
            time=base + timedelta(minutes=30),
            open=0.0,
            high=13.0,
            low=11.0,
            close=12.0,
            volume=0.0,
            bar_index=2,
        ),
    ]

    result = simple_atr(bars, period=3)

    assert result == (1.0 + 2.0 + 2.0) / 3.0


def test_atr_percentile_calculation() -> None:
    history = [0.8, 1.0, 1.2, 1.4]

    pct = atr_percentile(atr_current=1.2, atr_history=history)

    assert pct == 0.75


def test_regime_classification_trending_ranging_expanding_neutral() -> None:
    trending = classify_regime(
        atr_percentile_value=0.60,
        slope_magnitude=0.70,
        trending_threshold=0.65,
        expanding_threshold=0.85,
        ema_stack_aligned=True,
        price_on_correct_side_of_ema21=True,
        price_inside_or_crossing_stack=False,
    )
    ranging = classify_regime(
        atr_percentile_value=0.20,
        slope_magnitude=0.20,
        trending_threshold=0.65,
        expanding_threshold=0.85,
        ema_stack_aligned=False,
        price_on_correct_side_of_ema21=False,
        price_inside_or_crossing_stack=True,
    )
    expanding = classify_regime(
        atr_percentile_value=0.90,
        slope_magnitude=0.70,
        trending_threshold=0.65,
        expanding_threshold=0.85,
        ema_stack_aligned=True,
        price_on_correct_side_of_ema21=True,
        price_inside_or_crossing_stack=False,
    )
    neutral = classify_regime(
        atr_percentile_value=0.50,
        slope_magnitude=0.40,
        trending_threshold=0.65,
        expanding_threshold=0.85,
        ema_stack_aligned=True,
        price_on_correct_side_of_ema21=False,
        price_inside_or_crossing_stack=False,
    )

    assert trending == Regime.TRENDING
    assert ranging == Regime.RANGING
    assert expanding == Regime.EXPANDING
    assert neutral == Regime.NEUTRAL
