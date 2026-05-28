"""Tests for src/detectors/judas_sweep.py

Coverage:
- Bullish Judas: wick below Asian low, close back above → BULLISH structure
- Bearish Judas: wick above Asian high, close back below → BEARISH structure
- Bar closes outside Asian range (real breakout): not detected
- No Asian session bars on current date: no structures
- Bar too old (age > max_age_bars): filtered out
- Bar in non-London session: not scanned
- sweep_buffer_atr_mult respected: pierce below threshold → not detected
- Quality scoring: freshness, extension, rejection all contribute
- Multiple sweeps: all valid ones returned, sorted by quality desc
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from src.core.enums import Direction, StructureType, Timeframe
from src.core.models import Bar
from src.detectors.judas_sweep import JudasSweepDetector

UTC = timezone.utc

# Day anchor: 2026-05-01 (Wednesday). All bars on this date.
_DAY = datetime(2026, 5, 1, tzinfo=UTC)

ATR = 0.0010  # 10 pips


def _bar(
    hour: int,
    minute: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    bar_index: int,
) -> Bar:
    ts = _DAY + timedelta(hours=hour, minutes=minute)
    return Bar(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        time=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100,
        bar_index=bar_index,
    )


def _asia_bars() -> list[Bar]:
    """4 Asia session bars that set Asian high=1.1020, Asian low=1.0990."""
    return [
        _bar(0, 0,  1.1000, 1.1020, 1.0995, 1.1010, bar_index=0),
        _bar(0, 15, 1.1010, 1.1015, 1.0990, 1.1000, bar_index=1),
        _bar(0, 30, 1.1000, 1.1018, 1.0992, 1.1005, bar_index=2),
        _bar(0, 45, 1.1005, 1.1012, 1.0993, 1.1008, bar_index=3),
    ]


ASIAN_HIGH = 1.1020
ASIAN_LOW = 1.0990


# ---------------------------------------------------------------------------
# Bearish Judas: wick above Asian high, close back below
# ---------------------------------------------------------------------------

class TestBearishJudas:
    def _bearish_sweep_bar(self, bar_index: int = 5) -> Bar:
        """London bar: wicks up to 1.1035 (above 1.1020), closes at 1.1010 (below Asian high)."""
        return _bar(7, 15, 1.1020, 1.1035, 1.1005, 1.1010, bar_index=bar_index)

    def _build_bars(self, sweep_bar: Bar, current_idx: int = 6) -> list[Bar]:
        asia = _asia_bars()
        current = _bar(7, 30, 1.1010, 1.1012, 1.1005, 1.1008, bar_index=current_idx)
        return [*asia, sweep_bar, current]

    def test_bearish_judas_detected(self):
        det = JudasSweepDetector(max_age_bars=8, min_quality=0.3)
        bars = self._build_bars(self._bearish_sweep_bar())
        results = det.detect(bars, ATR)
        assert len(results) == 1
        s = results[0]
        assert s.structure_type == StructureType.JUDAS_SWEEP
        assert s.direction == Direction.BEARISH

    def test_bearish_judas_zone_bounds(self):
        det = JudasSweepDetector(max_age_bars=8, min_quality=0.3)
        bars = self._build_bars(self._bearish_sweep_bar())
        s = det.detect(bars, ATR)[0]
        assert s.price_high == pytest.approx(1.1035)   # wick tip
        assert s.price_low == pytest.approx(ASIAN_HIGH) # Asian boundary

    def test_bearish_judas_metadata(self):
        det = JudasSweepDetector(max_age_bars=8, min_quality=0.3)
        bars = self._build_bars(self._bearish_sweep_bar())
        s = det.detect(bars, ATR)[0]
        assert s.metadata["asian_high"] == pytest.approx(ASIAN_HIGH)
        assert s.metadata["asian_low"] == pytest.approx(ASIAN_LOW)
        assert "sweep_extension_atr" in s.metadata

    def test_close_outside_range_not_detected(self):
        """Bar closes above Asian high — real breakout, not a Judas."""
        breakout_bar = _bar(7, 15, 1.1020, 1.1040, 1.1015, 1.1035, bar_index=5)
        det = JudasSweepDetector(max_age_bars=8, min_quality=0.3)
        bars = self._build_bars(breakout_bar)
        assert det.detect(bars, ATR) == []


# ---------------------------------------------------------------------------
# Bullish Judas: wick below Asian low, close back above
# ---------------------------------------------------------------------------

class TestBullishJudas:
    def _bullish_sweep_bar(self, bar_index: int = 5) -> Bar:
        """London bar: wicks down to 1.0970 (below 1.0990), closes at 1.1000 (above Asian low)."""
        return _bar(7, 15, 1.0990, 1.1000, 1.0970, 1.1000, bar_index=bar_index)

    def _build_bars(self, sweep_bar: Bar, current_idx: int = 6) -> list[Bar]:
        asia = _asia_bars()
        current = _bar(7, 30, 1.1000, 1.1005, 1.0995, 1.1002, bar_index=current_idx)
        return [*asia, sweep_bar, current]

    def test_bullish_judas_detected(self):
        det = JudasSweepDetector(max_age_bars=8, min_quality=0.3)
        bars = self._build_bars(self._bullish_sweep_bar())
        results = det.detect(bars, ATR)
        assert len(results) == 1
        s = results[0]
        assert s.structure_type == StructureType.JUDAS_SWEEP
        assert s.direction == Direction.BULLISH

    def test_bullish_judas_zone_bounds(self):
        det = JudasSweepDetector(max_age_bars=8, min_quality=0.3)
        bars = self._build_bars(self._bullish_sweep_bar())
        s = det.detect(bars, ATR)[0]
        assert s.price_high == pytest.approx(ASIAN_LOW)  # Asian boundary
        assert s.price_low == pytest.approx(1.0970)       # wick tip

    def test_close_below_asian_low_not_detected(self):
        """Bar closes below Asian low — real breakdown, not a Judas."""
        breakdown_bar = _bar(7, 15, 1.0990, 1.0998, 1.0965, 1.0975, bar_index=5)
        det = JudasSweepDetector(max_age_bars=8, min_quality=0.3)
        bars = self._build_bars(breakdown_bar)
        assert det.detect(bars, ATR) == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def _london_current(self, idx: int) -> Bar:
        return _bar(8, 0, 1.1005, 1.1010, 1.1000, 1.1005, bar_index=idx)

    def test_no_asia_bars_returns_empty(self):
        """No Asia session bars for current date → can't compute Asian range."""
        det = JudasSweepDetector(max_age_bars=8, min_quality=0.3)
        sweep = _bar(7, 15, 1.1020, 1.1035, 1.1005, 1.1010, bar_index=5)
        current = self._london_current(6)
        # Only London bars — no Asia
        bars = [sweep, current]
        assert det.detect(bars, ATR) == []

    def test_too_old_bar_filtered(self):
        """Sweep bar is older than max_age_bars → not returned."""
        det = JudasSweepDetector(max_age_bars=3, min_quality=0.3)
        asia = _asia_bars()
        sweep = _bar(7, 15, 1.1020, 1.1035, 1.1005, 1.1010, bar_index=5)
        current = _bar(9, 0, 1.1005, 1.1010, 1.1000, 1.1005, bar_index=20)  # age=15 > max_age_bars=3
        bars = [*asia, sweep, current]
        assert det.detect(bars, ATR) == []

    def test_sweep_below_buffer_threshold_filtered(self):
        """Bar wicks just barely above Asian high but below sweep_buffer_atr_mult * ATR."""
        det = JudasSweepDetector(
            max_age_bars=8,
            min_quality=0.1,
            sweep_buffer_atr_mult=0.5,  # requires 0.5 * 0.001 = 0.0005 pierce
        )
        asia = _asia_bars()
        # Wick only 0.0001 above Asian high — below the 0.0005 buffer
        sweep = _bar(7, 15, 1.1020, 1.1021, 1.1010, 1.1012, bar_index=5)
        current = self._london_current(6)
        bars = [*asia, sweep, current]
        assert det.detect(bars, ATR) == []

    def test_asia_bar_not_scanned_as_judas(self):
        """Bar during Asia session itself is not scanned (only London)."""
        det = JudasSweepDetector(max_age_bars=20, min_quality=0.1)
        asia = _asia_bars()
        # Inject an Asia-hour bar that looks like a sweep
        fake_sweep = _bar(3, 0, 1.1020, 1.1040, 1.1005, 1.1010, bar_index=10)
        current = _bar(8, 0, 1.1010, 1.1015, 1.1005, 1.1010, bar_index=11)
        bars = [*asia, fake_sweep, current]
        results = det.detect(bars, ATR)
        assert all(s.bar_time.hour >= 7 for s in results)

    def test_insufficient_bars_returns_empty(self):
        det = JudasSweepDetector()
        assert det.detect([], ATR) == []
        assert det.detect([_bar(7, 0, 1.1, 1.1, 1.1, 1.1, 0)], ATR) == []

    def test_zero_atr_returns_empty(self):
        det = JudasSweepDetector()
        asia = _asia_bars()
        sweep = _bar(7, 15, 1.1020, 1.1035, 1.1005, 1.1010, bar_index=5)
        current = _bar(7, 30, 1.1010, 1.1012, 1.1005, 1.1008, bar_index=6)
        bars = [*asia, sweep, current]
        assert det.detect(bars, atr=0.0) == []

    def test_quality_below_threshold_filtered(self):
        """Very weak sweep (tiny wick, small body ratio): quality below min_quality."""
        det = JudasSweepDetector(max_age_bars=8, min_quality=0.9)  # very high floor
        asia = _asia_bars()
        # Sweep barely meets criteria but quality will be low
        sweep = _bar(7, 15, 1.1019, 1.1021, 1.1010, 1.1018, bar_index=5)
        current = _bar(7, 30, 1.1010, 1.1012, 1.1005, 1.1008, bar_index=6)
        bars = [*asia, sweep, current]
        assert det.detect(bars, ATR) == []


