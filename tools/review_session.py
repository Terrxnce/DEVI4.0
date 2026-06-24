#!/usr/bin/env python3
"""DEVI session review - reads logs/prod and prints three checks in one pass.

  1. Decisions: final_decision breakdown, RR-filter rejections, EXECUTE
     decisions vs orders actually FILLED (an EXECUTE can be blocked at a
     pre-trade recheck such as spread, so the two differ).
  2. Trade frequency: EXECUTE and FILLED per day. After the commission filter
     goes live, watch for a modest drop in fills, not a cliff.
  3. Partial-close ledger reconciliation: per-trade realized PnL summed across
     trade_partial_close + trade_close legs, plus the ledger-vs-balance gap.

Usage (from repo root):
    python tools/review_session.py            # latest date in logs/prod
    python tools/review_session.py 2026-06-24 # a specific date
    python tools/review_session.py --logs-root logs
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re


def _read_jsonl(path):
    rows = []
    try:
        with open(path, "r", encoding="utf-8-sig") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return rows


def _dates_present(logs_root):
    dates = set()
    for f in glob.glob(os.path.join(logs_root, "prod", "decisions_*.jsonl")):
        m = re.search(r"decisions_(\d{4}-\d{2}-\d{2})\.jsonl$", f)
        if m:
            dates.add(m.group(1))
    return sorted(dates)


def _money(x):
    try:
        return "$" + format(float(x), ",.2f")
    except (TypeError, ValueError):
        return str(x)


def _filled_count(prod, date):
    return sum(
        1
        for o in _read_jsonl(os.path.join(prod, "live_orders_" + date + ".jsonl"))
        if o.get("status") == "FILLED"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", default=None, help="YYYY-MM-DD (default: latest)")
    ap.add_argument("--logs-root", default="logs")
    args = ap.parse_args()

    prod = os.path.join(args.logs_root, "prod")
    dates = _dates_present(args.logs_root)
    if not dates:
        print("No decisions_*.jsonl under " + prod + ". Run from the repo root.")
        return
    date = args.date or dates[-1]

    print("=" * 64)
    print("DEVI SESSION REVIEW  -  " + date)
    print("=" * 64)

    pos_events = _read_jsonl(os.path.join(prod, "position_events_" + date + ".jsonl"))
    snaps = [e for e in pos_events if e.get("event") == "ftmo_risk_snapshot"]
    day_start = cur_bal = cur_eq = daily_pct = None
    if snaps:
        day_start = snaps[0].get("day_start_balance")
        last = snaps[-1]
        cur_bal = last.get("balance")
        cur_eq = last.get("equity")
        daily_pct = last.get("daily_pnl_pct")
    if cur_bal is not None:
        print("")
        print("Account: balance " + _money(cur_bal) + "  equity " + _money(cur_eq)
              + "  day P&L " + format(daily_pct, "+.2f") + "%  (day start "
              + _money(day_start) + ")")

    decisions = _read_jsonl(os.path.join(prod, "decisions_" + date + ".jsonl"))
    fd = collections.Counter(d.get("final_decision", "?") for d in decisions)
    rr_rej = sum(
        1
        for d in decisions
        for r in (d.get("tp_debug", {}) or {}).get("rejected", [])
        if r.get("rejection_reason") == "rr_below_floor"
    )
    print("")
    print("[1] DECISIONS")
    print("    records evaluated : " + str(len(decisions)))
    for k, v in fd.most_common():
        print("    " + str(k).ljust(22) + ": " + str(v))
    print("    rr_below_floor rejections (rr_filter): " + str(rr_rej))
    print("    orders actually FILLED (live_orders) : " + str(_filled_count(prod, date)))
    print("      (an EXECUTE decision can still be blocked at a pre-trade recheck)")
    execs = [d for d in decisions if d.get("final_decision") == "EXECUTE"]
    if execs:
        print("    EXECUTE decisions (symbol  RR-gross  setup/tier):")
        for d in execs:
            sel = (d.get("tp_debug", {}) or {}).get("selected", {}) or {}
            rr = sel.get("rr", "?")
            rr = format(rr, ".2f") if isinstance(rr, (int, float)) else str(rr)
            print("      " + str(d.get("symbol", "?")).ljust(9) + " rr=" + rr.ljust(7)
                  + " " + str(d.get("setup_class", "?")) + "/"
                  + str(d.get("confidence_tier", "?")))

    print("")
    print("[2] TRADE FREQUENCY")
    print("    date          EXECUTE   FILLED")
    for dt in dates:
        ds = _read_jsonl(os.path.join(prod, "decisions_" + dt + ".jsonl"))
        n = sum(1 for d in ds if d.get("final_decision") == "EXECUTE")
        fl = _filled_count(prod, dt)
        marker = "  <- this date" if dt == date else ""
        print("    " + dt + "    " + str(n).rjust(5) + "   " + str(fl).rjust(6) + marker)
    print("    (commission filter should trim marginal-RR trades - watch for a")
    print("     modest drop in fills, not a cliff.)")

    trades = _read_jsonl(os.path.join(prod, "trades_" + date + ".jsonl"))
    opens = {}
    legs = collections.defaultdict(list)
    for t in trades:
        ev = t.get("event")
        tid = t.get("trade_id")
        if not tid:
            continue
        if ev in ("trade_close", "trade_partial_close"):
            legs[tid].append((ev, float(t.get("close_pnl") or 0.0)))
        elif ev is None and t.get("status") == "open":
            opens[tid] = t

    print("")
    print("[3] PARTIAL-CLOSE LEDGER RECONCILIATION")
    ledger_total = 0.0
    if not legs:
        print("    no closed trades logged for this date yet.")
    for tid, lg in legs.items():
        o = opens.get(tid, {})
        sym = str(o.get("symbol", "?"))
        side = str(o.get("side", "?"))
        lots = o.get("lot_size", "?")
        try:
            lots = round(float(lots), 2)
        except (TypeError, ValueError):
            pass
        total = sum(p for _, p in lg)
        ledger_total += total
        had_partial = any(ev == "trade_partial_close" for ev, _ in lg)
        tag = "  [SCALED]" if had_partial else ""
        legtxt = " + ".join(ev.split("_")[-1] + ":" + _money(p) for ev, p in lg)
        print("    " + sym.ljust(9) + " " + side.ljust(5) + " " + str(lots)
              + " lots -> realized " + _money(total) + "  (" + legtxt + ")" + tag)

    print("")
    print("    LEDGER realized total (sum of close legs): " + _money(ledger_total))
    if day_start is not None and cur_bal is not None:
        bal_delta = cur_bal - day_start
        gap = bal_delta - ledger_total
        print("    BALANCE change today (start -> current) : " + _money(bal_delta))
        print("    GAP (balance - ledger)                  : " + _money(gap))
        print("    Gap should be small: commission + swap + any prior-day")
        print("    positions closing today. A large gap = trades unaccounted.")
    print("")
    print("    [SCALED] = had a partial close. Confirm each one's realized")
    print("    total matches MT5 History full-position PnL for that ticket.")
    print("=" * 64)


if __name__ == "__main__":
    main()
