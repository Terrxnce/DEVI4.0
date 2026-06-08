"""Parse the most recent live decisions log to reveal rejected setups."""
import argparse
import json
import sys
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="Parse ghost trades from telemetry")
    parser.add_argument("--date", help="Specific date to parse (YYYY-MM-DD)", default=None)
    parser.add_argument("--show-all", action="store_true", help="Show all evaluations, including 'no_setup_match'")
    args = parser.parse_args()

    log_dir = Path("logs/prod")
    if not log_dir.exists():
        print(f"Directory {log_dir} does not exist. Run a scan first.")
        return

    if args.date:
        files = list(log_dir.glob(f"decisions_{args.date}.jsonl"))
        if not files:
            print(f"No decisions log found for date {args.date}")
            return
        latest_file = files[0]
    else:
        files = sorted(log_dir.glob("decisions_*.jsonl"))
        if not files:
            print("No decisions logs found.")
            return
        latest_file = files[-1]

    print(f"Parsing {latest_file.name} for evaluated setups...\n")
    print(f"{'Time (UTC)':<20} | {'Symbol':<10} | {'Setup':<20} | {'Tier'} | {'Rejection Reason':<30} | {'Entry':<8} | {'RR'}")
    print("-" * 120)

    found_any = False
    with open(latest_file, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            data = json.loads(line)
            failure_code = data.get("failure_code", "")
            
            if not args.show_all:
                # Skip normal non-setups and generic session closures
                if failure_code in ["no_setup_match", "setup_disabled", "pipeline_full_flow_disabled"]: continue
                if str(failure_code).startswith("outside_symbol_session"): continue
            
            symbol = data.get("symbol", "UNKNOWN")
            timestamp = str(data.get("timestamp", ""))[:19].replace("T", " ")
            
            # Setup class & tier might be flat or nested
            confluence = data.get("confluence", {})
            setup_class = data.get("setup_class") or (confluence.get("setup_class", "N/A") if isinstance(confluence, dict) else "N/A")
            tier = data.get("confidence_tier") or (confluence.get("confidence_tier", "-") if isinstance(confluence, dict) else "-")
            
            exit_plan = data.get("exit_plan", {})
            entry_price = exit_plan.get("entry_price") if isinstance(exit_plan, dict) else data.get("entry_price")
            rr = exit_plan.get("rr") if isinstance(exit_plan, dict) else data.get("rr")
            
            entry_str = f"{entry_price:.2f}" if isinstance(entry_price, (float, int)) else "-"
            rr_str = f"{rr:.2f}" if isinstance(rr, (float, int)) else "-"
            
            print(f"{timestamp:<20} | {symbol:<10} | {setup_class:<20} | {tier:<4} | {failure_code:<30} | {entry_str:<8} | {rr_str}")
            found_any = True

    if not found_any:
        if not args.show_all:
            print("No ghost trades found. (Use --show-all to see 'no_setup_match' evaluations)")
        else:
            print("No logs found in this file.")

if __name__ == "__main__":
    main()