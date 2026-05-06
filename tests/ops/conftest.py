from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.core.enums import Direction, HTFAgreement, Regime, Session, StructureType, Timeframe
from src.core.models import ContextSnapshot, DetectedStructure


@pytest.fixture
def default_config() -> dict:
    return json.loads(Path("src/config/defaults.json").read_text(encoding="utf-8"))


def make_structure(
    structure_type: StructureType,
    direction: Direction,
    *,
    quality: float = 0.8,
    timeframe: Timeframe = Timeframe.M15,
    bar_index: int = 10,
) -> DetectedStructure:
    return DetectedStructure(
        structure_type=structure_type,
        direction=direction,
        price_high=1.1010,
        price_low=1.0990,
        quality=quality,
        age_bars=1,
        atr_relative_size=0.7,
        timeframe=timeframe,
        bar_index=bar_index,
        bar_time=datetime(2026, 4, 30, 8, 0, tzinfo=UTC),
        metadata={},
    )


@pytest.fixture
def make_structure_fn():
    return make_structure


def make_context(
    *,
    trend_m15: Direction = Direction.BULLISH,
    trend_h1: Direction = Direction.BULLISH,
    htf_agreement: HTFAgreement = HTFAgreement.AGREES,
    regime: Regime = Regime.TRENDING,
    spread_atr_ratio: float = 0.1,
    atr_percentile: float = 0.5,
    micro_window: bool = False,
    stale_entry: bool = False,
    news_blocked: bool = False,
) -> ContextSnapshot:
    return ContextSnapshot(
        symbol="EURUSD",
        bar_time=datetime(2026, 4, 30, 8, 0, tzinfo=UTC),
        session=Session.LONDON,
        micro_window=micro_window,
        trend_m15=trend_m15,
        trend_h1=trend_h1,
        htf_agreement=htf_agreement,
        regime=regime,
        atr_current=0.0012,
        atr_percentile=atr_percentile,
        spread_atr_ratio=spread_atr_ratio,
        stale_entry=stale_entry,
        news_blocked=news_blocked,
        nearby_structures=[],
    )


@pytest.fixture
def make_context_fn():
    return make_context
