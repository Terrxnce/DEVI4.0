from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from src.core.enums import InstrumentClass, Timeframe
from src.core.models import Bar, InstrumentProfile
from src.data.base import DataSourceError


class MT5ClientProtocol(Protocol):
    TIMEFRAME_M15: Any
    TIMEFRAME_H1: Any

    def initialize(self) -> bool:
        ...

    def shutdown(self) -> None:
        ...

    def copy_rates_from_pos(self, symbol: str, timeframe: Any, start_pos: int, count: int) -> Any:
        ...

    def symbol_info(self, symbol: str) -> Any:
        ...

    def account_info(self) -> Any:
        ...

    def symbol_info_tick(self, symbol: str) -> Any:
        ...


@dataclass
class MT5DataSource:
    mt5_client: MT5ClientProtocol | None = None
    initialized: bool = False

    def __post_init__(self) -> None:
        if self.mt5_client is None:
            try:
                import MetaTrader5 as mt5  # type: ignore
            except ImportError as exc:
                raise DataSourceError("mt5_library_not_installed") from exc
            self.mt5_client = mt5

        assert self.mt5_client is not None
        if not self.mt5_client.initialize():
            raise DataSourceError("mt5_initialize_failed")
        self.initialized = True

    def close(self) -> None:
        assert self.mt5_client is not None
        self.mt5_client.shutdown()

    def _to_mt5_tf(self, timeframe: Timeframe) -> Any:
        assert self.mt5_client is not None
        if timeframe == Timeframe.M15:
            return self.mt5_client.TIMEFRAME_M15
        if timeframe == Timeframe.H1:
            return self.mt5_client.TIMEFRAME_H1
        raise DataSourceError(f"unsupported_timeframe:{timeframe.value}")

    def fetch_bars(self, symbol: str, timeframe: Timeframe, count: int) -> list[Bar]:
        if count <= 0:
            return []

        assert self.mt5_client is not None
        rates = self.mt5_client.copy_rates_from_pos(
            symbol,
            self._to_mt5_tf(timeframe),
            0,
            count,
        )

        if rates is None:
            raise DataSourceError(f"mt5_rates_unavailable:{symbol}:{timeframe.value}")

        bars: list[Bar] = []
        for idx, rate in enumerate(rates):
            # Support both numpy structured arrays (real MT5) and plain dicts (tests)
            if hasattr(rate, "dtype"):
                names = rate.dtype.names or ()
                vol = float(
                    rate["tick_volume"]
                    if "tick_volume" in names
                    else (rate["real_volume"] if "real_volume" in names else 0.0)
                )
            else:
                rate = dict(rate)
                vol = float(rate.get("tick_volume", rate.get("real_volume", 0.0)))

            bars.append(
                Bar(
                    symbol=symbol,
                    timeframe=timeframe,
                    time=datetime.fromtimestamp(int(rate["time"]), tz=UTC),
                    open=float(rate["open"]),
                    high=float(rate["high"]),
                    low=float(rate["low"]),
                    close=float(rate["close"]),
                    volume=vol,
                    bar_index=idx,
                )
            )
        return bars

    def fetch_instrument_profile(self, symbol: str) -> InstrumentProfile:
        assert self.mt5_client is not None
        info = self.mt5_client.symbol_info(symbol)
        if info is None:
            raise DataSourceError(f"mt5_symbol_info_unavailable:{symbol}")

        digits = int(getattr(info, "digits", 5))
        point = float(getattr(info, "point", 0.00001))
        tick_size = float(getattr(info, "trade_tick_size", point))
        contract_size = float(getattr(info, "trade_contract_size", 100000.0))
        volume_step = float(getattr(info, "volume_step", 0.01))
        volume_min = float(getattr(info, "volume_min", 0.01))
        volume_max = float(getattr(info, "volume_max", 100.0))

        instrument_class = InstrumentClass.FX
        if symbol == "XAUUSD":
            instrument_class = InstrumentClass.XAUUSD
        elif symbol.startswith("US"):
            instrument_class = InstrumentClass.INDICES

        return InstrumentProfile(
            symbol=symbol,
            instrument_class=instrument_class,
            tick_size=tick_size,
            lot_step=volume_step,
            min_lot=volume_min,
            max_lot=volume_max,
            digits=digits,
            point=point,
            contract_size=contract_size,
            noise_floor_atr_mult=0.1,
            spread_warn_atr_mult=0.25,
            stale_entry_atr_mult=0.2,
        )

    def fetch_account_info(self) -> dict[str, Any]:
        """Fetch account balance and equity from MT5."""
        assert self.mt5_client is not None
        info = self.mt5_client.account_info()
        if info is None:
            raise DataSourceError("mt5_account_info_unavailable")
        return {
            "balance": float(getattr(info, "balance", 0.0)),
            "equity": float(getattr(info, "equity", 0.0)),
            "margin": float(getattr(info, "margin", 0.0)),
            "free_margin": float(getattr(info, "margin_free", 0.0)),
            "currency": str(getattr(info, "currency", "USD")),
        }

    def fetch_symbol_info(self, symbol: str) -> dict[str, Any]:
        """Fetch symbol tradability/session metadata used by live rechecks."""
        assert self.mt5_client is not None
        info = self.mt5_client.symbol_info(symbol)
        if info is None:
            raise DataSourceError(f"mt5_symbol_info_unavailable:{symbol}")
        return {
            "trade_allowed": bool(getattr(info, "trade_allowed", True)),
            "trade_mode": int(getattr(info, "trade_mode", -1)),
            "session_deals": getattr(info, "session_deals", None),
        }

    def fetch_tick(self, symbol: str) -> dict[str, Any]:
        """Fetch current tick (bid/ask) for paper fill pricing."""
        assert self.mt5_client is not None
        tick = self.mt5_client.symbol_info_tick(symbol)
        if tick is None:
            raise DataSourceError(f"mt5_tick_unavailable:{symbol}")
        return {
            "bid": float(getattr(tick, "bid", 0.0)),
            "ask": float(getattr(tick, "ask", 0.0)),
            "time": int(getattr(tick, "time", 0)),
        }
