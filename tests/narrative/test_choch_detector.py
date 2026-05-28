from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.narrative.choch_detector import CHoCHDetector
from src.core.enums import Direction, StructureType, Timeframe
from src.core.models import DetectedStructure


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DETECTOR = CHoCHDetector(min_bos_quality=0.35)


def _bos(bar_index: int, direction: Direction, quality: float = 0.65) -> DetectedStructure:
    ts = datetime(2026, 5, 20, 8, 0, tzinfo=UTC)
    return DetectedStructure(
        structure_type=StructureType.BREAK_OF_STRUCTURE,
        direction=direction,
        price_high=1.1050,
        price_low=1.1050,
        quality=quality,
        age_bars=bar_index,
        atr_relative_size=0.8,
        timeframe=Timeframe.M15,
        bar_index=bar_index,
        bar_time=ts,
        metadata={},
    )


def _fvg(bar_index: int, direction: Direction) -> DetectedStructure:
    ts = datetime(2026, 5, 20, 8, 0, tzinfo=UTC)
    return DetectedStructure(
        structure_type=StructureType.FAIR_VALUE_GAP,
        direction=direction,
        price_high=1.1060,
        price_low=1.1030,
        quality=0.70,
        age_bars=bar_index,
        atr_relative_size=0.6,
        timeframe=Timeframe.M15,
        bar_index=bar_index,
        bar_time=ts,
        metadata={},
    )


# ---------------------------------------------------------------------------
# NEUTRAL direction guard
# ---------------------------------------------------------------------------


def test_neutral_direction_returns_not_detected():
    structures = [_bos(10, Direction.BULLISH)]
    result = DETECTOR.detect(structures, sweep_bar_index=5, direction=Direction.NEUTRAL)
    assert not result.detected
    assert result.reason == "direction_neutral"


# ---------------------------------------------------------------------------
# No qualifying BOS cases
# ---------------------------------------------------------------------------


def test_no_structures_returns_not_detected():
    result = DETECTOR.detect([], sweep_bar_index=5, direction=Direction.BULLISH)
    assert not result.detected
    assert result.reason == "no_bos_after_sweep"


def test_bos_before_sweep_not_counted():
    # BOS at bar 3, sweep at bar 5 — BOS must come AFTER sweep
    structures = [_bos(3, Direction.BULLISH)]
    result = DETECTOR.detect(structures, sweep_bar_index=5, direction=Direction.BULLISH)
    assert not result.detected


def test_bos_at_sweep_bar_not_counted():
    # bar_index == sweep_bar_index should be excluded (must be strictly after)
    structures = [_bos(5, Direction.BULLISH)]
    result = DETECTOR.detect(structures, sweep_bar_index=5, direction=Direction.BULLISH)
    assert not result.detected


def test_bos_wrong_direction_not_counted():
    # BEARISH BOS after sweep, but we're looking for BULLISH reversal
    structures = [_bos(8, Direction.BEARISH)]
    result = DETECTOR.detect(structures, sweep_bar_index=5, direction=Direction.BULLISH)
    assert not result.detected


def test_bos_below_quality_floor_not_counted():
    structures = [_bos(8, Direction.BULLISH, quality=0.20)]
    result = DETECTOR.detect(structures, sweep_bar_index=5, direction=Direction.BULLISH)
    assert not result.detected
    assert result.reason == "no_bos_after_sweep"


def test_non_bos_structure_ignored():
    # FVG after sweep should not count as CHoCH
    structures = [_fvg(8, Direction.BULLISH)]
    result = DETECTOR.detect(structures, sweep_bar_index=5, direction=Direction.BULLISH)
    assert not result.detected


# ---------------------------------------------------------------------------
# Successful detection
# ---------------------------------------------------------------------------


def test_bullish_choch_detected():
    structures = [_bos(8, Direction.BULLISH, quality=0.70)]
    result = DETECTOR.detect(structures, sweep_bar_index=5, direction=Direction.BULLISH)
    assert result.detected
    assert result.direction == Direction.BULLISH
    assert result.bar_index == 8
    assert result.reason == "bos_confirmed_after_sweep"
    assert result.structure is not None


def test_bearish_choch_detected():
    structures = [_bos(12, Direction.BEARISH, quality=0.65)]
    result = DETECTOR.detect(structures, sweep_bar_index=9, direction=Direction.BEARISH)
    assert result.detected
    assert result.direction == Direction.BEARISH
    assert result.bar_index == 12


def test_highest_quality_bos_selected_when_multiple():
    structures = [
        _bos(7, Direction.BULLISH, quality=0.50),
        _bos(9, Direction.BULLISH, quality=0.80),
        _bos(11, Direction.BULLISH, quality=0.60),
    ]
    result = DETECTOR.detect(structures, sweep_bar_index=5, direction=Direction.BULLISH)
    assert result.detected
    assert result.bar_index == 9  # highest quality


def test_mixed_structures_only_bos_after_sweep_counts():
    structures = [
        _bos(3, Direction.BULLISH),          # before sweep
        _fvg(8, Direction.BULLISH),          # FVG — not a BOS
        _bos(8, Direction.BEARISH),          # wrong direction
        _bos(10, Direction.BULLISH, 0.15),   # below quality floor
        _bos(12, Direction.BULLISH, 0.70),   # this is the one
    ]
    result = DETECTOR.detect(structures, sweep_bar_index=5, direction=Direction.BULLISH)
    assert result.detected
    assert result.bar_index == 12
