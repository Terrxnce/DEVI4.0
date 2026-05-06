from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from src.core.enums import InstrumentClass, Timeframe
from src.core.models import Bar, InstrumentProfile
from src.data.base import DataSourceError, utc_datetime


REQUIRED_BAR_COLUMNS = {
    "symbol",
    "timeframe",
    "time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "bar_index",
}


@dataclass(frozen=True)
class CsvDataSource:
    bars_file: str
    profile_file: str | None = None

    def __post_init__(self) -> None:
        bars_path = Path(self.bars_file)
        if not bars_path.exists():
            raise DataSourceError(f"bars_file_not_found:{bars_path}")

        if self.profile_file is not None and not Path(self.profile_file).exists():
            raise DataSourceError(f"profile_file_not_found:{self.profile_file}")

    def fetch_bars(self, symbol: str, timeframe: Timeframe, count: int) -> list[Bar]:
        if count <= 0:
            return []

        rows: list[Bar] = []
        path = Path(self.bars_file)
        with path.open("r", encoding="utf-8-sig", newline="") as fp:
            reader = csv.DictReader(fp)
            if reader.fieldnames is None:
                raise DataSourceError("bars_csv_missing_header")

            missing = REQUIRED_BAR_COLUMNS - set(reader.fieldnames)
            if missing:
                raise DataSourceError(f"bars_csv_missing_columns:{sorted(missing)}")

            for row in reader:
                if row.get("symbol") != symbol:
                    continue
                if row.get("timeframe") != timeframe.value:
                    continue

                rows.append(
                    Bar(
                        symbol=row["symbol"],
                        timeframe=Timeframe(row["timeframe"]),
                        time=utc_datetime(row["time"]),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                        bar_index=int(row["bar_index"]),
                    )
                )

        rows.sort(key=lambda b: (b.time, b.bar_index))
        return rows[-count:]

    def fetch_instrument_profile(self, symbol: str) -> InstrumentProfile:
        path = Path(self.profile_file) if self.profile_file is not None else None

        if path is None:
            return InstrumentProfile(
                symbol=symbol,
                instrument_class=InstrumentClass.FX,
                tick_size=0.00001,
                lot_step=0.01,
                min_lot=0.01,
                max_lot=100.0,
                digits=5,
                point=0.00001,
                contract_size=100000.0,
                noise_floor_atr_mult=0.1,
                spread_warn_atr_mult=0.25,
                stale_entry_atr_mult=0.20,
            )

        with path.open("r", encoding="utf-8-sig", newline="") as fp:
            reader = csv.DictReader(fp)
            if reader.fieldnames is None:
                raise DataSourceError("profiles_csv_missing_header")
            for row in reader:
                if row.get("symbol") != symbol:
                    continue
                return InstrumentProfile(
                    symbol=row["symbol"],
                    instrument_class=InstrumentClass(row.get("instrument_class", "FX")),
                    tick_size=float(row["tick_size"]),
                    lot_step=float(row["lot_step"]),
                    min_lot=float(row["min_lot"]),
                    max_lot=float(row["max_lot"]),
                    digits=int(row["digits"]),
                    point=float(row["point"]),
                    contract_size=float(row["contract_size"]),
                    noise_floor_atr_mult=float(row.get("noise_floor_atr_mult", 0.1)),
                    spread_warn_atr_mult=float(row.get("spread_warn_atr_mult", 0.25)),
                    stale_entry_atr_mult=float(row.get("stale_entry_atr_mult", 0.2)),
                )

        raise DataSourceError(f"instrument_profile_not_found:{symbol}")
