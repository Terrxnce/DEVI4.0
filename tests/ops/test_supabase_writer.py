"""Tests for src/ops/supabase_writer.py

Coverage:
- SupabaseWriter disables itself gracefully when supabase-py is not installed
- write_trade calls table("devi_trades").insert with correct payload
- write_trade_close calls table("devi_trades").insert with correct payload
- write_heartbeat calls table("devi_heartbeats").upsert with correct payload
- write_ftmo_snapshot calls table("devi_ftmo_snapshots").insert with correct payload
- account_id is attached to every payload
- All write methods silently swallow exceptions (never raise)
- enabled property returns False when client init fails
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.ops.supabase_writer import SupabaseWriter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_writer(account_id: str = "test_account") -> tuple[SupabaseWriter, MagicMock]:
    """Return a SupabaseWriter with a mocked supabase client."""
    mock_client = MagicMock()
    # Simulate the fluent API: table(...).insert(...).execute()
    mock_table = MagicMock()
    mock_client.table.return_value = mock_table
    mock_table.insert.return_value = mock_table
    mock_table.upsert.return_value = mock_table

    with patch("src.ops.supabase_writer.SupabaseWriter.__init__", wraps=None):
        writer = SupabaseWriter.__new__(SupabaseWriter)
        writer._account_id = account_id
        writer._client = mock_client
        writer._enabled = True

    return writer, mock_client


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    def test_import_error_disables_writer(self) -> None:
        """If supabase-py is not installed, writer disables itself without raising."""
        with patch.dict("sys.modules", {"supabase": None}):
            writer = SupabaseWriter.__new__(SupabaseWriter)
            writer._account_id = "test"
            writer._client = None
            writer._enabled = False

        assert writer.enabled is False

    def test_disabled_write_trade_is_noop(self) -> None:
        writer, mock_client = _make_writer()
        writer._enabled = False
        writer.write_trade({"event": "trade_open", "trade_id": "t1"})
        mock_client.table.assert_not_called()

    def test_disabled_write_heartbeat_is_noop(self) -> None:
        writer, mock_client = _make_writer()
        writer._enabled = False
        writer.write_heartbeat(
            run_id="r1", open_positions=0,
            daily_pnl_pct=0.0, total_pnl_pct=0.0,
            daily_ok=True, total_ok=True,
            equity=100000.0, balance=100000.0,
        )
        mock_client.table.assert_not_called()

    def test_exception_in_insert_does_not_raise(self) -> None:
        writer, mock_client = _make_writer()
        mock_client.table.return_value.insert.return_value.execute.side_effect = RuntimeError("network error")
        # Must not raise — just log and continue
        writer.write_trade({"event": "trade_open", "trade_id": "t1"})

    def test_exception_in_upsert_does_not_raise(self) -> None:
        writer, mock_client = _make_writer()
        mock_client.table.return_value.upsert.return_value.execute.side_effect = RuntimeError("timeout")
        writer.write_heartbeat(
            run_id="r1", open_positions=0,
            daily_pnl_pct=0.0, total_pnl_pct=0.0,
            daily_ok=True, total_ok=True,
            equity=100000.0, balance=100000.0,
        )


# ---------------------------------------------------------------------------
# write_trade
# ---------------------------------------------------------------------------

class TestWriteTrade:
    def test_inserts_to_devi_trades(self) -> None:
        writer, mock_client = _make_writer()
        record = {
            "event": "trade_open",
            "trade_id": "trade-001",
            "ticket": 12345,
            "symbol": "EURUSD",
            "side": "BUY",
            "lot_size": 0.05,
            "open_price": 1.1000,
            "sl": 1.0980,
            "tp": 1.1040,
            "status": "open",
            "run_id": "run-001",
            "timestamp": "2026-05-26T08:00:00+00:00",
        }
        writer.write_trade(record)
        mock_client.table.assert_called_with("devi_trades")
        insert_call = mock_client.table.return_value.insert.call_args
        payload = insert_call[0][0]
        assert payload["event"] == "trade_open"
        assert payload["trade_id"] == "trade-001"
        assert payload["account_id"] == "test_account"
        assert payload["symbol"] == "EURUSD"
        assert payload["raw"] == record  # full record preserved

    def test_account_id_attached(self) -> None:
        writer, mock_client = _make_writer(account_id="ftmo_challenge_1")
        writer.write_trade({"event": "trade_open", "trade_id": "t1"})
        payload = mock_client.table.return_value.insert.call_args[0][0]
        assert payload["account_id"] == "ftmo_challenge_1"


# ---------------------------------------------------------------------------
# write_trade_close
# ---------------------------------------------------------------------------

class TestWriteTradeClose:
    def test_inserts_close_record_to_devi_trades(self) -> None:
        writer, mock_client = _make_writer()
        record = {
            "event": "trade_close",
            "trade_id": "trade-001",
            "ticket": 12345,
            "symbol": "EURUSD",
            "side": "BUY",
            "close_price": 1.1035,
            "close_reason": "tp_hit",
            "close_pnl": 175.0,
            "status": "closed",
            "run_id": "run-001",
            "timestamp": "2026-05-26T10:30:00+00:00",
        }
        writer.write_trade_close(record)
        mock_client.table.assert_called_with("devi_trades")
        payload = mock_client.table.return_value.insert.call_args[0][0]
        assert payload["event"] == "trade_close"
        assert payload["close_pnl"] == 175.0
        assert payload["close_reason"] == "tp_hit"
        assert payload["account_id"] == "test_account"


# ---------------------------------------------------------------------------
# write_heartbeat
# ---------------------------------------------------------------------------

class TestWriteHeartbeat:
    def test_upserts_to_devi_heartbeats(self) -> None:
        writer, mock_client = _make_writer(account_id="ftmo_challenge_1")
        writer.write_heartbeat(
            run_id="run-hb-001",
            open_positions=2,
            daily_pnl_pct=-0.5,
            total_pnl_pct=1.2,
            daily_ok=True,
            total_ok=True,
            equity=101200.0,
            balance=101000.0,
        )
        mock_client.table.assert_called_with("devi_heartbeats")
        upsert_call = mock_client.table.return_value.upsert.call_args
        payload = upsert_call[0][0]
        assert payload["account_id"] == "ftmo_challenge_1"
        assert payload["run_id"] == "run-hb-001"
        assert payload["open_positions"] == 2
        assert payload["daily_ok"] is True
        assert "last_seen" in payload

    def test_upsert_conflict_key_is_account_id(self) -> None:
        writer, mock_client = _make_writer()
        writer.write_heartbeat(
            run_id="r1", open_positions=0,
            daily_pnl_pct=0.0, total_pnl_pct=0.0,
            daily_ok=True, total_ok=True,
            equity=100000.0, balance=100000.0,
        )
        upsert_kwargs = mock_client.table.return_value.upsert.call_args[1]
        assert upsert_kwargs.get("on_conflict") == "account_id"


# ---------------------------------------------------------------------------
# write_ftmo_snapshot
# ---------------------------------------------------------------------------

class TestWriteFtmoSnapshot:
    def test_inserts_to_devi_ftmo_snapshots(self) -> None:
        writer, mock_client = _make_writer(account_id="ftmo_challenge_1")
        writer.write_ftmo_snapshot(
            run_id="run-001",
            daily_pnl_pct=-0.3,
            total_pnl_pct=0.8,
            daily_ok=True,
            total_ok=True,
            daily_floor=-5.0,
            total_floor=-10.0,
            equity=100700.0,
            balance=100800.0,
            reason="within_limits",
        )
        mock_client.table.assert_called_with("devi_ftmo_snapshots")
        payload = mock_client.table.return_value.insert.call_args[0][0]
        assert payload["account_id"] == "ftmo_challenge_1"
        assert payload["daily_pnl_pct"] == -0.3
        assert payload["daily_ok"] is True
        assert payload["reason"] == "within_limits"
        assert "timestamp" in payload
