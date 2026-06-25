#!/usr/bin/env python3
"""DEVI strategy backtest over historical bars (logs/history/bars.csv).

Walks each symbol's M15 history chronologically, runs DEVI's REAL decision
pipeline at each step (same detectors / context / confluence / exit planner as
live, no lookahead), and whenever a setup forms with an SL/TP, labels it by
walking FORWARD in the bars to see whether price hit TP or SL first.

It measures the strategy's raw edge in R-multiples (win = +RR, loss = -1) and
writes one labeled row per setup, so the same output doubles as the ML training
set. Use --block-ranging to A/B test dropping non-trending-regime setups.

This reuses DEVI's code; it does not reimplement the strategy. It deliberately
ignores the live execution gates (arming / supervisor / position caps) because
those decide *whether to fire given live constraints*, not whether the setup
itself was good — and edge measurement wants every setup the strategy sees.

V1 SCOPE: per-setup edge + labels. It is NOT yet a full $-equity portfolio sim
(concurrent positions, drawdown gating, lot sizing). Validate on one symbol /
a short window first; trust the full numbers only after a sanity check.

Usage (from repo root, after pulling bars):
    python tools/backtest.py --config src/config/live_market_watch.json
    python tools/backtest.py --symbols EURUSD --stride 4 --horizon 192
    python tools/backtest.py --block-ranging   # A/B the regime filter
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from src.core.enums import Direction, Timeframe, StructureType
from src.narrative.choch_detector import CHoCHDetector
from src.core.models import Bar
from src.context.builder import build_context_snapshot
from src.context.references import compute_reference_levels
from src.context.session_levels import SessionLevelTracker
from src.context.regime import simple_atr
from src.execution.structure_detectors import (
    run_all_detectors, scale_detection_cfg_for_higher_tf,
)
from src.zones.tracker import ZoneTracker
from src.decision.engine import evaluate_decision
from src.core.arming import ArmingService
from src.core.kill_switch import KillSwitch
from src.core.runtime_state import RuntimeState

WARMUP = 250
M15_N, H1_N, H4_N = 250, 400, 300
OB_CLASSES = {"OB_WITH_BOS", "OB_WITH_FVG", "OB_WITH_ENGULFING",
             "SWEEP_WITH_OB", "JUDAS_WITH_OB"}


def _find_ob(conf):
    for s in [conf.primary_trigger, *conf.structural_confirmations]:
        if s is not None and s.structure_type == StructureType.ORDER_BLOCK:
            return s
    return None


def _infer_instrument(symbol: str) -> dict:
    s = symbol.upper().split(".")[0]
    core = "".join(c for c in s if c.isalpha())
    if core.endswith("JPY"):
        point = 0.001
    elif core.startswith("XAU"):
        point = 0.01
    elif core.startswith("XAG"):
        point = 0.001
    elif "US30" in s or "US100" in s or "US500" in s or "NAS" in s:
        point = 0.1
    else:
        point = 0.00001
    return {
        "symbol": symbol, "digits": 5, "point": point, "tick_size": point,
        "tick_value": 1.0, "lot_step": 0.01, "min_lot": 0.01, "max_lot": 100.0,
        "contract_size": 100000.0, "instrument_class": "FX",
    }


def load_bars(path):
    """Return {symbol: {tf: [Bar sorted by (time, bar_index)]}}."""
    data = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as fp:
        for row in csv.DictReader(fp):
            try:
                b = Bar(
                    symbol=row["symbol"], timeframe=Timeframe(row["timeframe"]),
                    time=datetime.fromisoformat(row["time"].replace("Z", "+00:00")),
                    open=float(row["open"]), high=float(row["high"]),
                    low=float(row["low"]), close=float(row["close"]),
                    volume=float(row["volume"]), bar_index=int(row["bar_index"]),
                )
            except (KeyError, ValueError):
                continue
            data.setdefault(b.symbol, {}).setdefault(b.timeframe, []).append(b)
    for sym in data:
        for tf in data[sym]:
            data[sym][tf].sort(key=lambda x: (x.time, x.bar_index))
    return data


def forward_label(m15, start_i, direction, sl, tp, horizon):
    """Walk forward from start_i; return ('win'|'loss'|'timeout', bars_to_resolve).

    Conservative: if a single bar's range spans both SL and TP, call it a loss
    (assume the stop was hit first — no intrabar order available).
    """
    long = direction == Direction.BULLISH
    end = min(len(m15), start_i + horizon)
    for j in range(start_i, end):
        b = m15[j]
        hit_sl = (b.low <= sl) if long else (b.high >= sl)
        hit_tp = (b.high >= tp) if long else (b.low <= tp)
        if hit_sl and hit_tp:
            return "loss", j - start_i + 1
        if hit_sl:
            return "loss", j - start_i + 1
        if hit_tp:
            return "win", j - start_i + 1
    return "timeout", end - start_i


def backtest_symbol(symbol, bars, config, stride, horizon, block_ranging, require_choch=False):
    m15 = bars.get(Timeframe.M15, [])
    h1 = bars.get(Timeframe.H1, [])
    h4 = bars.get(Timeframe.H4, [])
    if len(m15) < WARMUP + 10:
        return []
    det_cfg = config["detection"]
    atr_period = int(det_cfg["atr_period"])
    h1_age = float(det_cfg.get("h1_detection_age_multiplier", 2.5))
    tp_age = float(det_cfg.get("tp_detection_age_multiplier", 4.0))
    cfg = dict(config)
    cfg["instrument"] = _infer_instrument(symbol)

    zt = ZoneTracker(max_zone_age_bars=int(det_cfg.get("max_zone_age_bars", 50)))
    arming, kill, rstate = ArmingService(), KillSwitch(), RuntimeState(run_id="bt")
    choch = CHoCHDetector()
    h1p = h4p = 0
    rows = []

    for i in range(WARMUP, len(m15) - 1, stride):
        cur = m15[i]
        t = cur.time
        while h1p < len(h1) and h1[h1p].time <= t:
            h1p += 1
        while h4p < len(h4) and h4[h4p].time <= t:
            h4p += 1
        m15_w = m15[max(0, i - M15_N + 1): i + 1]
        h1_w = h1[max(0, h1p - H1_N): h1p]
        h4_w = h4[max(0, h4p - H4_N): h4p]
        if len(m15_w) < atr_period or not h1_w:
            continue
        try:
            atr_m15 = simple_atr(m15_w, atr_period)
            m15_s = run_all_detectors(detection_cfg=det_cfg, bars=m15_w, atr=atr_m15)
            h1cfg = scale_detection_cfg_for_higher_tf(det_cfg, h1_age)
            atr_h1 = simple_atr(h1_w, atr_period) if len(h1_w) >= atr_period else atr_m15
            h1_s = run_all_detectors(detection_cfg=h1cfg, bars=h1_w, atr=atr_h1)
            zt.scan(symbol, [*m15_s, *h1_s], cur)
            structures = zt.get_active_structures(symbol)
            tp_s = run_all_detectors(
                detection_cfg=scale_detection_cfg_for_higher_tf(det_cfg, tp_age),
                bars=m15_w, atr=atr_m15)
            tp_h1 = run_all_detectors(
                detection_cfg=scale_detection_cfg_for_higher_tf(det_cfg, h1_age * tp_age),
                bars=h1_w, atr=atr_h1)
            sl_tracker = SessionLevelTracker(
                sweep_lookback_bars=int(config.get("narrative", {}).get("sweep_lookback_bars", 20)))
            session_levels = sl_tracker.compute(m15_w, config.get("sessions", {}))
            ctx = build_context_snapshot(
                symbol=symbol, bars_m15=m15_w, bars_h1=h1_w,
                detected_structures=structures, spread=abs(atr_m15) * 0.05,
                config=cfg, bars_h4=h4_w)
            refs = compute_reference_levels(m15_w)
            entry = cur.close
            outcome = evaluate_decision(
                structures=structures, context=ctx, config=cfg, entry_price=entry,
                references=refs, risk_state={"account_balance": 100000.0},
                runtime_state=rstate, arming_service=arming, kill_switch=kill,
                tp_structures=[*tp_s, *tp_h1], session_levels=session_levels, bars_h4=h4_w)
        except Exception:
            continue

        plan = outcome.exit_plan
        conf = outcome.confluence
        if plan is None or conf is None:
            continue
        if block_ranging and getattr(ctx.regime, "value", "") == "RANGING":
            continue
        if require_choch and conf.setup_class.value in OB_CLASSES:
            _ob = _find_ob(conf)
            if _ob is None or not choch.detect(structures, _ob.bar_index, conf.direction).detected:
                continue
        result, bars_held = forward_label(m15, i + 1, conf.direction, plan.stop_loss, plan.take_profit, horizon)
        rr = float(plan.risk_reward or 0.0)
        r_mult = rr if result == "win" else (-1.0 if result == "loss" else 0.0)
        rows.append({
            "symbol": symbol, "time": t.isoformat(), "session": ctx.session.value,
            "setup_class": conf.setup_class.value, "direction": conf.direction.value,
            "regime": ctx.regime.value, "rr": round(rr, 3),
            "entry": entry, "sl": plan.stop_loss, "tp": plan.take_profit,
            "result": result, "bars_held": bars_held, "r_multiple": round(r_mult, 3),
        })
    return rows


def summarize(rows):
    def stats(rs):
        n = len(rs)
        resolved = [r for r in rs if r["result"] in ("win", "loss")]
        wins = [r for r in resolved if r["result"] == "win"]
        wr = (len(wins) / len(resolved) * 100) if resolved else 0.0
        exp = (sum(r["r_multiple"] for r in resolved) / len(resolved)) if resolved else 0.0
        return n, len(resolved), wr, exp
    print("=" * 70)
    n, res, wr, exp = stats(rows)
    print("OVERALL  setups=%d  resolved=%d  win%%=%.1f  expectancy=%.3fR" % (n, res, wr, exp))
    print("-" * 70)
    print("By regime:")
    for reg in sorted({r["regime"] for r in rows}):
        n, res, wr, exp = stats([r for r in rows if r["regime"] == reg])
        print("  %-10s setups=%-5d resolved=%-5d win%%=%-5.1f exp=%.3fR" % (reg, n, res, wr, exp))
    print("By setup_class:")
    for sc in sorted({r["setup_class"] for r in rows}):
        n, res, wr, exp = stats([r for r in rows if r["setup_class"] == sc])
        print("  %-22s setups=%-5d win%%=%-5.1f exp=%.3fR" % (sc, n, wr, exp))
    print("=" * 70)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", default="logs/history/bars.csv")
    ap.add_argument("--config", default="src/config/live_market_watch.json")
    ap.add_argument("--symbols", default=None, help="comma list; default all in bars file")
    ap.add_argument("--stride", type=int, default=4, help="evaluate every Nth M15 bar")
    ap.add_argument("--horizon", type=int, default=192, help="max M15 bars to resolve a trade")
    ap.add_argument("--block-ranging", action="store_true")
    ap.add_argument("--drop-fvg", action="store_true", help="exclude OB_WITH_FVG from allowed setups")
    ap.add_argument("--min-gap", type=float, default=None, help="override FVG min_gap_atr_mult")
    ap.add_argument("--require-choch", action="store_true", help="require CHoCH confirmation on OB setups")
    ap.add_argument("--out", default="logs/backtest/trades.csv")
    args = ap.parse_args()

    config = json.load(open(args.config, encoding="utf-8"))
    if args.drop_fvg:
        _g = config.setdefault("gates", {})
        _g["allowed_setups"] = [x for x in _g.get("allowed_setups", []) if x != "OB_WITH_FVG"]
    if args.min_gap is not None:
        config.setdefault("detection", {}).setdefault("fair_value_gap", {})["min_gap_atr_mult"] = args.min_gap
    _active = [f for f, on in [("drop-fvg", args.drop_fvg), ("min-gap=%s" % args.min_gap, args.min_gap is not None), ("require-choch", args.require_choch), ("block-ranging", args.block_ranging)] if on]
    print("Active experiment flags: " + (", ".join(_active) if _active else "NONE (baseline)"))
    print("Loading bars from %s ..." % args.bars)
    data = load_bars(args.bars)
    syms = ([s.strip() for s in args.symbols.split(",")] if args.symbols
            else sorted(data.keys()))

    all_rows = []
    for sym in syms:
        if sym not in data:
            continue
        rows = backtest_symbol(sym, data[sym], config, args.stride, args.horizon, args.block_ranging, args.require_choch)
        all_rows.extend(rows)
        print("  %-12s %5d setups" % (sym, len(rows)))

    import os
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    if all_rows:
        with open(args.out, "w", newline="", encoding="utf-8") as fp:
            w = csv.DictWriter(fp, fieldnames=list(all_rows[0].keys()))
            w.writeheader()
            w.writerows(all_rows)
    print("\nWrote %d setups -> %s%s" % (
        len(all_rows), args.out, "  [block-ranging ON]" if args.block_ranging else ""))
    if all_rows:
        summarize(all_rows)


if __name__ == "__main__":
    main()
