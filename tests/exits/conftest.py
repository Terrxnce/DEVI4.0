from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.context.references import PriceReferenceLevels
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
    high: float = 1.1020,
    low: float = 1.0990,
) -> DetectedStructure:
    return DetectedStructure(
        structure_type=structure_type,
        direction=direction,
        price_high=high,
        price_low=low,
        quality=quality,
        age_bars=1,
        atr_relative_size=0.8,
        timeframe=timeframe,
        bar_index=bar_index,
        bar_time=datetime(2026, 4, 30, 8, 0, tzinfo=UTC),
        metadata={},
    )


@pytest.fixture
def make_structure_fn():
    return make_structure


def make_context(*, regime: Regime = Regime.TRENDING) -> ContextSnapshot:
    return ContextSnapshot(
        symbol="EURUSD",
        bar_time=datetime(2026, 4, 30, 8, 0, tzinfo=UTC),
        session=Session.LONDON,
        micro_window=False,
        trend_m15=Direction.BULLISH,
        trend_h1=Direction.BULLISH,
        htf_agreement=HTFAgreement.AGREES,
        regime=regime,
        atr_current=0.001,
        atr_percentile=0.5,
        spread_atr_ratio=0.1,
        stale_entry=False,
        news_blocked=False,
        nearby_structures=[],
    )


@pytest.fixture
def make_context_fn():
    return make_context


@pytest.fixture
def empty_references() -> PriceReferenceLevels:
    return PriceReferenceLevels(
        prior_day_high=None,
        prior_day_low=None,
        prior_session_high=None,
        prior_session_low=None,
        prominent_swing_high=None,
        prominent_swing_low=None,
    )
