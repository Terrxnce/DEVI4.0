#!/usr/bin/env python3
"""Pull historical bars from the connected MT5 terminal into DEVI's CSV format.

Output is a single CSV with columns DEVI's CsvDataSource reads directly:
    symbol, timeframe, time, open, high, low, close, volume, bar_index
so the backtest can consume it with no extra conversion.

This is STEP 1 of the backtest pipeline: get the fuel. It must run on the
machine with the logged-in MT5 terminal (MetaTrader5 is Windows-only). Depth
depends on the broker — scroll each chart back in the terminal first to force
it to download deep M15 history before running this.

Usage (from repo root, with MT5 running):
    python tools/pull_mt5_history.py --years 2
    python tools/pull_mt5_history.py --symbols EURUSD,GBPUSD --timeframes M15,H1
    python tools/pull_mt5_history.py --config src/config/live_market_watch.json
    python tools/pull_mt5_history.py --out logs/history/bars.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import UTC, datetime, timedelta

BAR_COLUMNS = ["symbol", "timeframe", "time", "open", "high", "low",
               "close", "volume", "bar_index"]


def _rate_get(rate, key):
    """Read a field from an MT5 rate (numpy void), a dict, or an object."""
    try:
        return rate[key]
    except (KeyError, IndexError, TypeError, ValueError):
        return getattr(rate, key, None)


def bars_to_rows(symbol: str, tf_value: str, rates) -> list[dict]:
    """Pure conversion: MT5 rates -> CsvDataSource-compatible row dicts.

    bar_index is assigned 0..N-1 in ascending time order (CsvDataSource sorts
    by (time, bar_index)). Volume uses tick_volume, falling back to real_volume.
    """
    items = []
    for r in rates:
        t = _rate_get(r, "time")
        if t is None:
            continue
        items.append((int(t), r))
    items.sort(key=lambda x: x[0])

    rows = []
    for idx, (t, r) in enumerate(items):
        vol = _rate_get(r, "tick_volume")
        if vol is None:
            vol = _rate_get(r, "real_volume") or 0
        rows.append({
            "symbol": symbol,
            "timeframe": tf_value,
            "time": datetime.fromtimestamp(int(t), tz=UTC).isoformat(),
            "open": float(_rate_get(r, "open")),
            "high": float(_rate_get(r, "high")),
            "low": float(_rate_get(r, "low")),
            "close": float(_rate_get(r, "close")),
            "volume": float(vol),
            "bar_index": idx,
        })
    return rows


def _resolve_symbols(mt5, args) -> list[str]:
    if args.symbols:
        return [s.strip() for s in args.symbols.split(",") if s.strip()]
    if args.config:
        cfg = json.load(open(args.config, encoding="utf-8"))
        return [k for k in cfg.get("symbol_sessions", {}) if k != "default"]
    syms = mt5.symbols_get()
    return [s.name for s in (syms or [])]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=None, help="comma-separated broker symbol names")
    ap.add_argument("--config", default=None, help="config whose symbol_sessions defines the basket")
    ap.add_argument("--timeframes", default="M15,H1,H4")
    ap.add_argument("--years", type=float, default=2.0)
    ap.add_argument("--out", default="logs/history/bars.csv")
    args = ap.parse_args()

    import MetaTrader5 as mt5  # noqa: only available on the MT5 host

    if not mt5.initialize():
        print("MT5 initialize() failed - is the terminal running and logged in?")
        print("last_error:", mt5.last_error())
        return 1

    tf_map = {"M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4}
    tfs = [t.strip().upper() for t in args.timeframes.split(",") if t.strip()]
    symbols = _resolve_symbols(mt5, args)
    to_dt = datetime.now(tz=UTC)
    from_dt = to_dt - timedelta(days=int(args.years * 365))

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    total = 0
    with open(args.out, "w", newline="", encoding="utf-8") as fp:
        w = csv.DictWriter(fp, fieldnames=BAR_COLUMNS)
        w.writeheader()
        for sym in symbols:
            mt5.symbol_select(sym, True)  # ensure it's in Market Watch
            for tf in tfs:
                if tf not in tf_map:
                    continue
                rates = mt5.copy_rates_range(sym, tf_map[tf], from_dt, to_dt)
                if rates is None or len(rates) == 0:
                    print("  no data: %s %s (scroll the chart back to download?)" % (sym, tf))
                    continue
                rows = bars_to_rows(sym, tf, rates)
                w.writerows(rows)
                total += len(rows)
                print("  %-12s %-4s %6d bars" % (sym, tf, len(rows)))

    mt5.shutdown()
    print("Wrote %d bars across %d symbols -> %s" % (total, len(symbols), args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
