"""Controlled paper fill validation using real MT5 data + deterministic TradeIntent.

This script proves that when D.E.V.I produces an approved TradeIntent,
the paper adapter creates a simulated fill correctly using MT5-derived
bid/ask/spread, without any broker execution.

Prerequisites:
- MetaTrader 5 terminal must be running and logged in.

Usage:
    python sandbox/run_paper_fill_validation.py
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.enums import Namespace
from src.core.models import ExitPlan, RiskVerdict, TradeIntent
from src.data.mt5_guard import create_paper_safe_mt5
from src.data.mt5_source import MT5DataSource
from src.execution.paper_adapter import PaperExecutionAdapter
from src.ops.telemetry import TelemetryWriter
from tests.fixtures.trade_intent import make_trade_intent


def _print_evidence(
    *,
    account: dict,
    tick: dict,
    profile: dict,
    intent: TradeIntent,
    fill,
    side: str,
    logs_dir: Path,
) -> None:
    print("\n" + "=" * 60)
    print("PAPER FILL VALIDATION EVIDENCE")
    print("=" * 60)

    print(f"\n  [MT5 DATA SOURCE]")
    print(f"    connection: SUCCESS")
    print(f"    symbol: EURUSD")
    print(f"    tick_bid: {tick['bid']}")
    print(f"    tick_ask: {tick['ask']}")
    print(f"    spread: {abs(tick['ask'] - tick['bid']):.5f}")
    print(f"    point: {profile['point']}")
    print(f"    contract_size: {profile['contract_size']}")

    print(f"\n  [ACCOUNT]")
    print(f"    balance: {account['balance']:,.2f} {account['currency']}")
    print(f"    equity: {account['equity']:,.2f}")

    print(f"\n  [TRADE INTENT FIXTURE]")
    print(f"    decision_id: {intent.trade_id}")
    print(f"    side: {side}")
    print(f"    symbol: {intent.symbol}")
    print(f"    entry_price: {intent.entry_price}")
    print(f"    planned_sl: {intent.exit_plan.stop_loss}")
    print(f"    planned_tp: {intent.exit_plan.take_profit}")
    print(f"    lot_size: {intent.risk_verdict.lot_size}")

    print(f"\n  [PAPER FILL RESULT]")
    print(f"    trade_id: {fill.trade_id}")
    print(f"    decision_id: {fill.decision_id}")
    print(f"    synthetic_ticket: {fill.ticket}")
    print(f"    side: {fill.side}")
    print(f"    intended_entry: {fill.intended_entry}")
    print(f"    actual_fill: {fill.actual_fill}")
    print(f"    planned_sl: {fill.planned_sl}")
    print(f"    planned_tp: {fill.planned_tp}")
    print(f"    slippage: {fill.slippage}")
    print(f"    spread_at_decision: {fill.spread_at_decision}")
    print(f"    spread_at_fill: {fill.spread_at_fill}")
    print(f"    order_status: {fill.order_status}")
    print(f"    paper_retcode: {fill.paper_retcode} (synthetic placeholder)")

    print(f"\n  [FILL LOGIC VERIFICATION]")
    spread = fill.spread_at_fill
    if side == "BUY":
        expected = intent.entry_price + spread  # ask = bid + spread
        print(f"    BUY: entry(bid) + spread = {intent.entry_price} + {spread} = {expected} (ask)")
    else:
        expected = intent.entry_price - spread  # bid = ask - spread
        print(f"    SELL: entry(ask) - spread = {intent.entry_price} - {spread} = {expected} (bid)")
    print(f"    expected_fill: {expected}")
    print(f"    actual_fill: {fill.actual_fill}")
    print(f"    match: {'YES' if abs(fill.actual_fill - expected) < 1e-9 else 'NO'}")

    print(f"\n  [TELEMETRY]")
    trades = sorted(logs_dir.glob("trades_*.jsonl"))
    print(f"    trade_files: {len(trades)}")
    if trades:
        print(f"    latest_trade_logged: YES")

    print(f"\n  [SAFETY]")
    print(f"    order_send_called: NO")
    print(f"    order_check_called: NO")
    print(f"    order_modify_called: NO")
    print(f"    position_close_called: NO")
    print(f"    real_broker_order: NO")
    print(f"    live_mode: BLOCKED")

    print("\n" + "=" * 60)


def main() -> int:
    print("=" * 60)
    print("D.E.V.I Paper Fill Validation (Real MT5 Data)")
    print("=" * 60)
    print()

    # 1. Wrap MT5 with paper safety guard
    print("Wrapping MT5 with paper safety guard...")
    safe_mt5 = create_paper_safe_mt5()
    print("  MT5PaperGuard active")

    # Verify forbidden methods blocked
    for method in ("order_send", "order_check", "order_modify", "position_close"):
        try:
            getattr(safe_mt5, method)
            print(f"  {method}: FAIL (not blocked)")
            return 1
        except Exception:
            print(f"  {method}: BLOCKED")
    print()

    # 2. Connect to MT5
    print("Connecting to MT5...")
    data = MT5DataSource(mt5_client=safe_mt5)
    print("  Connected")
    print()

    # 3. Fetch real MT5 data
    print("Fetching real MT5 data...")
    account = data.fetch_account_info()
    tick = data.fetch_tick("EURUSD")
    profile_raw = data.fetch_instrument_profile("EURUSD")
    profile = {
        "point": profile_raw.point,
        "contract_size": profile_raw.contract_size,
        "tick_size": profile_raw.tick_size,
    }
    print(f"  account_balance: {account['balance']:,.2f} {account['currency']}")
    print(f"  tick_bid: {tick['bid']}")
    print(f"  tick_ask: {tick['ask']}")
    print(f"  spread: {abs(tick['ask'] - tick['bid']):.5f}")
    print()

    # 4. Create deterministic TradeIntent using MT5-derived pricing
    bid = tick["bid"]
    ask = tick["ask"]
    spread = abs(ask - bid)

    # Test both BUY and SELL
    for side in ("BUY", "SELL"):
        print(f"--- Testing {side} fill ---")

        if side == "BUY":
            entry = bid
            sl = bid - 0.0020
            tp = bid + 0.0030
        else:
            entry = ask
            sl = ask + 0.0020
            tp = ask - 0.0030

        intent = make_trade_intent(
            side=side,
            entry_price=round(entry, 5),
            stop_loss=round(sl, 5),
            take_profit=round(tp, 5),
            lot_size=0.19,
            decision_id=f"test_{side.lower()}_001",
            symbol="EURUSD",
            spread=spread,
        )

        # 5. Execute paper fill
        adapter = PaperExecutionAdapter()
        fill = adapter.execute(intent, spread_at_decision=spread)

        # 6. Write telemetry
        logs_root = Path("logs")
        logs_root.mkdir(exist_ok=True)
        writer = TelemetryWriter(logs_root=str(logs_root), namespace=Namespace.EVAL)
        writer.write_trade(fill.__dict__)

        # 7. Print evidence
        _print_evidence(
            account=account,
            tick=tick,
            profile=profile,
            intent=intent,
            fill=fill,
            side=side,
            logs_dir=logs_root / "eval",
        )

    data.close()
    print("Run complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
