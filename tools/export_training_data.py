#!/usr/bin/env python3
"""Export a labeled training dataset from DEVI telemetry.

Joins three telemetry layers into one flat row per EXECUTED setup:

    snapshot (features)  --snapshot_id-->  decision (setup/choice)
                         --decision_id-->  trade (outcome label)

Output: CSV, one row per executed trade, with market/setup features and the
realized outcome. Designed to also consume backtest output (same schema), which
is how you bulk-generate a real training set — live data alone is far too small.

LABEL HYGIENE (read before training):
  * close_reason == 'bot_closed' (session_close_exit) is a CENSORED label — the
    trade was cut by the timer, not by hitting TP or SL. Use the `label_clean`
    column (1 only for tp_hit / sl_hit) to filter these out, or relabel from
    bars. Training on censored labels teaches the session timer, not the edge.
  * outcome_pnl comes from the trades ledger. Until the Option-B close fix is
    deployed, scaled trades understate PnL (the partial leg). `win` is still
    directionally correct in most cases, but treat pre-fix PnL with caution.

Usage (from repo root):
    python tools/export_training_data.py
    python tools/export_training_data.py --out logs/ml/training_data.csv
    python tools/export_training_data.py --logs-root logs --include-eval
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re

COLUMNS = [
    "date", "snapshot_id", "decision_id", "symbol", "session",
    "setup_class", "confidence_tier", "execution_side",
    "rr_selected", "sl_distance_pips", "tp_source_type", "tp_quality",
    "tp_distance_atr", "n_tp_found", "n_tp_rejected",
    "atr_m15", "atr_h1", "spread", "spread_atr_ratio", "n_structures",
    "outcome_pnl", "win", "close_reason", "label_clean",
]


def _iter_jsonl(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _trade_outcomes(trades_path):
    """decision_id -> {pnl, close_reason, lots}. PnL sums all close legs."""
    out = {}
    for t in _iter_jsonl(trades_path):
        did = t.get("decision_id")
        if not did:
            continue
        ev = t.get("event")
        if ev in ("trade_close", "trade_partial_close"):
            o = out.setdefault(did, {"pnl": 0.0, "close_reason": None, "lots": None})
            o["pnl"] += float(t.get("close_pnl") or 0.0)
            if ev == "trade_close":
                o["close_reason"] = t.get("close_reason")
        elif ev is None and t.get("status") == "open":
            o = out.setdefault(did, {"pnl": 0.0, "close_reason": None, "lots": None})
            o["lots"] = t.get("lot_size")
    return out


def _snapshot_features(snap_path, wanted_ids):
    """Stream the (large) snapshot file; extract features for wanted snapshot_ids."""
    feats = {}
    if not wanted_ids:
        return feats
    for s in _iter_jsonl(snap_path):
        sid = s.get("snapshot_id")
        if sid not in wanted_ids:
            continue
        structs = s.get("detected_structures") or []
        feats[sid] = {
            "atr_m15": s.get("atr_m15"),
            "atr_h1": s.get("atr_h1"),
            "spread": s.get("spread"),
            "n_structures": len(structs) if isinstance(structs, list) else "",
            "session": s.get("session"),
        }
        if len(feats) == len(wanted_ids):
            break
    return feats


def _date_from(path):
    m = re.search(r"_(\d{4}-\d{2}-\d{2})\.jsonl$", path)
    return m.group(1) if m else ""


def export(logs_root, out_path, include_eval):
    roots = [os.path.join(logs_root, "prod")]
    if include_eval:
        roots += [os.path.join(logs_root, "eval"), os.path.join(logs_root, "eval", "eval")]

    rows = []
    for root in roots:
        for dec_path in sorted(glob.glob(os.path.join(root, "decisions_*.jsonl"))):
            date = _date_from(dec_path)
            execs = [d for d in _iter_jsonl(dec_path)
                     if d.get("final_decision") == "EXECUTE"]
            if not execs:
                continue
            outcomes = _trade_outcomes(os.path.join(root, "trades_%s.jsonl" % date))
            want = {d.get("snapshot_id") for d in execs if d.get("snapshot_id")}
            feats = _snapshot_features(os.path.join(root, "snapshots_%s.jsonl" % date), want)

            for d in execs:
                sid = d.get("snapshot_id")
                did = d.get("decision_id")
                sel = (d.get("tp_debug", {}) or {}).get("selected") or {}
                found = (d.get("tp_debug", {}) or {}).get("found") or []
                rejected = (d.get("tp_debug", {}) or {}).get("rejected") or []
                f = feats.get(sid, {})
                oc = outcomes.get(did, {})
                pnl = oc.get("pnl")
                reason = oc.get("close_reason")
                atr_m15 = f.get("atr_m15")
                spread = f.get("spread")
                spread_atr = ""
                try:
                    if atr_m15 and float(atr_m15) > 0 and spread is not None:
                        spread_atr = round(float(spread) / float(atr_m15), 4)
                except (TypeError, ValueError):
                    pass
                rows.append({
                    "date": date,
                    "snapshot_id": sid,
                    "decision_id": did,
                    "symbol": d.get("symbol"),
                    "session": d.get("session") or f.get("session"),
                    "setup_class": d.get("setup_class"),
                    "confidence_tier": d.get("confidence_tier"),
                    "execution_side": d.get("execution_side"),
                    "rr_selected": sel.get("rr"),
                    "sl_distance_pips": d.get("sl_distance_pips"),
                    "tp_source_type": sel.get("source_type"),
                    "tp_quality": sel.get("quality"),
                    "tp_distance_atr": sel.get("distance_atr"),
                    "n_tp_found": len(found),
                    "n_tp_rejected": len(rejected),
                    "atr_m15": atr_m15,
                    "atr_h1": f.get("atr_h1"),
                    "spread": spread,
                    "spread_atr_ratio": spread_atr,
                    "n_structures": f.get("n_structures"),
                    "outcome_pnl": "" if pnl is None else round(pnl, 2),
                    "win": "" if pnl is None else (1 if pnl > 0 else 0),
                    "close_reason": reason,
                    "label_clean": 1 if reason in ("tp_hit", "sl_hit") else 0,
                })

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fp:
        w = csv.DictWriter(fp, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs-root", default="logs")
    ap.add_argument("--out", default="logs/ml/training_data.csv")
    ap.add_argument("--include-eval", action="store_true")
    args = ap.parse_args()
    rows = export(args.logs_root, args.out, args.include_eval)
    clean = sum(1 for r in rows if r["label_clean"] == 1)
    wins = sum(1 for r in rows if r["win"] == 1)
    print("Wrote %d labeled rows -> %s" % (len(rows), args.out))
    print("  clean labels (tp_hit/sl_hit): %d   |   censored (bot_closed): %d"
          % (clean, len(rows) - clean))
    print("  wins: %d   losses: %d" % (wins, len(rows) - wins - sum(1 for r in rows if r["win"] == "")))


if __name__ == "__main__":
    main()
