from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from src.core.enums import Timeframe
from src.core.models import Bar, InstrumentProfile


class DataSourceError(Exception):
    pass


class MarketDataSource(Protocol):
    def fetch_bars(self, symbol: str, timeframe: Timeframe, count: int) -> list[Bar]:
        ...

    def fetch_instrument_profile(self, symbol: str) -> InstrumentProfile:
        ...


@dataclass(frozen=True)
class DataFetchContext:
    symbol: str
    timeframe: Timeframe
    count: int
    fetched_at: datetime


@dataclass(frozen=True)
class DataFetchResult:
    context: DataFetchContext
    bars: list[Bar]


def utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(UTC)
