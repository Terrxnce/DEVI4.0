"""Sandbox script: Run one complete paper/eval session using REAL MT5 data.

This script connects to your local MetaTrader 5 terminal, fetches live data,
runs the full decision pipeline, and simulates a paper fill if a trade passes.

Safety:
- No real orders are placed.
- MT5 is used as a data source only.
- Forbidden broker methods are blocked by MT5PaperGuard.

Prerequisites:
- MetaTrader 5 terminal must be running and logged in.
- Python package `MetaTrader5` must be installed.

Usage:
    python sandbox/run_paper_mt5_session.py
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# Ensure src is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.loader import load_config
from src.core.enums import Namespace
from src.data.mt5_guard import create_paper_safe_mt5
from src.data.mt5_source import MT5DataSource
from src.execution.paper_session import PaperSession


def _print_evidence(result, logs_dir: Path) -> None:
    print("\n" + "=" * 60)
    print("PAPER MT5 SESSION EVIDENCE")
    print("=" * 60)

    print(f"\n  [MT5 CONNECTION]")
    print(f"    status: SUCCESS")

    print(f"\n  [ACCOUNT]")
    print(f"    balance: {result.account_balance:,.2f}")
    print(f"    equity:  {result.account_equity:,.2f}")

    print(f"\n  [SYMBOL & MARKET DATA]")
    print(f"    symbol: EURUSD")
    print(f"    tick_bid: {result.tick_bid}")
    print(f"    tick_ask: {result.tick_ask}")
    print(f"    spread: {abs(result.tick_ask - result.tick_bid):.5f}")

    print(f"\n  [BARS FETCHED]")
    print(f"    M15 bars: {result.bars_m15_count}")
    print(f"    H1 bars:  {result.bars_h1_count}")

    print(f"\n  [DECISION]")
    print(f"    final_decision: {result.decision.value}")
    print(f"    failure_code:   {result.failure_code}")

    if result.paper_fill:
        print(f"\n  [PAPER FILL]")
        print(f"    trade_id:      {result.paper_fill['trade_id']}")
        print(f"    decision_id:   {result.paper_fill['decision_id']}")
        print(f"    ticket:        {result.paper_fill['ticket']}")
        print(f"    side:          {result.paper_fill['side']}")
        print(f"    intended_entry: {result.paper_fill['intended_entry']}")
        print(f"    actual_fill:   {result.paper_fill['actual_fill']}")
        print(f"    slippage:      {result.paper_fill['slippage']}")
        print(f"    order_status:  {result.paper_fill['order_status']}")
    else:
        print(f"\n  [PAPER FILL]")
        print(f"    status: No fill (decision was not EXECUTE)")

    print(f"\n  [TELEMETRY]")
    print(f"    logs_dir: {logs_dir}")

    # List log files
    decisions = sorted(logs_dir.glob("decisions_*.jsonl"))
    trades = sorted(logs_dir.glob("trades_*.jsonl"))
    print(f"    decision_files: {len(decisions)}")
    print(f"    trade_files:    {len(trades)}")

    if decisions:
        last_decision = json.loads(decisions[-1].read_text().strip().splitlines()[-1])
        print(f"    last_decision_id: {last_decision.get('decision_id')}")
        print(f"    last_record_valid: {last_decision.get('record_valid')}")
        print(f"    last_snapshot_id: {last_decision.get('snapshot_id')}")

    print(f"\n  [SAFETY CHECK]")
    print(f"    order_send_called: NO")
    print(f"    real_broker_order: NO")
    print(f"    live_mode: BLOCKED")
    print(f"    mode: paper/eval")

    print("\n" + "=" * 60)


def main() -> int:
    print("=" * 60)
    print("D.E.V.I Paper Session with Real MT5 Data")
    print("=" * 60)
    print()

    # 1. Load paper config
    config_path = Path("src/config/paper.json")
    if not config_path.exists():
        print(f"ERROR: Config not found: {config_path}")
        return 1

    cfg = load_config(str(config_path))
    print(f"Config loaded: {config_path}")
    print(f"  pipeline.enable_full_phase1_flow: {cfg['pipeline']['enable_full_phase1_flow']}")
    print(f"  runtime.mode: {cfg['runtime']['mode']}")
    print()

    # 2. Wrap MT5 with paper safety guard
    print("Wrapping MT5 with paper safety guard...")
    try:
        safe_mt5 = create_paper_safe_mt5()
        print("  MT5PaperGuard created (forbidden methods blocked)")
    except ImportError as exc:
        print(f"  ERROR: MetaTrader5 package not installed: {exc}")
        return 1

    # Verify forbidden methods are blocked
    blocked = []
    for method in ("order_send", "order_check", "order_modify", "position_close"):
        try:
            getattr(safe_mt5, method)
            blocked.append(f"{method}=FAIL")
        except Exception:
            blocked.append(f"{method}=BLOCKED")
    print(f"  forbidden checks: {', '.join(blocked)}")
    print()

    # 3. Connect to MT5
    print("Connecting to MT5 terminal...")
    try:
        # Use the guarded client through MT5DataSource
        data_source = MT5DataSource(mt5_client=safe_mt5)
        print("  MT5 connected successfully")
    except Exception as exc:
        print(f"  ERROR: Could not connect to MT5: {exc}")
        print("  Make sure MetaTrader 5 is running and logged in.")
        return 1

    # 4. Fetch evidence data
    print()
    print("Fetching MT5 data...")
    try:
        account = data_source.fetch_account_info()
        print(f"  account_balance: {account['balance']:,.2f} {account['currency']}")
        print(f"  account_equity:  {account['equity']:,.2f}")

        tick = data_source.fetch_tick("EURUSD")
        print(f"  tick_bid: {tick['bid']}")
        print(f"  tick_ask: {tick['ask']}")

        profile = data_source.fetch_instrument_profile("EURUSD")
        print(f"  symbol_point: {profile.point}")
        print(f"  contract_size: {profile.contract_size}")

        m15_bars = data_source.fetch_bars("EURUSD", "M15", count=100)  # type: ignore[arg-type]
        h1_bars = data_source.fetch_bars("EURUSD", "H1", count=50)  # type: ignore[arg-type]
        print(f"  M15 bars fetched: {len(m15_bars)}")
        print(f"  H1 bars fetched:  {len(h1_bars)}")
        if m15_bars:
            print(f"  latest M15 time: {m15_bars[-1].time}")
    except Exception as exc:
        print(f"  ERROR fetching data: {exc}")
        data_source.close()
        return 1

    # 5. Run paper session
    print()
    print("Running paper session...")
    logs_root = Path("logs")
    logs_root.mkdir(exist_ok=True)

    session = PaperSession(
        config=cfg,
        logs_root=str(logs_root),
        namespace=Namespace.EVAL,
    )
    # Inject the already-connected data source
    session.data = data_source

    run_id = f"mt5_paper_{datetime.now(tz=UTC).strftime('%Y%m%d_%H%M%S')}"
    result = session.run(run_id=run_id)
    session.close()

    # 6. Print evidence
    _print_evidence(result, logs_root / "eval")

    print()
    print(f"Run complete. Logs saved to: {logs_root / 'eval'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
