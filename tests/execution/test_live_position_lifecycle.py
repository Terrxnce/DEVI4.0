"""Tests for LivePositionTracker position lifecycle — close detection and history enrichment.

All tests use mocked MT5 clients. No real broker calls.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.execution.live_position_tracker import (
    LivePosition,
    LivePositionTracker,
    _map_deal_reason,
)


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


class _MockMTPosition:
    def __init__(
        self,
        *,
        ticket: int,
        symbol: str = "EURUSD",
        pos_type: int = 0,
        volume: float = 0.01,
        price_open: float = 1.1000,
        price_current: float = 1.1010,
        sl: float = 1.0960,
        tp: float = 1.1080,
        profit: float = 10.0,
        swap: float = 0.0,
        comment: str = "devi:run:trade",
    ) -> None:
        self.ticket = ticket
        self.symbol = symbol
        self.type = pos_type
        self.volume = volume
        self.price_open = price_open
        self.price_current = price_current
        self.sl = sl
        self.tp = tp
        self.profit = profit
        self.swap = swap
        self.comment = comment


class _MockDeal:
    def __init__(
        self,
        *,
        entry: int = 1,  # DEAL_ENTRY_OUT
        price: float = 1.1080,
        profit: float = 40.0,
        reason: int = 5,  # DEAL_REASON_TP
        time: int = 1_700_000_000,
    ) -> None:
        self.entry = entry
        self.price = price
        self.profit = profit
        self.reason = reason
        self.time = time


class _MockMT5:
    def __init__(
        self,
        *,
        positions: list[_MockMTPosition] | None = None,
        deals: list[_MockDeal] | None = None,
        history_raises: bool = False,
    ) -> None:
        self._positions = positions or []
        self._deals = deals or []
        self._history_raises = history_raises

    def positions_get(self) -> list[_MockMTPosition]:
        return self._positions

    def history_deals_get(self, *, position: int) -> list[_MockDeal]:
        if self._history_raises:
            raise RuntimeError("history unavailable")
        return self._deals


# ---------------------------------------------------------------------------
# _map_deal_reason
# ---------------------------------------------------------------------------


def test_map_deal_reason_sl() -> None:
    assert _map_deal_reason(4) == "sl_hit"


def test_map_deal_reason_tp() -> None:
    assert _map_deal_reason(5) == "tp_hit"


def test_map_deal_reason_stop_out() -> None:
    assert _map_deal_reason(6) == "stop_out"


def test_map_deal_reason_expert() -> None:
    assert _map_deal_reason(3) == "bot_closed"


def test_map_deal_reason_unknown_returns_manually_closed() -> None:
    assert _map_deal_reason(0) == "manually_closed"
    assert _map_deal_reason(99) == "manually_closed"


# ---------------------------------------------------------------------------
# sync_positions — basic behaviour
# ---------------------------------------------------------------------------


def test_sync_with_no_positions_returns_empty() -> None:
    tracker = LivePositionTracker(_MockMT5(positions=[]))
    result = tracker.sync_positions()
    assert result == []


def test_sync_records_open_position() -> None:
    pos = _MockMTPosition(ticket=1001)
    tracker = LivePositionTracker(_MockMT5(positions=[pos]))
    open_list = tracker.sync_positions()
    assert len(open_list) == 1
    assert open_list[0].ticket == 1001
    assert open_list[0].status == "OPEN"


def test_sync_marks_disappeared_position_as_closed() -> None:
    pos = _MockMTPosition(ticket=1001)
    mt5 = _MockMT5(positions=[pos])
    tracker = LivePositionTracker(mt5)
    tracker.sync_positions()  # position is OPEN

    # Position disappears from MT5
    mt5._positions = []
    tracker.sync_positions()

    lp = tracker.get_position(1001)
    assert lp is not None
    assert lp.status == "CLOSED"
    assert lp.close_time is not None


# ---------------------------------------------------------------------------
# get_newly_closed
# ---------------------------------------------------------------------------


def test_get_newly_closed_empty_on_first_sync() -> None:
    pos = _MockMTPosition(ticket=1001)
    tracker = LivePositionTracker(_MockMT5(positions=[pos]))
    tracker.sync_positions()
    assert tracker.get_newly_closed() == []


def test_get_newly_closed_returns_position_that_just_closed() -> None:
    pos = _MockMTPosition(ticket=1001)
    mt5 = _MockMT5(positions=[pos])
    tracker = LivePositionTracker(mt5)
    tracker.sync_positions()

    # Position disappears
    mt5._positions = []
    tracker.sync_positions()

    newly_closed = tracker.get_newly_closed()
    assert len(newly_closed) == 1
    assert newly_closed[0].ticket == 1001


def test_get_newly_closed_clears_on_next_sync() -> None:
    pos = _MockMTPosition(ticket=1001)
    mt5 = _MockMT5(positions=[pos])
    tracker = LivePositionTracker(mt5)
    tracker.sync_positions()

    mt5._positions = []
    tracker.sync_positions()
    assert len(tracker.get_newly_closed()) == 1

    # Next sync — nothing new closed
    tracker.sync_positions()
    assert tracker.get_newly_closed() == []


# ---------------------------------------------------------------------------
# History enrichment on close
# ---------------------------------------------------------------------------


def test_close_enriched_with_tp_hit_from_history() -> None:
    pos = _MockMTPosition(ticket=2001)
    out_deal = _MockDeal(entry=1, price=1.1080, profit=40.0, reason=5, time=1_700_000_000)
    mt5 = _MockMT5(positions=[pos], deals=[out_deal])
    tracker = LivePositionTracker(mt5)
    tracker.sync_positions()

    mt5._positions = []
    tracker.sync_positions()

    lp = tracker.get_position(2001)
    assert lp is not None
    assert lp.close_reason == "tp_hit"
    assert lp.close_price == 1.1080
    assert lp.close_pnl == 40.0
    assert lp.close_time is not None  # parsed from unix timestamp


def test_close_enriched_with_sl_hit_from_history() -> None:
    pos = _MockMTPosition(ticket=2002)
    out_deal = _MockDeal(entry=1, price=1.0960, profit=-40.0, reason=4)
    mt5 = _MockMT5(positions=[pos], deals=[out_deal])
    tracker = LivePositionTracker(mt5)
    tracker.sync_positions()

    mt5._positions = []
    tracker.sync_positions()

    lp = tracker.get_position(2002)
    assert lp.close_reason == "sl_hit"
    assert lp.close_price == 1.0960
    assert lp.close_pnl == -40.0


def test_close_fallback_when_no_history_available() -> None:
    """If history_deals_get is not on the MT5 client, close still records without enrichment."""
    class NoHistoryMT5:
        def positions_get(self):
            return []

    pos = _MockMTPosition(ticket=3001)
    mt5_with = _MockMT5(positions=[pos])
    tracker = LivePositionTracker(mt5_with)
    tracker.sync_positions()

    # Replace MT5 with one that has no history_deals_get
    tracker._mt5 = NoHistoryMT5()
    tracker.sync_positions()

    lp = tracker.get_position(3001)
    assert lp is not None
    assert lp.status == "CLOSED"
    assert lp.close_reason == "external_close"  # fallback
    assert lp.close_price is None
    assert lp.close_pnl is None


def test_close_fallback_when_history_raises() -> None:
    pos = _MockMTPosition(ticket=3002)
    mt5 = _MockMT5(positions=[pos], history_raises=True)
    tracker = LivePositionTracker(mt5)
    tracker.sync_positions()

    mt5._positions = []
    tracker.sync_positions()

    lp = tracker.get_position(3002)
    assert lp is not None
    assert lp.status == "CLOSED"
    assert lp.close_reason == "external_close"
    assert lp.close_price is None


def test_close_fallback_when_no_out_deal_found() -> None:
    """Only IN deals in history — no OUT deal. Enrichment silently skipped."""
    pos = _MockMTPosition(ticket=3003)
    in_deal = _MockDeal(entry=0, price=1.1000, profit=0.0, reason=0)  # DEAL_ENTRY_IN
    mt5 = _MockMT5(positions=[pos], deals=[in_deal])
    tracker = LivePositionTracker(mt5)
    tracker.sync_positions()

    mt5._positions = []
    tracker.sync_positions()

    lp = tracker.get_position(3003)
    assert lp.close_reason == "external_close"
    assert lp.close_price is None


# ---------------------------------------------------------------------------
# has_open_position
# ---------------------------------------------------------------------------


def test_has_open_position_true_when_open() -> None:
    pos = _MockMTPosition(ticket=1001, symbol="EURUSD")
    tracker = LivePositionTracker(_MockMT5(positions=[pos]))
    tracker.sync_positions()
    assert tracker.has_open_position("EURUSD") is True


def test_has_open_position_false_after_close() -> None:
    pos = _MockMTPosition(ticket=1001, symbol="EURUSD")
    mt5 = _MockMT5(positions=[pos])
    tracker = LivePositionTracker(mt5)
    tracker.sync_positions()

    mt5._positions = []
    tracker.sync_positions()

    assert tracker.has_open_position("EURUSD") is False


# ---------------------------------------------------------------------------
# record_sent_order
# ---------------------------------------------------------------------------


def test_record_sent_order_immediately_visible() -> None:
    tracker = LivePositionTracker(_MockMT5(positions=[]))
    tracker.record_sent_order(
        ticket=5001,
        trade_id="trade_1",
        decision_id="dec_1",
        symbol="GBPUSD",
        side="SELL",
        lot_size=0.02,
        open_price=1.2500,
        sl=1.2540,
        tp=1.2420,
    )
    assert tracker.has_open_position("GBPUSD") is True
    lp = tracker.get_position(5001)
    assert lp is not None
    assert lp.trade_id == "trade_1"
    assert lp.decision_id == "dec_1"


# ---------------------------------------------------------------------------
# Cross-run persistence — state_path
# ---------------------------------------------------------------------------


def test_save_state_creates_file(tmp_path: Path) -> None:
    """After sync_positions, OPEN positions should be written to the state file."""
    state_file = tmp_path / "position_state.json"
    pos = _MockMTPosition(ticket=6001, symbol="EURUSD")
    mt5 = _MockMT5(positions=[pos])

    tracker = LivePositionTracker(mt5, state_path=state_file)
    tracker.sync_positions()

    assert state_file.exists()
    data = json.loads(state_file.read_text())
    assert len(data) == 1
    assert data[0]["ticket"] == 6001
    assert data[0]["status"] == "OPEN"
    assert data[0]["symbol"] == "EURUSD"


def test_load_state_restores_open_positions(tmp_path: Path) -> None:
    """A new tracker instance should load previously saved OPEN positions."""
    state_file = tmp_path / "position_state.json"

    # Run 1: position is open, state saved
    pos = _MockMTPosition(ticket=6002, symbol="GBPUSD")
    mt5 = _MockMT5(positions=[pos])
    tracker1 = LivePositionTracker(mt5, state_path=state_file)
    tracker1.sync_positions()
    assert state_file.exists()

    # Run 2: new tracker instance, MT5 now shows no positions (closed between runs)
    mt5_empty = _MockMT5(positions=[])
    tracker2 = LivePositionTracker(mt5_empty, state_path=state_file)
    # _load_state is called in __init__ — position 6002 should be restored
    assert tracker2.get_position(6002) is not None
    assert tracker2.get_position(6002).status == "OPEN"  # type: ignore[union-attr]


def test_cross_run_close_detected(tmp_path: Path) -> None:
    """If a position closes between runs, sync_positions on the new tracker detects the close."""
    state_file = tmp_path / "position_state.json"

    # Run 1: record open position
    pos = _MockMTPosition(ticket=6003, symbol="EURUSD")
    mt5 = _MockMT5(positions=[pos])
    tracker1 = LivePositionTracker(mt5, state_path=state_file)
    tracker1.sync_positions()

    # Run 2: new tracker, MT5 no longer has the position (it closed between runs)
    out_deal = _MockDeal(entry=1, price=1.1080, profit=42.0, reason=5)
    mt5_empty = _MockMT5(positions=[], deals=[out_deal])
    tracker2 = LivePositionTracker(mt5_empty, state_path=state_file)
    tracker2.sync_positions()

    newly_closed = tracker2.get_newly_closed()
    assert len(newly_closed) == 1
    assert newly_closed[0].ticket == 6003
    assert newly_closed[0].status == "CLOSED"
    assert newly_closed[0].close_reason == "tp_hit"
    assert newly_closed[0].close_pnl == 42.0


def test_closed_positions_not_saved_to_state(tmp_path: Path) -> None:
    """After a position closes, the state file should not contain it (stays clean)."""
    state_file = tmp_path / "position_state.json"

    pos = _MockMTPosition(ticket=6004, symbol="EURUSD")
    mt5 = _MockMT5(positions=[pos])
    tracker = LivePositionTracker(mt5, state_path=state_file)
    tracker.sync_positions()

    # Position disappears from MT5 — detected as closed
    mt5._positions = []
    tracker.sync_positions()

    data = json.loads(state_file.read_text())
    # No OPEN positions remain — state file should be empty list
    open_entries = [e for e in data if e.get("status") == "OPEN"]
    assert open_entries == []


def test_no_state_path_does_not_create_file(tmp_path: Path) -> None:
    """Without state_path, no file is written anywhere."""
    pos = _MockMTPosition(ticket=6005)
    mt5 = _MockMT5(positions=[pos])
    tracker = LivePositionTracker(mt5)  # no state_path
    tracker.sync_positions()
    # No files should have been created in tmp_path (which we control)
    assert list(tmp_path.iterdir()) == []


def test_corrupt_state_file_starts_fresh(tmp_path: Path) -> None:
    """A corrupt JSON state file should not crash — tracker starts with empty positions."""
    state_file = tmp_path / "position_state.json"
    state_file.write_text("{ this is not valid json", encoding="utf-8")

    mt5 = _MockMT5(positions=[])
    tracker = LivePositionTracker(mt5, state_path=state_file)
    # Should not raise — positions dict is empty
    assert tracker.get_open_positions() == []


def test_state_file_with_wrong_root_type_starts_fresh(tmp_path: Path) -> None:
    """State file containing a dict instead of list is skipped gracefully."""
    state_file = tmp_path / "position_state.json"
    state_file.write_text(json.dumps({"ticket": 9999}), encoding="utf-8")

    mt5 = _MockMT5(positions=[])
    tracker = LivePositionTracker(mt5, state_path=state_file)
    assert tracker.get_open_positions() == []


def test_state_file_skips_closed_entries(tmp_path: Path) -> None:
    """Entries with status != OPEN in the state file are not loaded."""
    state_file = tmp_path / "position_state.json"
    state_file.write_text(
        json.dumps([
            {
                "ticket": 7001,
                "trade_id": "t1",
                "decision_id": "d1",
                "symbol": "EURUSD",
                "side": "BUY",
                "lot_size": 0.01,
                "open_price": 1.1000,
                "sl": 1.0960,
                "tp": 1.1080,
                "status": "CLOSED",  # should be skipped
            }
        ]),
        encoding="utf-8",
    )

    mt5 = _MockMT5(positions=[])
    tracker = LivePositionTracker(mt5, state_path=state_file)
    assert tracker.get_open_positions() == []
    assert tracker.get_position(7001) is None


def test_multiple_open_positions_persisted_and_restored(tmp_path: Path) -> None:
    """Multiple open positions survive a round-trip through the state file."""
    state_file = tmp_path / "position_state.json"

    positions = [
        _MockMTPosition(ticket=8001, symbol="EURUSD"),
        _MockMTPosition(ticket=8002, symbol="GBPUSD"),
        _MockMTPosition(ticket=8003, symbol="USDJPY"),
    ]
    mt5 = _MockMT5(positions=positions)
    tracker1 = LivePositionTracker(mt5, state_path=state_file)
    tracker1.sync_positions()

    # New tracker — all three should be restored
    mt5_new = _MockMT5(positions=positions)  # all still open
    tracker2 = LivePositionTracker(mt5_new, state_path=state_file)
    open_pos = tracker2.get_open_positions()
    assert len(open_pos) == 3
    tickets = {p.ticket for p in open_pos}
    assert tickets == {8001, 8002, 8003}


# ---------------------------------------------------------------------------
# trade_id / decision_id preservation across sync cycles (task #48)
# ---------------------------------------------------------------------------

def test_trade_id_preserved_across_sync_cycles() -> None:
    """trade_id set by record_sent_order must survive subsequent sync_positions calls.

    Regression test for the bug where sync_positions() rebuilt each LivePosition
    from MT5 data and only copied trail state, not trade_id / decision_id.
    The MT5 comment field is "devi:{run_id}:{trade_id}" truncated to 27 chars —
    the run_id alone is 36 chars so the trade_id portion is completely cut off.
    After one sync cycle the trade_id became an orphaned comment string that
    could not match the original write_trade record in Supabase.
    """
    pos = _MockMTPosition(ticket=9001, comment="devi:abc123def456ghi789j")

    mt5 = _MockMT5(positions=[pos])
    tracker = LivePositionTracker(mt5)

    # Simulate what live_session does after placing an order
    real_trade_id = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
    real_decision_id = "dec-aabbccdd"
    tracker.record_sent_order(
        ticket=9001,
        trade_id=real_trade_id,
        decision_id=real_decision_id,
        symbol="EURUSD",
        side="BUY",
        lot_size=0.01,
        open_price=1.1000,
        sl=1.0960,
        tp=1.1080,
    )

    # Cycle 1 — position still open
    tracker.sync_positions()
    lp = tracker.get_position(9001)
    assert lp is not None
    assert lp.trade_id == real_trade_id, (
        f"trade_id overwritten after first sync: got {lp.trade_id!r}"
    )
    assert lp.decision_id == real_decision_id

    # Cycle 2 — still open, still preserved
    tracker.sync_positions()
    lp = tracker.get_position(9001)
    assert lp.trade_id == real_trade_id, (
        f"trade_id overwritten after second sync: got {lp.trade_id!r}"
    )
    assert lp.decision_id == real_decision_id


def test_trade_id_on_close_record_matches_original() -> None:
    """When a position closes, the newly_closed entry must carry the original trade_id.

    This ensures write_trade_close() writes a Supabase row with a trade_id
    that matches the original write_trade row so the dashboard can reconcile.
    """
    pos = _MockMTPosition(ticket=9002, comment="devi:truncated_comment_xx")
    mt5 = _MockMT5(positions=[pos])
    tracker = LivePositionTracker(mt5)

    real_trade_id = "c9a646d3-9c61-4cb7-bfcd-ee2522c8f633"
    tracker.record_sent_order(
        ticket=9002,
        trade_id=real_trade_id,
        decision_id="dec-xyz",
        symbol="GBPUSD",
        side="SELL",
        lot_size=0.02,
        open_price=1.3400,
        sl=1.3450,
        tp=1.3300,
    )

    # First sync — open
    tracker.sync_positions()

    # Position disappears (TP hit, SL hit, or manual close)
    mt5._positions = []
    tracker.sync_positions()

    newly_closed = tracker.get_newly_closed()
    assert len(newly_closed) == 1
    closed = newly_closed[0]
    assert closed.ticket == 9002
    assert closed.trade_id == real_trade_id, (
        f"Close record has wrong trade_id: {closed.trade_id!r} — "
        f"Supabase close row will never match the open row"
    )
    assert closed.decision_id == "dec-xyz"


def test_trade_id_preserved_across_state_file_restart(tmp_path) -> None:
    """trade_id survives a bot restart via the state file.

    record_sent_order sets the correct trade_id. The state file is saved.
    A new tracker loads the state file and must see the same trade_id,
    not the truncated comment.
    """
    state_file = tmp_path / "position_state.json"
    pos = _MockMTPosition(ticket=9003, comment="devi:runid_truncated_xxx")
    mt5 = _MockMT5(positions=[pos])

    real_trade_id = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"

    tracker1 = LivePositionTracker(mt5, state_path=state_file)
    tracker1.record_sent_order(
        ticket=9003,
        trade_id=real_trade_id,
        decision_id="dec-restart",
        symbol="USDCHF",
        side="BUY",
        lot_size=0.01,
        open_price=0.8800,
        sl=0.8750,
        tp=0.8900,
    )
    tracker1.sync_positions()  # saves state

    # Simulate restart — new tracker loads state
    mt5_new = _MockMT5(positions=[pos])
    tracker2 = LivePositionTracker(mt5_new, state_path=state_file)
    open_pos = tracker2.get_open_positions()

    assert len(open_pos) == 1
    assert open_pos[0].trade_id == real_trade_id, (
        f"trade_id lost across restart: got {open_pos[0].trade_id!r}"
    )
    assert open_pos[0].decision_id == "dec-restart"
