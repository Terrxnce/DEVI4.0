"""Backfill historical D.E.V.I telemetry into Supabase.

Reads prod JSONL logs and inserts missing records into:
    devi_trades           — from trades_*.jsonl  (open + close events)
    devi_ftmo_snapshots   — from position_events_*.jsonl and snapshots_*.jsonl

Deduplication:
    Trades   — fetches existing (ticket, event) pairs before inserting.
    Snapshots — inserts all historical records (time-series, duplicates
                acceptable; each cycle is a distinct data point).

Usage:
    python tools/backfill_supabase.py
    python tools/backfill_supabase.py --dry-run
    python tools/backfill_supabase.py --trades-only
    python tools/backfill_supabase.py --snapshots-only

Env vars required:
    SUPABASE_URL
    SUPABASE_KEY   (service_role key — not anon)

Optional:
    DEVI_ACCOUNT_ID  (default: ftmo_challenge_1)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

LOGS_ROOT = Path(__file__).parent.parent / "logs" / "prod"
ACCOUNT_ID = os.environ.get("DEVI_ACCOUNT_ID", "ftmo_challenge_1")
BATCH_SIZE = 100


# ---------------------------------------------------------------------------
# JSONL loader
# ---------------------------------------------------------------------------

def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    logger.warning("  Bad JSON line %d in %s: %s", lineno, path.name, exc)
    except FileNotFoundError:
        logger.warning("File not found: %s", path)
    return records


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _extract_symbol(record: dict[str, Any]) -> str | None:
    """Extract symbol from record, falling back to parsing decision_id."""
    sym = record.get("symbol")
    if sym:
        return sym
    # decision_id format: live_scan_loop_001_EURUSD_dec
    dec_id = record.get("decision_id", "")
    if dec_id:
        parts = dec_id.split("_")
        # Symbol is the second-to-last part before 'dec'
        if len(parts) >= 2 and parts[-1] == "dec":
            return parts[-2]
    return None


def trade_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Normalise a trades JSONL record to devi_trades schema."""
    # Determine event type from explicit field or infer from status
    event = record.get("event")
    if not event:
        status = record.get("status", "")
        event = "trade_close" if status == "closed" else "trade_open"

    # open_price: try multiple field names
    open_price = (
        record.get("open_price")
        or record.get("actual_fill")
        or record.get("intended_entry")
    )

    return {
        "account_id": ACCOUNT_ID,
        "event": event,
        "trade_id": record.get("trade_id"),
        "decision_id": record.get("decision_id"),
        "ticket": record.get("ticket"),
        "symbol": _extract_symbol(record),
        "side": record.get("side"),
        "lot_size": record.get("lot_size"),
        "open_price": open_price,
        "close_price": record.get("close_price"),
        "close_time": record.get("close_time"),
        "close_reason": record.get("close_reason"),
        "close_pnl": record.get("close_pnl"),
        "sl": record.get("sl"),
        "tp": record.get("tp"),
        "status": record.get("status", "open"),
        "run_id": record.get("run_id"),
        "timestamp": record.get("timestamp") or _now_iso(),
        "raw": record,
    }


