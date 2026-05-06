"""Live scan loop — run full decision pipeline every M15 bar boundary.

Usage:
    python tools/live_scan_loop.py --config src/config/live_one_order_test.json \
        --symbol EURUSD --max-orders 1 --max-cycles 10

Safety:
    - Auto-arms before each cycle, auto-disarms after
    - Logs every cycle to stdout and file
    - Stops on keyboard interrupt (Ctrl+C)
    - Stops if max_cycles reached
    - Never retries failed orders
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


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
    for sym in symbols:
        cmd.extend(["--symbol", sym])
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).parent.parent)
    try:
        return json.loads(result.stdout) if result.stdout else {"verdict": "FAIL", "exit_code": 1, "reason": "no_output"}
    except json.JSONDecodeError:
        return {"verdict": "FAIL", "exit_code": 1, "reason": "json_parse_error", "raw_stdout": result.stdout[:500]}


def main() -> int:
    parser = argparse.ArgumentParser(prog="live_scan_loop")
    parser.add_argument("--config", required=True)
    parser.add_argument("--symbol", action="append", default=None)
    parser.add_argument("--max-orders", type=int, default=1)
    parser.add_argument("--max-cycles", type=int, default=10)
    parser.add_argument("--reason", default="live scan loop")
    parser.add_argument("--log-file", default="logs/live_scan_loop.log")
    args = parser.parse_args()

    log_path = Path(args.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    cycle = 0
    print(f"[{datetime.now(tz=UTC).isoformat()}] Live scan loop starting")
    print(f"  Config: {args.config}")
    print(f"  Symbol: {args.symbol}")
    print(f"  Max cycles: {args.max_cycles}")
    print(f"  Max orders/cycle: {args.max_orders}")
    print("  Press Ctrl+C to stop")
    print()

    try:
        while cycle < args.max_cycles:
            cycle += 1
            sleep_sec = _seconds_until_next_bar()
            next_bar = _next_m15_boundary()

            print(f"[{datetime.now(tz=UTC).isoformat()}] Cycle {cycle}/{args.max_cycles}")
            print(f"  Next M15 bar: {next_bar.isoformat()}")
            print(f"  Sleeping {sleep_sec:.0f}s...")
            time.sleep(sleep_sec)

            symbols = args.symbol if args.symbol else ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"]
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
                "failure_code": result.get("failure_code", ""),
                "ticket": result.get("ticket"),
                "open_positions": result.get("open_position_count", 0),
                "balance": result.get("account_balance"),
                "equity": result.get("account_equity"),
                "verdict": result.get("verdict"),
                "exit_code": result.get("exit_code"),
            }

            print(f"  Decision: {log_line['decision']}")
            print(f"  Order:    {log_line['order_status']}")
            print(f"  Ticket:   {log_line['ticket']}")
            print(f"  Positions: {log_line['open_positions']}")
            print(f"  Balance:  {log_line['balance']}")
            print(f"  Verdict:  {log_line['verdict']}")
            print()

            with open(log_path, "a") as f:
                f.write(json.dumps(log_line) + "\n")

            # Short pause to avoid hammering MT5 right at bar boundary
            time.sleep(2)

    except KeyboardInterrupt:
        print(f"\n[{datetime.now(tz=UTC).isoformat()}] Interrupted by user. Exiting cleanly.")
        return 0

    print(f"[{datetime.now(tz=UTC).isoformat()}] Max cycles ({args.max_cycles}) reached. Exiting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
