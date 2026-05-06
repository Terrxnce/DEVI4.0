from __future__ import annotations

from dataclasses import dataclass

from src.core.enums import Direction, StructureType
from src.core.models import Bar, DetectedStructure
from src.detectors.base import atr_relative, build_structure, structure_sort_key


@dataclass(frozen=True)
class BreakOfStructureDetector:
    min_swing_atr_mult: float = 0.5
    lookback_bars: int = 20
    min_quality: float = 0.40

    def detect(self, bars: list[Bar], atr: float) -> list[DetectedStructure]:
        if len(bars) < 4 or atr <= 0:
            return []

        last_bar = bars[-1]
        start = max(1, len(bars) - self.lookback_bars)
        structures: list[DetectedStructure] = []

        for i in range(start, len(bars) - 1):
            center = bars[i]
            prev_bar = bars[i - 1]
            next_bar = bars[i + 1]

            is_swing_high = center.high > prev_bar.high and center.high > next_bar.high
            is_swing_low = center.low < prev_bar.low and center.low < next_bar.low

            if is_swing_high and last_bar.close > center.high:
                break_distance = last_bar.close - center.high
                if atr_relative(break_distance, atr) < self.min_swing_atr_mult:
                    continue
                conviction = min(atr_relative(break_distance, atr) / 0.5, 1.0)
                age = max(last_bar.bar_index - center.bar_index, 0)
                freshness = max(1.0 - age / self.lookback_bars, 0.0)
                quality = min(0.5 * conviction + 0.5 * freshness, 1.0)
                if quality >= self.min_quality:
                    structures.append(
                        build_structure(
                            structure_type=StructureType.BREAK_OF_STRUCTURE,
                            direction=Direction.BULLISH,
                            bar=last_bar,
                            price_high=center.high,
                            price_low=center.high,
                            quality=quality,
                            age_bars=int(age),
                            atr_relative_size_value=atr_relative(break_distance, atr),
                            metadata={"swing_bar_index": center.bar_index, "break_distance": break_distance},
                        )
                    )

            if is_swing_low and last_bar.close < center.low:
                break_distance = center.low - last_bar.close
                if atr_relative(break_distance, atr) < self.min_swing_atr_mult:
                    continue
                conviction = min(atr_relative(break_distance, atr) / 0.5, 1.0)
                age = max(last_bar.bar_index - center.bar_index, 0)
                freshness = max(1.0 - age / self.lookback_bars, 0.0)
                quality = min(0.5 * conviction + 0.5 * freshness, 1.0)
                if quality >= self.min_quality:
                    structures.append(
                        build_structure(
                            structure_type=StructureType.BREAK_OF_STRUCTURE,
                            direction=Direction.BEARISH,
                            bar=last_bar,
                            price_high=center.low,
                            price_low=center.low,
                            quality=quality,
                            age_bars=int(age),
                            atr_relative_size_value=atr_relative(break_distance, atr),
                            metadata={"swing_bar_index": center.bar_index, "break_distance": break_distance},
                        )
                    )

        return sorted(structures, key=structure_sort_key)
