from __future__ import annotations

from dataclasses import dataclass

from src.core.enums import Direction, StructureType
from src.core.models import Bar, DetectedStructure
from src.detectors.base import (
    atr_relative,
    bar_range,
    body,
    build_structure,
    lower_wick,
    structure_sort_key,
    upper_wick,
)


@dataclass(frozen=True)
class RejectionDetector:
    min_wick_atr_mult: float = 0.5
    min_wick_body_ratio: float = 1.5
    max_age_bars: int = 5
    min_quality: float = 0.40

    def detect(self, bars: list[Bar], atr: float, current_bar_index: int | None = None) -> list[DetectedStructure]:
        if len(bars) < 2 or atr <= 0:
            return []

        last = bars[-1]
        current_idx = current_bar_index if current_bar_index is not None else last.bar_index
        age = max(current_idx - last.bar_index, 0)
        if age > self.max_age_bars:
            return []

        rng = max(bar_range(last), 1e-12)
        body_size = max(body(last), 1e-12)

        lw = lower_wick(last)
        uw = upper_wick(last)

        structures: list[DetectedStructure] = []

        if lw > uw and atr_relative(lw, atr) >= self.min_wick_atr_mult and (lw / body_size) >= self.min_wick_body_ratio:
            close_position = (last.close - last.low) / rng
            wick_score = min(atr_relative(lw, atr) / 1.0, 1.0)
            ratio_score = min((lw / body_size) / 4.0, 1.0)
            position_score = close_position
            freshness = max(1.0 - (age / self.max_age_bars), 0.0)
            quality = 0.30 * wick_score + 0.25 * ratio_score + 0.20 * position_score + 0.25 * freshness
            if quality >= self.min_quality:
                structures.append(
                    build_structure(
                        structure_type=StructureType.REJECTION,
                        direction=Direction.BULLISH,
                        bar=last,
                        price_high=last.high,
                        price_low=last.low,
                        quality=quality,
                        age_bars=int(age),
                        atr_relative_size_value=atr_relative(lw, atr),
                        metadata={"wick_body_ratio": lw / body_size},
                    )
                )

        if uw > lw and atr_relative(uw, atr) >= self.min_wick_atr_mult and (uw / body_size) >= self.min_wick_body_ratio:
            close_position = (last.high - last.close) / rng
            wick_score = min(atr_relative(uw, atr) / 1.0, 1.0)
            ratio_score = min((uw / body_size) / 4.0, 1.0)
            position_score = close_position
            freshness = max(1.0 - (age / self.max_age_bars), 0.0)
            quality = 0.30 * wick_score + 0.25 * ratio_score + 0.20 * position_score + 0.25 * freshness
            if quality >= self.min_quality:
                structures.append(
                    build_structure(
                        structure_type=StructureType.REJECTION,
                        direction=Direction.BEARISH,
                        bar=last,
                        price_high=last.high,
                        price_low=last.low,
                        quality=quality,
                        age_bars=int(age),
                        atr_relative_size_value=atr_relative(uw, atr),
                        metadata={"wick_body_ratio": uw / body_size},
                    )
                )

        return sorted(structures, key=structure_sort_key)
