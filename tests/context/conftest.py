from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.core.enums import Timeframe
from src.core.models import Bar


def make_bar(
    bar_index: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
    timestamp: datetime | None = None,
    timeframe: Timeframe = Timeframe.M15,
    symbol: str = "EURUSD",
) -> Bar:
    if timestamp is None:
        base = datetime(2026, 4, 30, 8, 0, tzinfo=UTC)
        step = timedelta(minutes=15 if timeframe == Timeframe.M15 else 60)
        timestamp = base + (step * bar_index)
    return Bar(
        symbol=symbol,
        timeframe=timeframe,
        time=timestamp,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        bar_index=bar_index,
    )


@pytest.fixture
def make_bar_fn():
    return make_bar


@pytest.fixture
def default_config() -> dict:
    path = Path("src/config/defaults.json")
    return json.loads(path.read_text(encoding="utf-8"))
