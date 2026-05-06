from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.core.enums import Timeframe
from src.core.models import Bar


@pytest.fixture
def atr_value() -> float:
    return 0.001


def make_bar(
    bar_index: int,
    open_price: float,
    high: float,
    low: float,
    close: float,
    timeframe: Timeframe = Timeframe.M15,
    symbol: str = "EURUSD",
) -> Bar:
    base = datetime(2026, 4, 30, 8, 0, tzinfo=UTC)
    step = timedelta(minutes=15 if timeframe == Timeframe.M15 else 60)
    return Bar(
        symbol=symbol,
        timeframe=timeframe,
        time=base + (step * bar_index),
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        bar_index=bar_index,
    )


def clone_with_index(bar: Bar, bar_index: int) -> Bar:
    return make_bar(
        bar_index=bar_index,
        open_price=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        timeframe=bar.timeframe,
        symbol=bar.symbol,
    )


@pytest.fixture
def make_bar_fn():
    return make_bar


@pytest.fixture
def clone_with_index_fn():
    return clone_with_index
