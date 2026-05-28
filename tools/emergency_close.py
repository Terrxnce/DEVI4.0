"""Emergency force close CLI tool.

Connects to MT5, finds all open D.E.V.I positions (identified by the
'devi:' comment prefix), and closes them immediately with market orders.

This tool does NOT require the bot to be running. It operates directly
against MT5 and is safe to call at any time.

Usage:
    python tools/emergency_close.py
    python tools/emergency_close.py --dry-run
    python tools/emergency_close.py --log logs/force_close.jsonl

Exit codes:
    0  All positions closed (or none found)
    1  One or more positions failed to close — check MT5 manually
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.mt5_source import MT5DataSource
from src.execution.force_close import close_devi_positions, is_devi_position

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("emergency_close")


def _print_separator() -> None:
    print("-" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="D.E.V.I emergency force close — flatten all open positions immediately",
    )
    parser.add_argument(
        "--log",
        default="logs/force_close.jsonl",
        help="JSONL path to append close results (default: logs/force_close.jsonl)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List D.E.V.I positions without closing them",
    )
    args = parser.parse_args()

    _print_separator()
    print(f"D.E.V.I EMERGENCY FORCE CLOSE — {datetime.now(tz=UTC).isoformat()} UTC")
    if args.dry_run:
        print("MODE: DRY RUN (no positions will be closed)")
    else:
        print("MODE: LIVE — positions will be closed immediately")
    _print_separator()

    source = MT5DataSource()

    try:
        mt5_client = source.mt5_client

        if mt5_client is None:
            print("ERROR: MT5 client unavailable. Is MetaTrader 5 running?")
            sys.exit(1)

        # Fetch all positions for inspection
        try:
            raw_positions = mt5_client.positions_get() or []
        except Exception as exc:
            print(f"ERROR: positions_get failed: {exc}")
            sys.exit(1)

        devi_positions = [p for p in raw_positions if is_devi_position(p)]

        print(f"Total open positions : {len(raw_positions)}")
        print(f"D.E.V.I positions    : {len(devi_positions)}")
        _print_separator()

        if not devi_positions:
            print("No D.E.V.I positions found. Nothing to close.")
            return

        for p in devi_positions:
            pos_type = int(getattr(p, "type", 0))
            print(
                f"  ticket={getattr(p, 'ticket', '?'):<10} "
                f"{getattr(p, 'symbol', '?'):<12} "
                f"{'BUY' if pos_type == 0 else 'SELL':<5} "
                f"vol={getattr(p, 'volume', 0):<6} "
                f"open={getattr(p, 'price_open', 0):<10} "
                f"comment={getattr(p, 'comment', '')}"
            )

        _print_separator()

        if args.dry_run:
            print("DRY RUN complete — no positions closed.")
            return

        # Confirm before closing
        print(f"Closing {len(devi_positions)} position(s)...")
        results = close_devi_positions(mt5_client, log_path=args.log)

        _print_separator()
        closed = [r for r in results if r.status == "closed"]
        failed = [r for r in results if r.status == "failed"]

        for r in results:
            icon = "OK  " if r.status == "closed" else "FAIL"
            print(
                f"  [{icon}] ticket={r.ticket:<10} {r.symbol:<12} {r.side:<5} "
                f"close_price={r.close_price}  retcode={r.retcode}  {r.reason}"
            )

        _print_separator()
        print(f"Closed: {len(closed)}   Failed: {len(failed)}")

        if args.log:
            print(f"Log written to: {args.log}")

        if failed:
            print(
                f"\nWARNING: {len(failed)} position(s) could not be closed. "
                "Check MT5 manually."
            )
            sys.exit(1)

    finally:
        source.close()


if __name__ == "__main__":
    main()
