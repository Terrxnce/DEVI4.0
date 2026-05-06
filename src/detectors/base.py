from __future__ import annotations

from dataclasses import dataclass

from src.core.enums import Direction, StructureType, Timeframe
from src.core.models import Bar, DetectedStructure


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def atr_relative(value: float, atr: float) -> float:
    if atr <= 0:
        return float("inf")
    return value / atr


def body(bar: Bar) -> float:
    return abs(bar.close - bar.open)


def bar_range(bar: Bar) -> float:
    return bar.high - bar.low


def is_bullish(bar: Bar) -> bool:
    return bar.close > bar.open


def is_bearish(bar: Bar) -> bool:
    return bar.close < bar.open


def upper_wick(bar: Bar) -> float:
    return bar.high - max(bar.open, bar.close)


def lower_wick(bar: Bar) -> float:
    return min(bar.open, bar.close) - bar.low


def adjusted_max_age(base_age: int, timeframe: Timeframe) -> float:
    if timeframe == Timeframe.H1:
        return base_age * 2.5
    return float(base_age)


def structure_sort_key(item: DetectedStructure) -> tuple[float, int, int, str]:
    return (-item.quality, item.age_bars, item.bar_index, item.structure_type.value)


@dataclass(frozen=True)
class DetectorResult:
    structures: list[DetectedStructure]


def build_structure(
    structure_type: StructureType,
    direction: Direction,
    bar: Bar,
    price_high: float,
    price_low: float,
    quality: float,
    age_bars: int,
    atr_relative_size_value: float,
    metadata: dict,
) -> DetectedStructure:
    return DetectedStructure(
        structure_type=structure_type,
        direction=direction,
        price_high=price_high,
        price_low=price_low,
        quality=round(clamp(quality, 0.0, 1.0), 4),
        age_bars=age_bars,
        atr_relative_size=atr_relative_size_value,
        timeframe=bar.timeframe,
        bar_index=bar.bar_index,
        bar_time=bar.time,
        metadata=metadata,
    )
