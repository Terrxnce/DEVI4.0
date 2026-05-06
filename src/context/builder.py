from __future__ import annotations

from datetime import time
from typing import Any

from src.core.enums import Direction, Session
from src.core.models import Bar, ContextSnapshot, DetectedStructure
from src.context.regime import atr_history_simple, atr_percentile, classify_regime, simple_atr
from src.context.trend import classify_ema_trend, classify_htf_agreement, ema


class ContextBuildError(ValueError):
    pass


def _parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(hour=int(hh), minute=int(mm))


def classify_session(bar_time, sessions_cfg: dict[str, Any]) -> Session:
    t = bar_time.time()
    for key in (Session.ASIA, Session.LONDON, Session.NY_AM, Session.NY_PM):
        spec = sessions_cfg.get(key.value)
        if not isinstance(spec, dict):
            continue
        start = _parse_hhmm(str(spec.get("start", "00:00")))
        end = _parse_hhmm(str(spec.get("end", "00:00")))
        if start <= t < end:
            return key
    return Session.CLOSED


def build_context_snapshot(
    symbol: str,
    bars_m15: list[Bar],
    bars_h1: list[Bar],
    detected_structures: list[DetectedStructure],
    spread: float,
    config: dict[str, Any],
    micro_window: bool = False,
    stale_entry: bool = False,
    news_blocked: bool = False,
) -> ContextSnapshot:
    if not bars_m15:
        raise ContextBuildError("insufficient_data:bars_m15_missing")
    if not bars_h1:
        raise ContextBuildError("insufficient_data:h1_bars_missing")

    trend_cfg = config["trend"]
    regime_cfg = config["regime"]
    atr_period = int(config["detection"]["atr_period"])

    closes_m15 = [bar.close for bar in bars_m15]
    closes_h1 = [bar.close for bar in bars_h1]

    atr_m15 = simple_atr(bars_m15, atr_period)
    atr_h1 = simple_atr(bars_h1, atr_period)

    trend_m15, slope_m15 = classify_ema_trend(
        closes_m15,
        atr=atr_m15,
        ema_periods=tuple(trend_cfg["ema_periods"]),
        slope_lookback=int(trend_cfg["slope_lookback"]),
        slope_threshold_atr_mult=float(trend_cfg["slope_threshold_atr_mult"]),
    )
    trend_h1, _ = classify_ema_trend(
        closes_h1,
        atr=atr_h1,
        ema_periods=tuple(trend_cfg["ema_periods"]),
        slope_lookback=int(trend_cfg["slope_lookback"]),
        slope_threshold_atr_mult=float(trend_cfg["slope_threshold_atr_mult"]),
    )
    htf_agreement = classify_htf_agreement(trend_m15=trend_m15, trend_h1=trend_h1)

    atr_hist = atr_history_simple(bars_m15, atr_period)
    atr_current = atr_hist[-1] if atr_hist else 0.0
    atr_pct = atr_percentile(atr_current=atr_current, atr_history=atr_hist)

    fast_period, mid_period, slow_period = tuple(trend_cfg["ema_periods"])
    ema21 = ema(closes_m15, fast_period)[-1]
    ema50 = ema(closes_m15, mid_period)[-1]
    ema200 = ema(closes_m15, slow_period)[-1]
    price = closes_m15[-1]

    bullish_stack = ema21 > ema50 > ema200
    bearish_stack = ema21 < ema50 < ema200
    ema_stack_aligned = bullish_stack or bearish_stack
    price_on_correct_side_of_ema21 = (bullish_stack and price > ema21) or (bearish_stack and price < ema21)
    stack_low = min(ema21, ema50, ema200)
    stack_high = max(ema21, ema50, ema200)
    price_inside_or_crossing_stack = stack_low <= price <= stack_high

    regime = classify_regime(
        atr_percentile_value=atr_pct,
        slope_magnitude=abs(slope_m15),
        trending_threshold=float(regime_cfg["trending_threshold"]),
        expanding_threshold=float(regime_cfg["expanding_threshold"]),
        ema_stack_aligned=ema_stack_aligned,
        price_on_correct_side_of_ema21=price_on_correct_side_of_ema21,
        price_inside_or_crossing_stack=price_inside_or_crossing_stack,
    )

    session = classify_session(bars_m15[-1].time, sessions_cfg=config["sessions"])

    spread_atr_ratio = spread / atr_current if atr_current > 0 else 0.0

    return ContextSnapshot(
        symbol=symbol,
        bar_time=bars_m15[-1].time,
        session=session,
        micro_window=micro_window,
        trend_m15=trend_m15,
        trend_h1=trend_h1,
        htf_agreement=htf_agreement,
        regime=regime,
        atr_current=atr_current,
        atr_percentile=atr_pct,
        spread_atr_ratio=spread_atr_ratio,
        stale_entry=stale_entry,
        news_blocked=news_blocked,
        nearby_structures=sorted(
            detected_structures,
            key=lambda item: (-item.quality, item.age_bars, item.bar_index, item.structure_type.value),
        ),
    )
