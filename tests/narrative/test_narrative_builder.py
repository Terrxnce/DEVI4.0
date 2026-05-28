from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.context.session_levels import SessionLevels, SessionRange, SessionSweep
from src.narrative.narrative_builder import NarrativeBuilder
from src.core.enums import Direction, Session, StructureType, Timeframe
from src.core.models import DetectedStructure


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BUILDER = NarrativeBuilder(min_fvg_quality=0.35, retrace_tolerance_atr=0.15)
ATR = 0.0010  # 10 pips equivalent


def _ts(h: int = 8, m: int = 0) -> datetime:
    return datetime(2026, 5, 20, h, m, tzinfo=UTC)


def _sweep(direction: Direction, bar_index: int = 5, swept_level: float = 1.0950) -> SessionSweep:
    return SessionSweep(
        direction=direction,
        swept_level=swept_level,
        swept_session=Session.ASIA,
        bar_index=bar_index,
        bar_time=_ts(8, 0),
    )


def _fvg(
    bar_index: int,
    direction: Direction,
    price_low: float,
    price_high: float,
    quality: float = 0.70,
) -> DetectedStructure:
    return DetectedStructure(
        structure_type=StructureType.FAIR_VALUE_GAP,
        direction=direction,
        price_high=price_high,
        price_low=price_low,
        quality=quality,
        age_bars=0,
        atr_relative_size=0.6,
        timeframe=Timeframe.M15,
        bar_index=bar_index,
        bar_time=_ts(8, 15),
        metadata={},
    )


def _bos(bar_index: int, direction: Direction, quality: float = 0.65) -> DetectedStructure:
    return DetectedStructure(
        structure_type=StructureType.BREAK_OF_STRUCTURE,
        direction=direction,
        price_high=1.1060,
        price_low=1.1060,
        quality=quality,
        age_bars=0,
        atr_relative_size=0.8,
        timeframe=Timeframe.M15,
        bar_index=bar_index,
        bar_time=_ts(8, 30),
        metadata={},
    )


def _session_levels(sweep: SessionSweep | None = None) -> SessionLevels:
    return SessionLevels(
        current_session=Session.LONDON,
        current_session_high=1.1080,
        current_session_low=1.0960,
        prior_completed_sessions=[
            SessionRange(
                session=Session.ASIA,
                high=1.1050,
                low=1.0950,
                start_bar_index=0,
                end_bar_index=4,
                start_time=_ts(1, 0),
                end_time=_ts(6, 0),
            )
        ],
        sweep=sweep,
    )


# ---------------------------------------------------------------------------
# No sweep = None returned
# ---------------------------------------------------------------------------


def test_returns_none_when_no_sweep():
    levels = _session_levels(sweep=None)
    result = BUILDER.evaluate(levels, [], current_price=1.1000, atr=ATR)
    assert result is None


# ---------------------------------------------------------------------------
# No qualifying FVG = None returned
# ---------------------------------------------------------------------------


def test_returns_none_when_no_post_sweep_fvg():
    sweep = _sweep(Direction.BULLISH, bar_index=5)
    levels = _session_levels(sweep=sweep)
    # Only a FVG that existed BEFORE the sweep — should be ignored
    pre_sweep_fvg = _fvg(bar_index=2, direction=Direction.BULLISH, price_low=1.0960, price_high=1.0980)
    result = BUILDER.evaluate(levels, [pre_sweep_fvg], current_price=1.0970, atr=ATR)
    assert result is None


def test_returns_none_when_fvg_quality_too_low():
    sweep = _sweep(Direction.BULLISH, bar_index=5)
    levels = _session_levels(sweep=sweep)
    low_quality_fvg = _fvg(bar_index=6, direction=Direction.BULLISH, price_low=1.0960, price_high=1.0980, quality=0.20)
    result = BUILDER.evaluate(levels, [low_quality_fvg], current_price=1.0970, atr=ATR)
    assert result is None


def test_returns_none_when_fvg_wrong_direction():
    sweep = _sweep(Direction.BULLISH, bar_index=5)
    levels = _session_levels(sweep=sweep)
    # Bearish FVG after a bullish sweep — wrong direction
    wrong_dir_fvg = _fvg(bar_index=6, direction=Direction.BEARISH, price_low=1.0960, price_high=1.0980)
    result = BUILDER.evaluate(levels, [wrong_dir_fvg], current_price=1.0970, atr=ATR)
    assert result is None


# ---------------------------------------------------------------------------
# Sequence partial — FVG present but no retrace
# ---------------------------------------------------------------------------


def test_sequence_incomplete_when_price_outside_fvg():
    sweep = _sweep(Direction.BULLISH, bar_index=5)
    levels = _session_levels(sweep=sweep)
    fvg = _fvg(bar_index=6, direction=Direction.BULLISH, price_low=1.0960, price_high=1.0980)
    # Price at 1.1010 is well above the FVG zone [1.0960, 1.0980]
    result = BUILDER.evaluate(levels, [fvg], current_price=1.1010, atr=ATR)
    assert result is not None
    assert result.sequence_complete is False
    assert result.retrace_confirmed is False


# ---------------------------------------------------------------------------
# Complete sequence — bullish sweep reversal
# ---------------------------------------------------------------------------


