"""Live scan loop — run full decision pipeline every M15 bar boundary.

Usage:
    python tools/live_scan_loop.py --config src/config/live_one_order_test.json \
        --symbol EURUSD --max-orders 1 --max-cycles 10

Safety:
    - Auto-arms before each cycle, auto-disarms after
    - Logs every cycle to stdout and file
    - Stops on keyboard interrupt (Ctrl+C)
    - Stops if max_cycles reached (optional)
    - Never retries failed orders
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from collections import Counter, defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.ops.discord_notifier import DiscordNotifier
from src.ops.ollama_narrator import generate_narrative
from src.ops.run_summary import (
    accumulate_cycle,
    make_daily_accumulator,
    match_outcomes,
    print_daily_summary,
)

_DISCORD = DiscordNotifier()


def _fetch_day_outcomes(date_str: str) -> list[dict]:
    """Fetch closed deals for the given day directly from MT5.

    Initialises MT5 in this process, fetches history, shuts down.
    Silent fail — returns empty list on any error so daily summary
    always posts regardless of MT5 availability.

    Groups all OUT deals by position_id so that:
      - Partial closes + final close are summed into one profit figure
      - Each returned record has a 'ticket' field for precise matching
        in match_outcomes (prevents same-symbol P&L cloning)
    """
    try:
        import MetaTrader5 as mt5  # type: ignore
        from collections import defaultdict
        day_start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
        day_end = day_start + timedelta(days=1)
        if not mt5.initialize():
            return []
        try:
            deals = mt5.history_deals_get(day_start, day_end)
        finally:
            mt5.shutdown()
        if deals is None:
            return []

        # Group OUT deals by position_id:
        # - sum profit (partial + final close contribute separately)
        # - keep the last close price (latest deal by time)
        grouped: dict = defaultdict(lambda: {
            "symbol": "", "profit": 0.0, "price": 0.0,
            "volume": 0.0, "ticket": 0, "_last_time": 0,
        })
        for deal in deals:
            if int(getattr(deal, "entry", -1)) != 1:   # OUT deals only
                continue
            if int(getattr(deal, "type", -1)) == 2:    # skip balance ops
                continue
            symbol = str(getattr(deal, "symbol", "")).strip()
            if not symbol:
                continue
            position_id = int(getattr(deal, "position_id", 0))
            deal_time = int(getattr(deal, "time", 0))
            g = grouped[position_id]
            g["symbol"] = symbol
            g["ticket"] = position_id
            g["profit"] += float(getattr(deal, "profit", 0.0))
            # Track last close price by deal time
            if deal_time >= g["_last_time"]:
                g["price"] = float(getattr(deal, "price", 0.0))
                g["_last_time"] = deal_time
            g["volume"] = float(getattr(deal, "volume", 0.0))

        return [
            {"symbol": g["symbol"], "profit": g["profit"],
             "price": g["price"], "volume": g["volume"], "ticket": g["ticket"]}
            for g in grouped.values()
        ]
    except Exception:
        return []


def _close_day(acc: dict) -> None:
    """Enrich accumulator with outcomes, generate narrative, post to Discord."""
    date_str = acc.get("date", datetime.now(tz=UTC).strftime("%Y-%m-%d"))
    closed_deals = _fetch_day_outcomes(date_str)
    if closed_deals:
        if acc.get("orders_filled"):
            # Normal path: match today's fills against their close outcomes
            acc["trade_outcomes"] = match_outcomes(acc["orders_filled"], closed_deals)
        else:
            # Positions opened in a previous session closed today (TP/SL/session exit)
            # orders_filled is empty so match_outcomes has nothing to work with.
            # Store the raw deals so Discord can report what actually closed today.
            acc["closed_deals_today"] = closed_deals
    narrative = generate_narrative(acc)
    print_daily_summary(acc)
    _DISCORD.send_daily_summary(acc, narrative=narrative)


def _load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _summarise_tp_debug(tp_debug: dict | None) -> dict | None:
    """Return a compact summary of tp_debug for the scan loop log line."""
    if not isinstance(tp_debug, dict):
        return None
    found = tp_debug.get("found", [])
    rejected = tp_debug.get("rejected", [])
    if not found and not rejected:
        return None
    reasons = list({r.get("rejection_reason") for r in rejected if r.get("rejection_reason")})
    return {
        "found_count": len(found),
        "rejected_count": len(rejected),
        "rejection_reasons": sorted(reasons),
    }


def _parse_hhmm(value: str) -> tuple[int, int]:
    hh, mm = str(value).split(":")
    return int(hh), int(mm)


def _is_in_any_session(now: datetime, sessions_cfg: dict) -> bool:
    t = now.time()
    for key in ("ASIA", "LONDON", "NY_AM", "NY_PM"):
        spec = sessions_cfg.get(key)
        if not isinstance(spec, dict):
            continue
        sh, sm = _parse_hhmm(spec.get("start", "00:00"))
        eh, em = _parse_hhmm(spec.get("end", "00:00"))
        start = t.replace(hour=sh, minute=sm, second=0, microsecond=0)
        end = t.replace(hour=eh, minute=em, second=0, microsecond=0)
        if start <= t < end:
            return True
    return False


def _seconds_until_next_session(now: datetime, sessions_cfg: dict) -> float:
    """Return seconds until the next session start (UTC)."""
    candidates: list[datetime] = []
    for key in ("ASIA", "LONDON", "NY_AM", "NY_PM"):
        spec = sessions_cfg.get(key)
        if not isinstance(spec, dict):
            continue
        sh, sm = _parse_hhmm(spec.get("start", "00:00"))
        start_today = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
        candidates.append(start_today)
    # Fallback: next day ASIA 00:00
    if not candidates:
        next_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return (next_midnight - now).total_seconds()
    # Pick the next start strictly after now, else roll to next day ASIA start.
    future = [dt for dt in candidates if dt > now]
    if future:
        next_start = min(future)
        return (next_start - now).total_seconds()

    asia = sessions_cfg.get("ASIA", {"start": "00:00"})
    sh, sm = _parse_hhmm(asia.get("start", "00:00"))
    next_start = (now.replace(hour=sh, minute=sm, second=0, microsecond=0) + timedelta(days=1))
    return (next_start - now).total_seconds()


def _next_m15_boundary() -> datetime:
    """Return the next M15 bar boundary (00, 15, 30, 45 minutes past hour)."""
    now = datetime.now(tz=UTC)
    minute = now.minute
    next_min = ((minute // 15) + 1) * 15
    if next_min >= 60:
        next_min = 0
        hour = now.hour + 1
    else:
        hour = now.hour
    boundary = now.replace(minute=next_min, second=0, microsecond=0)
    if next_min == 0:
        boundary = boundary.replace(hour=hour)
    if boundary <= now:
        boundary = boundary.replace(minute=boundary.minute + 15)
    return boundary


def _seconds_until_next_bar() -> float:
    boundary = _next_m15_boundary()
    return (boundary - datetime.now(tz=UTC)).total_seconds()


def run_scan(
    *,
    config: str,
    symbols: list[str],
    max_orders: int,
    run_id: str,
    reason: str,
) -> dict:
    """Execute one live scan cycle via subprocess."""
    cmd = [
        sys.executable,
        "run.py",
        "live", "scan",
        "--namespace", "prod",
        "--config", config,
        "--run-id", run_id,
        "--max-orders", str(max_orders),
        "--ttl-minutes", "15",
        "--reason", reason,
        "--json",
    ]
    # If symbols list is empty, let CLI auto-load Market Watch symbols.
    for sym in symbols:
        cmd.extend(["--symbol", sym])
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).parent.parent)
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    diag = {
        "subprocess_exit_code": proc.returncode,
        "stderr_preview": stderr[:1200] if stderr else "",
    }

    if not stdout:
        out: dict = {
            "verdict": "FAIL",
            "exit_code": proc.returncode or 1,
            "reason": "no_output",
            "failure_code": "live_scan_subprocess_no_stdout",
            **diag,
        }
        return out

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return {
            "verdict": "FAIL",
            "exit_code": 1,
            "reason": "json_parse_error",
            "failure_code": "live_scan_subprocess_invalid_json",
            "raw_stdout_preview": stdout[:800],
            **diag,
        }

    if not isinstance(payload, dict):
        return {
            "verdict": "FAIL",
            "exit_code": 1,
            "reason": "json_not_object",
            "failure_code": "live_scan_subprocess_json_not_object",
            **diag,
        }

    # Preserve parsed fields; attach subprocess diagnostics for ops debugging.
    for k, v in diag.items():
        payload.setdefault(k, v)
    if proc.returncode != 0 and payload.get("verdict") != "FAIL":
        payload["verdict"] = "FAIL"
        payload.setdefault("exit_code", proc.returncode or 1)
        payload.setdefault(
            "failure_code",
            f"live_scan_subprocess_nonzero_exit:{proc.returncode}",
        )
    return payload


def _print_symbol_breakdown(result: dict, *, max_groups: int = 6, per_group: int = 5) -> None:
    symbol_results = result.get("symbol_results")
    if not isinstance(symbol_results, list) or not symbol_results:
        return

    rows: list[dict] = [r for r in symbol_results if isinstance(r, dict)]
    if not rows:
        return

    by_code: Counter[str] = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    filled: list[str] = []

    for r in rows:
        sym = str(r.get("symbol", ""))
        decision = str(r.get("decision", ""))
        code = str(r.get("failure_code", ""))
        order_status = r.get("order_status")
        if order_status == "FILLED":
            filled.append(sym)
        key = code if code else decision
        by_code[key] += 1
        if sym and len(examples[key]) < per_group:
            examples[key].append(sym)

    if filled:
        print(f"  Filled:   {', '.join(filled[:10])}{' ...' if len(filled) > 10 else ''}")

    print("  Top blocks:")
    for idx, (code, count) in enumerate(by_code.most_common(max_groups), start=1):
        ex = examples.get(code, [])
        ex_str = f" (e.g. {', '.join(ex)})" if ex else ""
        print(f"    {idx}. {code}: {count}/{len(rows)}{ex_str}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="live_scan_loop")
    parser.add_argument("--config", required=True)
    parser.add_argument("--symbol", action="append", default=None)
    parser.add_argument("--max-orders", type=int, default=1)
    parser.add_argument("--max-cycles", type=int, default=None)
    parser.add_argument("--reason", default="live scan loop")
    parser.add_argument("--log-file", default="logs/live_scan_loop.log")
    args = parser.parse_args()

    cfg = _load_config(args.config)
    sessions_cfg = cfg.get("sessions", {}) if isinstance(cfg, dict) else {}

    cycle = 0
    daily_acc = make_daily_accumulator()
    summary_printed_for_date: str = ""

    # Write risk config snapshot to log for audit trail
    risk_cfg = cfg.get("risk", {}) if isinstance(cfg, dict) else {}
    risk_snapshot = {
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "event": "risk_config_snapshot",
        "config_file": args.config,
        "risk_per_trade_pct": risk_cfg.get("risk_per_trade_pct"),
        "dynamic_lot_sizing": risk_cfg.get("dynamic_lot_sizing"),
        "fixed_lot_size": risk_cfg.get("fixed_lot_size"),
        "max_open_positions_total": risk_cfg.get("max_open_positions_total"),
        "max_open_positions_per_symbol": risk_cfg.get("max_open_positions_per_symbol"),
        "max_new_trades_per_session": risk_cfg.get("max_new_trades_per_session"),
        "soft_daily_reduction_pct": risk_cfg.get("soft_daily_reduction_pct"),
        "block_new_trades_daily_pct": risk_cfg.get("block_new_trades_daily_pct"),
        "force_close_daily_pct": risk_cfg.get("force_close_daily_pct"),
        "force_close_total_pct": risk_cfg.get("force_close_total_pct"),
        "same_direction_correlation_cap": risk_cfg.get("same_direction_correlation_cap"),
    }
    log_path = Path(args.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        f.write(json.dumps(risk_snapshot) + "\n")

    print(f"[{datetime.now(tz=UTC).isoformat()}] Live scan loop starting")
    print(f"  Config: {args.config}")
    print(f"  Symbol: {args.symbol}")
    print(f"  Max cycles: {args.max_cycles}")
    print(f"  Max orders/cycle: {args.max_orders}")
    print("  Press Ctrl+C to stop")
    print()
    _DISCORD._post("🟢 **D.E.V.I is online**\nScanning markets. Standing by.")

    try:
        while True:
            if args.max_cycles is not None and cycle >= int(args.max_cycles):
                break

            now = datetime.now(tz=UTC)
            if isinstance(sessions_cfg, dict) and sessions_cfg and not _is_in_any_session(now, sessions_cfg):
                # End of trading day: print daily summary once after 19:00 UTC
                today_str = now.strftime("%Y-%m-%d")
                if now.hour >= 19 and summary_printed_for_date != today_str and daily_acc["cycles"] > 0:
                    daily_acc["date"] = today_str
                    _close_day(daily_acc)
                    summary_printed_for_date = today_str
                    daily_acc = make_daily_accumulator()

                wait_sec = _seconds_until_next_session(now, sessions_cfg)
                print(f"[{now.isoformat()}] Outside session hours. Waiting {wait_sec:.0f}s for next session start...")
                time.sleep(wait_sec)
                continue  # re-check session before incrementing cycle or running a scan

            cycle += 1

            sleep_sec = _seconds_until_next_bar()
            next_bar = _next_m15_boundary()

            print(f"[{datetime.now(tz=UTC).isoformat()}] Cycle {cycle}/{args.max_cycles if args.max_cycles is not None else 'unlimited'}")
            print(f"  Next M15 bar: {next_bar.isoformat()}")
            print(f"  Sleeping {sleep_sec:.0f}s...")
            time.sleep(sleep_sec)

            symbols = args.symbol if args.symbol else []
            run_id = f"live_scan_loop_{cycle:03d}"
            result = run_scan(
                config=args.config,
                symbols=symbols,
                max_orders=args.max_orders,
                run_id=run_id,
                reason=args.reason,
            )

            timestamp = datetime.now(tz=UTC).isoformat()
            log_line = {
                "timestamp": timestamp,
                "cycle": cycle,
                "run_id": run_id,
                "decision": result.get("decision", "UNKNOWN"),
                "order_status": result.get("order_status", "N/A"),
                "failure_code": result.get("failure_code", "") or result.get("reason", ""),
                "ticket": result.get("ticket"),
                "open_positions": result.get("open_position_count", 0),
                "balance": result.get("account_balance"),
                "equity": result.get("account_equity"),
                "verdict": result.get("verdict"),
                "exit_code": result.get("exit_code"),
                "subprocess_exit_code": result.get("subprocess_exit_code"),
                "stderr_preview": result.get("stderr_preview"),
                "stdout_preview": result.get("raw_stdout_preview") if result.get("verdict") == "FAIL" else None,
                "fatal_reason": result.get("reason") if result.get("failure_code") == "fatal_exception" else None,
                "fatal_traceback": result.get("traceback") if result.get("failure_code") == "fatal_exception" else None,
                "tp_debug_summary": _summarise_tp_debug(result.get("tp_debug")),
            }

            accumulate_cycle(daily_acc, result)

            # Minimal per-cycle stdout — just enough to show the loop is alive
            print(f"  [{timestamp}] Cycle {cycle} — {log_line['decision']} | bal {log_line['balance']} | pos {log_line['open_positions']}")

            # Surface subprocess errors immediately
            if log_line.get("verdict") == "FAIL":
                if log_line.get("subprocess_exit_code") is not None:
                    print(f"  Child exit: {log_line['subprocess_exit_code']}")
                prev = log_line.get("stderr_preview")
                if prev:
                    print(f"  Stderr: {prev[:400]}{'..' if len(str(prev)) > 400 else ''}")
            print()

            with open(log_path, "a") as f:
                f.write(json.dumps(log_line) + "\n")

            # Short pause to avoid hammering MT5 right at bar boundary
            time.sleep(2)

    except KeyboardInterrupt:
        print(f"\n[{datetime.now(tz=UTC).isoformat()}] Interrupted by user. Exiting cleanly.")
        if daily_acc["cycles"] > 0:
            daily_acc["date"] = datetime.now(tz=UTC).strftime("%Y-%m-%d")
            _close_day(daily_acc)
        _DISCORD._post("🔴 **D.E.V.I is offline**\nScanning stopped.")
        return 0

    if args.max_cycles is not None:
        print(f"[{datetime.now(tz=UTC).isoformat()}] Max cycles ({args.max_cycles}) reached. Exiting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