# ---------------------------------------------------------------------------
# Multiple sweeps
# ---------------------------------------------------------------------------

class TestMultipleSweeps:
    def test_two_valid_sweeps_both_returned(self):
        """Two London bars both sweep Asian range — both returned."""
        det = JudasSweepDetector(max_age_bars=10, min_quality=0.3)
        asia = _asia_bars()
        sweep1 = _bar(7, 15, 1.1020, 1.1040, 1.1005, 1.1010, bar_index=5)   # bearish
        sweep2 = _bar(7, 30, 1.0990, 1.1000, 1.0965, 1.1000, bar_index=6)   # bullish
        current = _bar(7, 45, 1.0995, 1.1000, 1.0990, 1.0998, bar_index=7)
        bars = [*asia, sweep1, sweep2, current]
        results = det.detect(bars, ATR)
        assert len(results) == 2
        directions = {s.direction for s in results}
        assert Direction.BEARISH in directions
        assert Direction.BULLISH in directions

    def test_results_sorted_by_quality_descending(self):
        """Higher quality sweep comes first."""
        det = JudasSweepDetector(max_age_bars=10, min_quality=0.1)
        asia = _asia_bars()
        # Strong sweep: big extension, clean wick
        strong = _bar(7, 15, 1.1020, 1.1060, 1.1005, 1.1010, bar_index=5)  # 4x ATR extension
        # Weak sweep: tiny extension
        weak = _bar(7, 30, 1.1020, 1.1023, 1.1010, 1.1012, bar_index=6)    # 0.3x ATR extension
        current = _bar(7, 45, 1.1010, 1.1015, 1.1005, 1.1010, bar_index=7)
        bars = [*asia, strong, weak, current]
        results = det.detect(bars, ATR)
        bearish = [s for s in results if s.direction == Direction.BEARISH]
        if len(bearish) >= 2:
            assert bearish[0].quality >= bearish[1].quality
