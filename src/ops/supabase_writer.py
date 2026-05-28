"""SupabaseWriter — mirrors D.E.V.I telemetry to a remote Supabase database.

Design rules:
- JSONL files remain the primary audit trail. Supabase is additive.
- Every write is wrapped in try/except. A Supabase failure logs a warning
  and does NOT propagate. The bot never crashes because of this module.
- The supabase-py library is imported lazily. If it is not installed,
  SupabaseWriter logs a warning and disables itself. Paper mode and tests
  that do not install the library are unaffected.
- Credentials come from environment variables only. Never hardcoded.

Environment variables:
    SUPABASE_URL   — your project URL (https://xxxx.supabase.co)
    SUPABASE_KEY   — service_role key (not anon key — gives full write access)

Tables written:
    devi_trades           — one row per trade_open / trade_close event
    devi_heartbeats       — one row per account_id, upserted every cycle
    devi_ftmo_snapshots   — FTMO risk snapshot per cycle
    devi_live_positions   — one row per open ticket, upserted every cycle with floating P&L
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


class SupabaseWriter:
    """Pushes D.E.V.I events to Supabase in real time.

    Args:
        url:        Supabase project URL from env var SUPABASE_URL.
        key:        Supabase service_role API key from env var SUPABASE_KEY.
        account_id: Human-readable identifier for this trading account
                    (e.g. "ftmo_challenge_1"). Used to filter data per account
                    in the dashboard. Stored on every record.
    """

    def __init__(self, url: str, key: str, account_id: str = "default") -> None:
        self._account_id = account_id
        self._client: Any = None
        self._enabled = False

        try:
            from supabase import create_client  # type: ignore[import]
            self._client = create_client(url, key)
            self._enabled = True
            logger.info(
                "supabase_writer: connected account_id=%s url=%s",
                account_id,
                url[:40] + "...",
            )
        except ImportError:
            logger.warning(
                "supabase_writer: supabase-py not installed — "
                "remote writes disabled. Run: pip install supabase"
            )
        except Exception as exc:
            logger.warning(
                "supabase_writer: failed to connect — remote writes disabled. %s", exc
            )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    def write_trade(self, record: dict[str, Any]) -> None:
        """Mirror a trade_open record to devi_trades."""
        if not self._enabled:
            return
        payload = self._trade_payload(record)
        self._insert("devi_trades", payload)

    def write_trade_close(self, record: dict[str, Any]) -> None:
        """Mirror a trade_close record to devi_trades."""
        if not self._enabled:
            return
        payload = self._trade_payload(record)
        self._insert("devi_trades", payload)

    def write_heartbeat(
        self,
        *,
        run_id: str,
        open_positions: int,
        daily_pnl_pct: float,
        total_pnl_pct: float,
        daily_ok: bool,
        total_ok: bool,
        equity: float,
        balance: float,
    ) -> None:
        """Upsert a heartbeat row for this account.

        There is always exactly one row per account_id in devi_heartbeats.
        The dashboard reads this to confirm D.E.V.I is alive and check
        current FTMO position without loading the full snapshot table.
        """
        if not self._enabled:
            return
        payload = {
            "account_id": self._account_id,
            "run_id": run_id,
            "last_seen": datetime.now(tz=UTC).isoformat(),
            "open_positions": open_positions,
            "daily_pnl_pct": round(daily_pnl_pct, 4),
            "total_pnl_pct": round(total_pnl_pct, 4),
            "daily_ok": daily_ok,
            "total_ok": total_ok,
            "equity": equity,
            "balance": balance,
        }
        self._upsert("devi_heartbeats", payload, on_conflict="account_id")

    def write_ftmo_snapshot(
        self,
        *,
        run_id: str,
        daily_pnl_pct: float,
        total_pnl_pct: float,
        daily_ok: bool,
        total_ok: bool,
        daily_floor: float,
        total_floor: float,
        equity: float,
        balance: float,
        reason: str,
    ) -> None:
        """Insert a point-in-time FTMO risk snapshot to devi_ftmo_snapshots."""
        if not self._enabled:
            return
        payload = {
            "account_id": self._account_id,
            "run_id": run_id,
            "daily_pnl_pct": round(daily_pnl_pct, 4),
            "total_pnl_pct": round(total_pnl_pct, 4),
            "daily_ok": daily_ok,
            "total_ok": total_ok,
            "daily_floor": round(daily_floor, 4),
            "total_floor": round(total_floor, 4),
            "equity": equity,
            "balance": balance,
            "reason": reason,
            "timestamp": datetime.now(tz=UTC).isoformat(),
        }
        self._insert("devi_ftmo_snapshots", payload)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _trade_payload(self, record: dict[str, Any]) -> dict[str, Any]:
        """Normalise a raw telemetry record into the devi_trades schema."""
        return {
            "account_id": self._account_id,
            "event": record.get("event"),
            "trade_id": record.get("trade_id"),
            "decision_id": record.get("decision_id"),
            "ticket": record.get("ticket"),
            "symbol": record.get("symbol"),
            "side": record.get("side"),
            "lot_size": record.get("lot_size"),
            "open_price": record.get("open_price"),
            "close_price": record.get("close_price"),
            "close_time": record.get("close_time"),
            "close_reason": record.get("close_reason"),
            "close_pnl": record.get("close_pnl"),
            "sl": record.get("sl"),
            "tp": record.get("tp"),
            "status": record.get("status"),
            "run_id": record.get("run_id"),
            "timestamp": record.get("timestamp") or datetime.now(tz=UTC).isoformat(),
            "raw": record,  # full record stored as jsonb for future-proofing
        }

    def sync_live_positions(self, positions: list[dict[str, Any]]) -> None:
        """Sync open position state to devi_live_positions.

        Upserts every currently open position with its floating P&L.
        Deletes rows for any ticket no longer in the open list so the
        table always reflects the exact live state.

        Called once per cycle after all position management is complete.
        Safe to call with an empty list — clears the table for this account.
        """
        if not self._enabled:
            return

        open_tickets: list[int] = []
        _now = datetime.now(tz=UTC).isoformat()
        for pos in positions:
            ticket = pos.get("ticket")
            if not ticket:
                continue
            open_tickets.append(int(ticket))
            payload = {
                "account_id": self._account_id,
                "ticket": ticket,
                "trade_id": pos.get("trade_id"),
                "decision_id": pos.get("decision_id"),
                "symbol": pos.get("symbol"),
                "side": pos.get("side"),
                "lot_size": pos.get("lot_size"),
                "open_price": pos.get("open_price"),
                "current_price": pos.get("current_price"),
                "sl": pos.get("sl"),
                "tp": pos.get("tp"),
                "profit": pos.get("profit"),
                "swap": pos.get("swap"),
                "open_time": pos.get("open_time"),
                "last_updated": _now,
            }
            self._upsert("devi_live_positions", payload, on_conflict="account_id,ticket")

        # Remove stale rows — tickets no longer open.
        try:
            query = (
                self._client.table("devi_live_positions")
                .delete()
                .eq("account_id", self._account_id)
            )
            if open_tickets:
                query = query.not_.in_("ticket", open_tickets)
            query.execute()
        except Exception as exc:
            logger.warning("supabase_writer: cleanup devi_live_positions failed: %s", exc)

    def _insert(self, table: str, payload: dict[str, Any]) -> None:
        try:
            self._client.table(table).insert(payload).execute()
        except Exception as exc:
            logger.warning("supabase_writer: insert to %s failed: %s", table, exc)

    def _upsert(
        self,
        table: str,
        payload: dict[str, Any],
        on_conflict: str,
    ) -> None:
        try:
            self._client.table(table).upsert(
                payload, on_conflict=on_conflict
            ).execute()
        except Exception as exc:
            logger.warning("supabase_writer: upsert to %s failed: %s", table, exc)
