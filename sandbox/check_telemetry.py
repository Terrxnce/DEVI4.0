"""Check latest telemetry files after MT5 paper session."""
import json
from pathlib import Path

log_dir = Path("logs/eval")

# Check latest decision log
decisions = sorted(log_dir.glob("decisions_*.jsonl"))
if decisions:
    with open(decisions[-1]) as f:
        for line in f:
            pass
        last = json.loads(line)
    print("=== LATEST DECISION RECORD ===")
    for k in ['decision_id', 'run_id', 'final_decision', 'failure_code',
              'record_valid', 'record_invalid_reasons', 'snapshot_id',
              'sl_distance_price', 'sl_distance_points', 'sl_distance_pips',
              'symbol', 'session']:
        print(f"  {k}: {last.get(k)}")
else:
    print("No decision files found")

# Check snapshots
snaps = sorted(log_dir.glob("snapshots_*.jsonl"))
print(f"\n=== SNAPSHOTS ===")
print(f"  count: {len(snaps)}")
if snaps:
    with open(snaps[-1]) as f:
        for line in f:
            pass
        last_snap = json.loads(line)
    print(f"  latest_id: {last_snap.get('snapshot_id')}")
    print(f"  symbol: {last_snap.get('symbol')}")
    print(f"  m15_bars_count: {len(last_snap.get('m15_bars', []))}")
    print(f"  h1_bars_count: {len(last_snap.get('h1_bars', []))}")
