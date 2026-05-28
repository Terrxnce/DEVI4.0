from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from src.core.enums import InstrumentClass, Timeframe
from src.core.models import Bar, InstrumentProfile
from src.data.base import DataSourceError


class MT5ClientProtocol(Protocol):
    TIMEFRAME_M15: Any
    TIMEFRAME_H1: Any
    TIMEFRAME_H4: Any

    def initialize(self) -> bool:
        ...

    def shutdown(self) -> None:
        ...

    def symbols_get(self) -> Any:
        ...

    def copy_rates_from_pos(self, symbol: str, timeframe: Any, start_pos: int, count: int) -> Any:
        ...

    def symbol_info(self, symbol: str) -> Any:
        ...

    def symbol_select(self, symbol: str, enable: bool) -> Any:
        ...

    def account_info(self) -> Any:
        ...

    def symbol_info_tick(self, symbol: str) -> Any:
        ...

    def history_deals_get(self, from_date: Any, to_date: Any) -> Any:
        ...


@dataclass
class MT5DataSource:
    mt5_client: MT5ClientProtocol | None = None
    initialized: bool = False
    # Broker server UTC offset in hours. MT5 stores bar open times in broker
    # server time, not UTC. Set this to match your broker's server timezone
    # so all bar times are normalised to UTC before session classification.
    # Example: UTC+3 broker (IC Markets, Pepperstone EET) → set to 3.
    broker_utc_offset_hours: int = 0

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

    def list_market_watch_symbols(self) -> list[str]:
        """Return symbols from MT5 Market Watch (visible symbols)."""
        assert self.mt5_client is not None
        raw = self.mt5_client.symbols_get()
        if raw is None:
            return []
        symbols: list[str] = []
        for item in raw:
            name = str(getattr(item, "name", "")).strip()
            if not name:
                continue
            visible = bool(getattr(item, "visible", False))
            if visible:
                # Preserve exact broker symbol name/case.
                # MT5 symbol_info/copy_rates calls can be case-sensitive for some brokers.
                symbols.append(name)
        # Stable ordering for repeatable scans
        return sorted(set(symbols))

    def _to_mt5_tf(self, timeframe: Timeframe) -> Any:
        assert self.mt5_client is not None
        if timeframe == Timeframe.M15:
            return self.mt5_client.TIMEFRAME_M15
        if timeframe == Timeframe.H1:
            return self.mt5_client.TIMEFRAME_H1
        if timeframe == Timeframe.H4:
            return self.mt5_client.TIMEFRAME_H4
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
                    time=datetime.fromtimestamp(int(rate["time"]), tz=UTC) - timedelta(hours=self.broker_utc_offset_hours),
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
            # MT5 returns None if the symbol isn't selected/activated in Market Watch.
            select = getattr(self.mt5_client, "symbol_select", None)
            if callable(select):
                try:
                    select(symbol, True)
                except Exception:
                    pass
            info = self.mt5_client.symbol_info(symbol)
        if info is None:
            raise DataSourceError(f"mt5_symbol_info_unavailable:{symbol}")

        digits = int(getattr(info, "digits", 5))
        point = float(getattr(info, "point", 0.00001))
        tick_size = float(getattr(info, "trade_tick_size", point))
        tick_value = float(getattr(info, "trade_tick_value", 1.0))
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
            tick_value=tick_value,
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
            select = getattr(self.mt5_client, "symbol_select", None)
            if callable(select):
                try:
                    select(symbol, True)
                except Exception:
                    pass
            info = self.mt5_client.symbol_info(symbol)
        if info is None:
            raise DataSourceError(f"mt5_symbol_info_unavailable:{symbol}")
        return {
            "trade_allowed": bool(getattr(info, "trade_allowed", True)),
            "trade_mode": int(getattr(info, "trade_mode", -1)),
            "session_deals": getattr(info, "session_deals", None),
        }

    def fetch_closed_deals(self, from_date: datetime, to_date: datetime) -> list[dict[str, Any]]:
        """Fetch closed deals for a date range from MT5 trade history.

        Returns list of dicts with symbol, profit, price, time.
        Only returns DEAL_ENTRY_OUT deals (actual closes, not opens).
        Silent fail — returns empty list on any error.
        """
        assert self.mt5_client is not None
        history_fn = getattr(self.mt5_client, "history_deals_get", None)
        if not callable(history_fn):
            return []
        try:
            deals = history_fn(from_date, to_date)
        except Exception:
            return []
        if deals is None:
            return []

        results: list[dict[str, Any]] = []
        for deal in deals:
            # DEAL_ENTRY_OUT = 1 — only closing deals carry realised P&L
            entry = int(getattr(deal, "entry", -1))
            if entry != 1:
                continue
            symbol = str(getattr(deal, "symbol", "")).strip()
            if not symbol:
                continue
            profit = float(getattr(deal, "profit", 0.0))
            # Skip balance/deposit operations (type 2) and zero-profit non-trades
            deal_type = int(getattr(deal, "type", -1))
            if deal_type == 2:  # DEAL_TYPE_BALANCE
                continue
            results.append({
                "symbol": symbol,
                "profit": profit,
                "price": float(getattr(deal, "price", 0.0)),
                "volume": float(getattr(deal, "volume", 0.0)),
                "time": datetime.fromtimestamp(int(getattr(deal, "time", 0)), tz=UTC),
                "type": deal_type,
            })
        return results

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
