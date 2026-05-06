from __future__ import annotations

from dataclasses import dataclass

from src.core.enums import Direction, StructureType
from src.core.models import Bar, DetectedStructure
from src.detectors.base import (
    adjusted_max_age,
    atr_relative,
    body,
    build_structure,
    is_bearish,
    is_bullish,
    structure_sort_key,
)


@dataclass(frozen=True)
class OrderBlockDetector:
    min_body_atr_mult: float = 0.4
    max_age_bars: int = 20
    min_quality: float = 0.45

    def detect(self, bars: list[Bar], atr: float, current_bar_index: int | None = None) -> list[DetectedStructure]:
        if len(bars) < 3 or atr <= 0:
            return []

        current_idx = current_bar_index if current_bar_index is not None else bars[-1].bar_index
        max_age = adjusted_max_age(self.max_age_bars, bars[-1].timeframe)

        structures: list[DetectedStructure] = []
        for i in range(1, len(bars) - 1):
            candidate = bars[i]
            displacement = bars[i + 1]
            age = current_idx - candidate.bar_index
            if age > max_age:
                continue

            cand_body = body(candidate)
            disp_body = body(displacement)
            if atr_relative(cand_body, atr) < self.min_body_atr_mult:
                continue
            if atr_relative(disp_body, atr) < self.min_body_atr_mult:
                continue

            direction: Direction | None = None
            price_high = 0.0
            price_low = 0.0

            if is_bearish(candidate) and is_bullish(displacement):
                direction = Direction.BULLISH
                price_high = candidate.open
                price_low = candidate.low
            elif is_bullish(candidate) and is_bearish(displacement):
                direction = Direction.BEARISH
                price_high = candidate.high
                price_low = candidate.open

            if direction is None:
                continue

            body_score = min(atr_relative(cand_body, atr) / 1.0, 1.0)
            disp_score = min(atr_relative(disp_body, atr) / 1.5, 1.0)
            freshness = max(1.0 - (age / max_age), 0.0)
            quality = 0.35 * body_score + 0.40 * disp_score + 0.25 * freshness
            if quality < self.min_quality:
                continue

            structures.append(
                build_structure(
                    structure_type=StructureType.ORDER_BLOCK,
                    direction=direction,
                    bar=candidate,
                    price_high=price_high,
                    price_low=price_low,
                    quality=quality,
                    age_bars=int(age),
                    atr_relative_size_value=atr_relative(abs(price_high - price_low), atr),
                    metadata={"displacement_bar_index": displacement.bar_index},
                )
            )

        return sorted(structures, key=structure_sort_key)