def snapshot_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Normalise a JSONL record to devi_ftmo_snapshots schema."""
    return {
        "account_id": ACCOUNT_ID,
        "run_id": record.get("run_id", ""),
        "daily_pnl_pct": round(float(record.get("daily_pnl_pct", 0)), 4),
        "total_pnl_pct": round(float(record.get("total_pnl_pct", 0)), 4),
        "daily_ok": bool(record.get("daily_ok", True)),
        "total_ok": bool(record.get("total_ok", True)),
        "daily_floor": round(float(record.get("daily_floor", 0)), 4),
        "total_floor": round(float(record.get("total_floor", 0)), 4),
        "equity": float(record.get("equity", 0)),
        "balance": float(record.get("balance", 0)),
        "reason": record.get("reason", ""),
        "timestamp": record.get("timestamp") or _now_iso(),
    }


# ---------------------------------------------------------------------------
# Batch insert helper
# ---------------------------------------------------------------------------

def batch_insert(client: Any, table: str, rows: list[dict], dry_run: bool) -> int:
    """Insert rows in batches of BATCH_SIZE. Returns count of attempted inserts."""
    if not rows:
        return 0
    if dry_run:
        return len(rows)
    inserted = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        try:
            client.table(table).insert(batch).execute()
            inserted += len(batch)
        except Exception as exc:
            logger.warning("Batch insert to %s failed (offset %d): %s", table, i, exc)
            # Fall back to one-by-one
            for row in batch:
                try:
                    client.table(table).insert(row).execute()
                    inserted += 1
                except Exception as exc2:
                    logger.warning("Single insert failed: %s", exc2)
    return inserted


# ---------------------------------------------------------------------------
# Trades backfill
# ---------------------------------------------------------------------------

def backfill_trades(client: Any, dry_run: bool) -> None:
    logger.info("=== TRADES ===")

    # Fetch existing (ticket, event) pairs to avoid duplicates
    existing_keys: set[tuple] = set()
    if client and not dry_run:
        try:
            # Supabase limits selects to 1000 rows by default — paginate if needed
            offset = 0
            while True:
                resp = (
                    client.table("devi_trades")
                    .select("ticket,event")
                    .eq("account_id", ACCOUNT_ID)
                    .range(offset, offset + 999)
                    .execute()
                )
                batch = resp.data or []
                for row in batch:
                    existing_keys.add((row.get("ticket"), row.get("event")))
                if len(batch) < 1000:
                    break
                offset += 1000
            logger.info("Existing trade records in Supabase: %d", len(existing_keys))
        except Exception as exc:
            logger.warning("Could not fetch existing trades (will insert all): %s", exc)

    trade_files = sorted(LOGS_ROOT.glob("trades_*.jsonl"))
    if not trade_files:
        logger.warning("No trades_*.jsonl files found in %s", LOGS_ROOT)
        return

    to_insert: list[dict] = []
    skipped = 0
    seen_keys: set[tuple] = set(existing_keys)

    for f in trade_files:
        records = load_jsonl(f)
        file_new = 0
        for r in records:
            status = r.get("status", "")
            order_status = r.get("order_status", "")
            event_field = r.get("event")

            # Skip blocked/rejected executions
            if status == "rejected":
                continue
            if order_status and "blocked" in order_status and status not in ("closed", "open"):
                continue
            # Must have a ticket or be an explicit close event
            if not r.get("ticket") and event_field != "trade_close":
                continue

            payload = trade_payload(r)
            key = (payload.get("ticket"), payload.get("event"))

            # Deduplicate within this run too
            if key in seen_keys:
                skipped += 1
                continue

            seen_keys.add(key)
            to_insert.append(payload)
            file_new += 1

        logger.info("  %-45s  records=%d  new=%d", f.name, len(records), file_new)

    logger.info("Total to insert: %d  |  Skipped (duplicate): %d", len(to_insert), skipped)

    if to_insert:
        inserted = batch_insert(client, "devi_trades", to_insert, dry_run)
        logger.info("Inserted: %d trade records", inserted)


# ---------------------------------------------------------------------------
# Snapshots backfill
# ---------------------------------------------------------------------------

def backfill_snapshots(client: Any, dry_run: bool) -> None:
    logger.info("=== SNAPSHOTS ===")

    to_insert: list[dict] = []

    # --- position_events_*.jsonl (May 14+) ---
    # These files contain ftmo_risk_snapshot events mixed with position events.
    event_files = sorted(LOGS_ROOT.glob("position_events_*.jsonl"))
    event_dates: set[str] = set()

    for f in event_files:
        date_str = f.stem.replace("position_events_", "")
        event_dates.add(date_str)
        records = load_jsonl(f)
        snaps = [r for r in records if r.get("event") == "ftmo_risk_snapshot"]
        for r in snaps:
            to_insert.append(snapshot_payload(r))
        logger.info("  %-45s  total=%d  snapshots=%d", f.name, len(records), len(snaps))

    # --- snapshots_*.jsonl (older dates without position_events) ---
    snap_files = sorted(LOGS_ROOT.glob("snapshots_*.jsonl"))
    for f in snap_files:
        date_str = f.stem.replace("snapshots_", "")
        if date_str in event_dates:
            # Already covered by position_events
            logger.info("  %-45s  SKIPPED (covered by position_events)", f.name)
            continue
        records = load_jsonl(f)
        valid = [r for r in records if "balance" in r and "equity" in r]
        for r in valid:
            if "event" not in r:
                r["event"] = "ftmo_risk_snapshot"
            to_insert.append(snapshot_payload(r))
        logger.info("  %-45s  total=%d  snapshots=%d", f.name, len(records), len(valid))

    logger.info("Total snapshots to insert: %d", len(to_insert))

    if to_insert:
        inserted = batch_insert(client, "devi_ftmo_snapshots", to_insert, dry_run)
        logger.info("Inserted: %d snapshot records", inserted)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill historical D.E.V.I telemetry into Supabase"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Count records without inserting anything")
    parser.add_argument("--trades-only", action="store_true")
    parser.add_argument("--snapshots-only", action="store_true")
    args = parser.parse_args()

    sb_url = os.environ.get("SUPABASE_URL", "")
    sb_key = os.environ.get("SUPABASE_KEY", "")

    if not args.dry_run and (not sb_url or not sb_key):
        logger.error("SUPABASE_URL and SUPABASE_KEY env vars required (or use --dry-run)")
        sys.exit(1)

    client: Any = None
    if not args.dry_run:
        try:
            from supabase import create_client  # type: ignore[import]
            client = create_client(sb_url, sb_key)
            logger.info("Connected to Supabase — account_id=%s", ACCOUNT_ID)
        except ImportError:
            logger.error("supabase not installed: python -m pip install supabase")
            sys.exit(1)
        except Exception as exc:
            logger.error("Supabase connection failed: %s", exc)
            sys.exit(1)
    else:
        logger.info("DRY RUN — no records will be inserted")
        logger.info("account_id=%s  logs_root=%s", ACCOUNT_ID, LOGS_ROOT)

    if not args.snapshots_only:
        backfill_trades(client, args.dry_run)

    if not args.trades_only:
        backfill_snapshots(client, args.dry_run)

    logger.info("Done.")


if __name__ == "__main__":
    main()