def test_bullish_sweep_reversal_complete_sequence():
    sweep = _sweep(Direction.BULLISH, bar_index=5, swept_level=1.0950)
    levels = _session_levels(sweep=sweep)
    fvg = _fvg(bar_index=6, direction=Direction.BULLISH, price_low=1.0960, price_high=1.0980)
    # Price retracing into the FVG zone
    result = BUILDER.evaluate(levels, [fvg], current_price=1.0970, atr=ATR)
    assert result is not None
    assert result.sequence_complete is True
    assert result.retrace_confirmed is True
    assert result.direction == Direction.BULLISH
    assert result.sweep is sweep
    assert result.fvg_zone is fvg


def test_bearish_sweep_reversal_complete_sequence():
    sweep = _sweep(Direction.BEARISH, bar_index=5, swept_level=1.1060)
    levels = _session_levels(sweep=sweep)
    fvg = _fvg(bar_index=6, direction=Direction.BEARISH, price_low=1.1020, price_high=1.1040)
    # Price retracing into the bearish FVG zone (came back up after sell-off)
    result = BUILDER.evaluate(levels, [fvg], current_price=1.1030, atr=ATR)
    assert result is not None
    assert result.sequence_complete is True
    assert result.direction == Direction.BEARISH


# ---------------------------------------------------------------------------
# CHoCH integration
# ---------------------------------------------------------------------------


def test_choch_detected_when_bos_after_sweep():
    sweep = _sweep(Direction.BULLISH, bar_index=5)
    levels = _session_levels(sweep=sweep)
    fvg = _fvg(bar_index=6, direction=Direction.BULLISH, price_low=1.0960, price_high=1.0980)
    bos = _bos(bar_index=8, direction=Direction.BULLISH, quality=0.70)
    result = BUILDER.evaluate(levels, [fvg, bos], current_price=1.0970, atr=ATR)
    assert result is not None
    assert result.choch.detected is True


def test_choch_not_detected_without_bos_after_sweep():
    sweep = _sweep(Direction.BULLISH, bar_index=5)
    levels = _session_levels(sweep=sweep)
    fvg = _fvg(bar_index=6, direction=Direction.BULLISH, price_low=1.0960, price_high=1.0980)
    result = BUILDER.evaluate(levels, [fvg], current_price=1.0970, atr=ATR)
    assert result is not None
    assert result.choch.detected is False


# ---------------------------------------------------------------------------
# Quality scoring
# ---------------------------------------------------------------------------


def test_quality_higher_with_choch():
    sweep = _sweep(Direction.BULLISH, bar_index=5)
    levels = _session_levels(sweep=sweep)
    fvg = _fvg(bar_index=6, direction=Direction.BULLISH, price_low=1.0960, price_high=1.0980)
    bos = _bos(bar_index=8, direction=Direction.BULLISH, quality=0.70)

    without_choch = BUILDER.evaluate(levels, [fvg], current_price=1.0970, atr=ATR)
    with_choch = BUILDER.evaluate(levels, [fvg, bos], current_price=1.0970, atr=ATR)

    assert with_choch is not None and without_choch is not None
    assert with_choch.quality > without_choch.quality


def test_quality_bounded_zero_to_one():
    sweep = _sweep(Direction.BULLISH, bar_index=5)
    levels = _session_levels(sweep=sweep)
    fvg = _fvg(bar_index=6, direction=Direction.BULLISH, price_low=1.0960, price_high=1.0980, quality=1.0)
    bos = _bos(bar_index=8, direction=Direction.BULLISH, quality=1.0)
    result = BUILDER.evaluate(levels, [fvg, bos], current_price=1.0970, atr=ATR)
    assert result is not None
    assert 0.0 <= result.quality <= 1.0


# ---------------------------------------------------------------------------
# Retrace tolerance
# ---------------------------------------------------------------------------


def test_price_just_outside_fvg_within_tolerance_counts_as_retrace():
    # tolerance = 0.15 * ATR(0.001) = 0.00015
    sweep = _sweep(Direction.BULLISH, bar_index=5)
    levels = _session_levels(sweep=sweep)
    fvg = _fvg(bar_index=6, direction=Direction.BULLISH, price_low=1.0960, price_high=1.0980)
    # Price at 1.0960 - 0.0001 = 1.0959, just barely below the zone but within tolerance
    price_just_below = fvg.price_low - 0.00010
    result = BUILDER.evaluate(levels, [fvg], current_price=price_just_below, atr=ATR)
    assert result is not None
    assert result.retrace_confirmed is True  # within tolerance


def test_price_far_outside_fvg_beyond_tolerance_not_retrace():
    sweep = _sweep(Direction.BULLISH, bar_index=5)
    levels = _session_levels(sweep=sweep)
    fvg = _fvg(bar_index=6, direction=Direction.BULLISH, price_low=1.0960, price_high=1.0980)
    # Price at 1.1010 — far above the FVG zone (above price_high by 30 pips)
    result = BUILDER.evaluate(levels, [fvg], current_price=1.1010, atr=ATR)
    assert result is not None
    assert result.retrace_confirmed is False


# ---------------------------------------------------------------------------
# Multiple FVGs — highest quality selected
# ---------------------------------------------------------------------------


def test_highest_quality_post_sweep_fvg_selected():
    sweep = _sweep(Direction.BULLISH, bar_index=5)
    levels = _session_levels(sweep=sweep)
    fvg_low = _fvg(bar_index=6, direction=Direction.BULLISH, price_low=1.0958, price_high=1.0970, quality=0.45)
    fvg_high = _fvg(bar_index=7, direction=Direction.BULLISH, price_low=1.0960, price_high=1.0980, quality=0.80)
    result = BUILDER.evaluate(levels, [fvg_low, fvg_high], current_price=1.0970, atr=ATR)
    assert result is not None
    assert result.fvg_zone.quality == pytest.approx(0.80)
