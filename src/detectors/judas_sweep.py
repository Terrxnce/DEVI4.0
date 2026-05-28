"""Judas Sweep detector — Asian range sweep + London reversal.

A Judas Sweep occurs when price runs liquidity above the Asian session high
or below the Asian session low at London open, then closes back inside the
Asian range. It signals a manipulation sweep before the real intraday move.

Detection logic:
- Compute Asian range (00:00–07:00 UTC) from bars on the current trading day.
- Scan London session bars (07:00–12:00 UTC) for wicks that pierce beyond
  the Asian high/low by at least sweep_buffer_atr_mult * ATR.
- Bar must CLOSE back inside the Asian range (wick sweep, not a breakout).
- Quality scored from sweep extension, wick/body rejection ratio, freshness.

Structure fields:
- direction: BEARISH if swept Asian high, BULLISH if swept Asian low.
- price_high / price_low: the sweep zone (Asian boundary → wick extreme).
- metadata: asian_high, asian_low, sweep_extension_atr.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from src.core.enums import Direction, StructureType
from src.core.models import Bar, DetectedStructure
from src.detectors.base import (
    atr_relative,
    body,
    build_structure,
    lower_wick,
    structure_sort_key,
    upper_wick,
)

_ASIA_START = time(0, 0)
_ASIA_END = time(7, 0)
_LONDON_START = time(7, 0)
_LONDON_END = time(12, 0)


def _is_asia_bar(bar: Bar) -> bool:
    t = bar.time.time()
    return _ASIA_START <= t < _ASIA_END


def _is_london_bar(bar: Bar) -> bool:
    t = bar.time.time()
    return _LONDON_START <= t < _LONDON_END


@dataclass(frozen=True)
class JudasSweepDetector:
    """Detect Asian range Judas Sweeps at London open.

    Parameters
    ----------
    max_age_bars:
        How many bars ago the sweep bar can be and still be valid.
        Keep this tight (default 8 = 2 hours on M15) — stale sweeps lose edge.
    min_quality:
        Minimum quality score [0, 1] to emit a structure.
    sweep_buffer_atr_mult:
        Minimum wick pierce beyond the Asian boundary as a multiple of ATR.
        Filters out noise; 0.1 ATR is a small but meaningful pierce.
    """

    max_age_bars: int = 8
    min_quality: float = 0.45
    sweep_buffer_atr_mult: float = 0.1

    def detect(
        self,
        bars: list[Bar],
        atr: float,
        current_bar_index: int | None = None,
    ) -> list[DetectedStructure]:
        if len(bars) < 3 or atr <= 0:
            return []

        last = bars[-1]
        current_idx = current_bar_index if current_bar_index is not None else last.bar_index

        # Asian range: today only (same date as most-recent bar).
        current_date = last.time.date()
        asia_bars = [b for b in bars[:-1] if _is_asia_bar(b) and b.time.date() == current_date]
        if not asia_bars:
            return []

        asian_high = max(b.high for b in asia_bars)
        asian_low = min(b.low for b in asia_bars)
        if asian_high <= asian_low:
            return []

        min_pierce = atr * self.sweep_buffer_atr_mult
        structures: list[DetectedStructure] = []

        for bar in bars[:-1]:
            if not _is_london_bar(bar):
                continue

            age = max(current_idx - bar.bar_index, 0)
            if age > self.max_age_bars:
                continue

            bar_body = max(body(bar), 1e-12)

            # Bearish Judas: wick above Asian high, close back below Asian high.
            if bar.high > asian_high + min_pierce and bar.close < asian_high:
                sweep_ext = bar.high - asian_high
                wick = upper_wick(bar)
                wick_body_ratio = wick / bar_body
                ext_score = min(atr_relative(sweep_ext, atr) / 0.5, 1.0)
                rejection_score = min(wick_body_ratio / 3.0, 1.0)
                freshness = max(1.0 - (age / max(self.max_age_bars, 1)), 0.0)
                quality = 0.35 * ext_score + 0.40 * rejection_score + 0.25 * freshness
                if quality >= self.min_quality:
                    structures.append(
                        build_structure(
                            structure_type=StructureType.JUDAS_SWEEP,
                            direction=Direction.BEARISH,
                            bar=bar,
                            price_high=bar.high,
                            price_low=asian_high,
                            quality=quality,
                            age_bars=age,
                            atr_relative_size_value=atr_relative(sweep_ext, atr),
                            metadata={
                                "asian_high": asian_high,
                                "asian_low": asian_low,
                                "sweep_extension_atr": round(atr_relative(sweep_ext, atr), 4),
                            },
                        )
                    )

            # Bullish Judas: wick below Asian low, close back above Asian low.
            if bar.low < asian_low - min_pierce and bar.close > asian_low:
                sweep_ext = asian_low - bar.low
                wick = lower_wick(bar)
                wick_body_ratio = wick / bar_body
                ext_score = min(atr_relative(sweep_ext, atr) / 0.5, 1.0)
                rejection_score = min(wick_body_ratio / 3.0, 1.0)
                freshness = max(1.0 - (age / max(self.max_age_bars, 1)), 0.0)
                quality = 0.35 * ext_score + 0.40 * rejection_score + 0.25 * freshness
                if quality >= self.min_quality:
                    structures.append(
                        build_structure(
                            structure_type=StructureType.JUDAS_SWEEP,
                            direction=Direction.BULLISH,
                            bar=bar,
                            price_high=asian_low,
                            price_low=bar.low,
                            quality=quality,
                            age_bars=age,
                            atr_relative_size_value=atr_relative(sweep_ext, atr),
                            metadata={
                                "asian_high": asian_high,
                                "asian_low": asian_low,
                                "sweep_extension_atr": round(atr_relative(sweep_ext, atr), 4),
                            },
                        )
                    )

        return sorted(structures, key=structure_sort_key)
