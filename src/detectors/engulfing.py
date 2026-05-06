from __future__ import annotations

from dataclasses import dataclass

from src.core.enums import Direction, StructureType
from src.core.models import Bar, DetectedStructure
from src.detectors.base import (
    atr_relative,
    body,
    build_structure,
    is_bearish,
    is_bullish,
    structure_sort_key,
)


@dataclass(frozen=True)
class EngulfingDetector:
    min_body_atr_mult: float = 0.3
    max_age_bars: int = 3
    min_quality: float = 0.35

    def detect(self, bars: list[Bar], atr: float, current_bar_index: int | None = None) -> list[DetectedStructure]:
        if len(bars) < 2 or atr <= 0:
            return []

        current_idx = current_bar_index if current_bar_index is not None else bars[-1].bar_index

        structures: list[DetectedStructure] = []
        for i in range(1, len(bars)):
            prev = bars[i - 1]
            curr = bars[i]
            age = max(current_idx - curr.bar_index, 0)
            if age > self.max_age_bars:
                continue

            curr_body = body(curr)
            if atr_relative(curr_body, atr) < self.min_body_atr_mult:
                continue

            prev_body_low = min(prev.open, prev.close)
            prev_body_high = max(prev.open, prev.close)
            curr_body_low = min(curr.open, curr.close)
            curr_body_high = max(curr.open, curr.close)

            engulf_ratio = curr_body / max(body(prev), 1e-12)

            direction: Direction | None = None
            if is_bearish(prev) and is_bullish(curr):
                if curr_body_low <= prev_body_low and curr_body_high >= prev_body_high:
                    direction = Direction.BULLISH
            elif is_bullish(prev) and is_bearish(curr):
                if curr_body_low <= prev_body_low and curr_body_high >= prev_body_high:
                    direction = Direction.BEARISH

            if direction is None:
                continue

            body_score = min(atr_relative(curr_body, atr) / 1.0, 1.0)
            ratio_score = min(engulf_ratio / 3.0, 1.0)
            freshness = max(1.0 - (age / self.max_age_bars), 0.0)
            quality = 0.40 * body_score + 0.30 * ratio_score + 0.30 * freshness
            if quality < self.min_quality:
                continue

            structures.append(
                build_structure(
                    structure_type=StructureType.ENGULFING,
                    direction=direction,
                    bar=curr,
                    price_high=curr.high,
                    price_low=curr.low,
                    quality=quality,
                    age_bars=int(age),
                    atr_relative_size_value=atr_relative(curr_body, atr),
                    metadata={"engulf_ratio": engulf_ratio, "previous_bar_index": prev.bar_index},
                )
            )

        return sorted(structures, key=structure_sort_key)
