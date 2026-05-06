from __future__ import annotations

import pytest

from src.core.enums import Direction, Session, StructureType, Timeframe
from src.core.models import DetectedStructure
from src.context.builder import ContextBuildError, build_context_snapshot


def _structure(index: int, quality: float) -> DetectedStructure:
    from datetime import UTC, datetime

    return DetectedStructure(
        structure_type=StructureType.ORDER_BLOCK,
        direction=Direction.BULLISH,
        price_high=1.1010,
        price_low=1.0990,
        quality=quality,
        age_bars=2,
        atr_relative_size=0.8,
        timeframe=Timeframe.M15,
        bar_index=index,
        bar_time=datetime(2026, 4, 30, 8, 0, tzinfo=UTC),
        metadata={},
    )


def test_context_snapshot_assembly(make_bar_fn, default_config) -> None:
    bars_m15 = [
        make_bar_fn(i, 1.1000 + i * 0.0002, 1.1005 + i * 0.0002, 1.0995 + i * 0.0002, 1.1002 + i * 0.0002)
        for i in range(220)
    ]
    bars_h1 = [
        make_bar_fn(i, 1.1000 + i * 0.0005, 1.1010 + i * 0.0005, 1.0990 + i * 0.0005, 1.1006 + i * 0.0005, timeframe=Timeframe.H1)
        for i in range(220)
    ]
    structures = [_structure(index=5, quality=0.7), _structure(index=3, quality=0.9)]

    snap = build_context_snapshot(
        symbol="EURUSD",
        bars_m15=bars_m15,
        bars_h1=bars_h1,
        detected_structures=structures,
        spread=0.0001,
        config=default_config,
    )

    assert snap.symbol == "EURUSD"
    assert snap.session in (Session.ASIA, Session.LONDON, Session.NY_AM, Session.NY_PM, Session.CLOSED)
    assert snap.trend_h1 in (Direction.BULLISH, Direction.BEARISH, Direction.NEUTRAL)
    assert snap.trend_m15 in (Direction.BULLISH, Direction.BEARISH, Direction.NEUTRAL)
    assert 0.0 <= snap.atr_percentile <= 1.0
    assert len(snap.nearby_structures) == 2
    assert snap.nearby_structures[0].quality >= snap.nearby_structures[1].quality


def test_context_snapshot_deterministic_output(make_bar_fn, default_config) -> None:
    bars_m15 = [
        make_bar_fn(i, 1.2000 + i * 0.0001, 1.2004 + i * 0.0001, 1.1996 + i * 0.0001, 1.2002 + i * 0.0001)
        for i in range(220)
    ]
    bars_h1 = [
        make_bar_fn(i, 1.2000 + i * 0.0002, 1.2008 + i * 0.0002, 1.1992 + i * 0.0002, 1.2005 + i * 0.0002, timeframe=Timeframe.H1)
        for i in range(220)
    ]
    structures = [_structure(index=2, quality=0.8), _structure(index=1, quality=0.8)]

    first = build_context_snapshot(
        symbol="EURUSD",
        bars_m15=bars_m15,
        bars_h1=bars_h1,
        detected_structures=structures,
        spread=0.00015,
        config=default_config,
    )
    second = build_context_snapshot(
        symbol="EURUSD",
        bars_m15=bars_m15,
        bars_h1=bars_h1,
        detected_structures=structures,
        spread=0.00015,
        config=default_config,
    )

    assert first == second


def test_missing_h1_bars_raises_insufficient_data(make_bar_fn, default_config) -> None:
    bars_m15 = [
        make_bar_fn(i, 1.2000 + i * 0.0001, 1.2004 + i * 0.0001, 1.1996 + i * 0.0001, 1.2002 + i * 0.0001)
        for i in range(220)
    ]

    with pytest.raises(ContextBuildError, match="insufficient_data:h1_bars_missing"):
        build_context_snapshot(
            symbol="EURUSD",
            bars_m15=bars_m15,
            bars_h1=[],
            detected_structures=[],
            spread=0.0001,
            config=default_config,
        )
