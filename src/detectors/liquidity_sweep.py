from __future__ import annotations

from dataclasses import dataclass

from src.core.enums import Direction, StructureType
from src.core.models import Bar, DetectedStructure
from src.detectors.base import (
    atr_relative,
    build_structure,
    lower_wick,
    structure_sort_key,
    upper_wick,
    body,
)


@dataclass(frozen=True)
class LiquiditySweepDetector:
    max_wick_atr_mult: float = 1.5
    min_wick_body_ratio: float = 2.0
    lookback_bars: int = 20
    max_age_bars: int = 10
    min_quality: float = 0.40

    def detect(self, bars: list[Bar], atr: float, current_bar_index: int | None = None) -> list[DetectedStructure]:
        if len(bars) < 4 or atr <= 0:
            return []

        last = bars[-1]
        current_idx = current_bar_index if current_bar_index is not None else last.bar_index
        structures: list[DetectedStructure] = []

        prior_bars = bars[:-1]
        swing_highs: list[Bar] = []
        swing_lows: list[Bar] = []
        start = max(1, len(prior_bars) - self.lookback_bars)

        for i in range(start, len(prior_bars) - 1):
            center = prior_bars[i]
            prev_bar = prior_bars[i - 1]
            next_bar = prior_bars[i + 1]

            if center.high > prev_bar.high and center.high > next_bar.high:
                swing_highs.append(center)

            if center.low < prev_bar.low and center.low < next_bar.low:
                swing_lows.append(center)

        last_body = max(body(last), 1e-12)

        for swing_low in swing_lows:
            if last.low >= swing_low.low or last.close <= swing_low.low:
                continue

            extension = swing_low.low - last.low
            wick_body_ratio = lower_wick(last) / last_body
            if atr_relative(extension, atr) > self.max_wick_atr_mult or wick_body_ratio < self.min_wick_body_ratio:
                continue

            age = max(current_idx - swing_low.bar_index, 0)
            if age > self.max_age_bars:
                continue

            ext_score = min(atr_relative(extension, atr) / 0.5, 1.0)
            rejection_score = min((wick_body_ratio) / 4.0, 1.0)
            freshness = max(1.0 - (age / self.max_age_bars), 0.0)
            quality = 0.35 * ext_score + 0.40 * rejection_score + 0.25 * freshness
            if quality >= self.min_quality:
                structures.append(
                    build_structure(
                        structure_type=StructureType.LIQUIDITY_SWEEP,
                        direction=Direction.BULLISH,
                        bar=last,
                        price_high=swing_low.low,
                        price_low=last.low,
                        quality=quality,
                        age_bars=int(age),
                        atr_relative_size_value=atr_relative(extension, atr),
                        metadata={"prior_swing_bar_index": swing_low.bar_index},
                    )
                )

        for swing_high in swing_highs:
            if last.high <= swing_high.high or last.close >= swing_high.high:
                continue

            extension = last.high - swing_high.high
            wick_body_ratio = upper_wick(last) / last_body
            if atr_relative(extension, atr) > self.max_wick_atr_mult or wick_body_ratio < self.min_wick_body_ratio:
                continue

            age = max(current_idx - swing_high.bar_index, 0)
            if age > self.max_age_bars:
                continue

            ext_score = min(atr_relative(extension, atr) / 0.5, 1.0)
            rejection_score = min((wick_body_ratio) / 4.0, 1.0)
            freshness = max(1.0 - (age / self.max_age_bars), 0.0)
            quality = 0.35 * ext_score + 0.40 * rejection_score + 0.25 * freshness
            if quality >= self.min_quality:
                structures.append(
                    build_structure(
                        structure_type=StructureType.LIQUIDITY_SWEEP,
                        direction=Direction.BEARISH,
                        bar=last,
                        price_high=last.high,
                        price_low=swing_high.high,
                        quality=quality,
                        age_bars=int(age),
                        atr_relative_size_value=atr_relative(extension, atr),
                        metadata={"prior_swing_bar_index": swing_high.bar_index},
                    )
                )

        return sorted(structures, key=structure_sort_key)
