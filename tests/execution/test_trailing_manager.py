"""Tests for TrailingManager — partial close, breakeven, and trail logic.

All MT5 interaction is mocked. No real broker calls.
"""
from __future__ import annotations

from src.execution.live_position_tracker import LivePosition
from src.execution.trailing_manager import TrailingManager, _RETCODE_DONE


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


class _OrderResult:
    def __init__(self, retcode: int = _RETCODE_DONE, order: int = 9001, comment: str = "ok") -> None:
        self.retcode = retcode
        self.order = order
        self.comment = comment
        self.request_id = 1


class _MockMT5:
    """Captures order_send calls and returns configurable results."""

    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 6
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_FILLING_IOC = 1

    def __init__(self, retcodes: list[int] | None = None) -> None:
        # retcodes: sequence of retcodes to return, one per order_send call
        self._retcodes = retcodes or [_RETCODE_DONE]
        self._call_index = 0
        self.requests: list[dict] = []

    def order_send(self, request: dict) -> _OrderResult:
        retcode = self._retcodes[min(self._call_index, len(self._retcodes) - 1)]
        self._call_index += 1
        self.requests.append(dict(request))
        return _OrderResult(retcode=retcode)


def _make_pos(
    *,
    ticket: int = 1001,
    symbol: str = "EURUSD",
    side: str = "BUY",
    lot_size: float = 0.02,
    open_price: float = 1.10000,
    current_price: float = 1.10000,
    sl: float = 1.09800,   # 20 pip SL
    tp: float = 1.10400,   # 40 pip TP
) -> LivePosition:
    return LivePosition(
        ticket=ticket,
        trade_id=f"trade_{ticket}",
        decision_id=f"dec_{ticket}",
        symbol=symbol,
        side=side,
        lot_size=lot_size,
        open_price=open_price,
        current_price=current_price,
        sl=sl,
        tp=tp,
        profit=0.0,
        swap=0.0,
    )


def _make_manager(mt5: _MockMT5 | None = None, **kwargs) -> TrailingManager:
    if mt5 is None:
        mt5 = _MockMT5()
    defaults = dict(
        trail_distance_r=0.5,
        partial_close_ratio=0.5,
        min_lot=0.01,
        lot_step=0.01,
        breakeven_buffer=0.0001,
        magic_number=234000,
        deviation=10,
    )
    defaults.update(kwargs)
    return TrailingManager(mt5, **defaults)


# ---------------------------------------------------------------------------
# _compute_partial_volume
# ---------------------------------------------------------------------------


def test_partial_volume_half_of_lot() -> None:
    mgr = _make_manager()
    assert mgr._compute_partial_volume(0.02) == 0.01


def test_partial_volume_rounds_down() -> None:
    mgr = _make_manager()
    # 0.03 * 0.5 = 0.015 → rounds DOWN to 0.01
    assert mgr._compute_partial_volume(0.03) == 0.01


def test_partial_volume_below_min_lot_returns_none() -> None:
    mgr = _make_manager(min_lot=0.01)
    # 0.01 * 0.5 = 0.005 < 0.01 → None
    assert mgr._compute_partial_volume(0.01) is None


def test_partial_volume_exactly_min_lot() -> None:
    mgr = _make_manager(min_lot=0.01, lot_step=0.01)
    assert mgr._compute_partial_volume(0.02) == 0.01


def test_partial_volume_zero_ratio_always_returns_none() -> None:
    """partial_close_ratio=0.0 disables partial closes regardless of lot size.

    This is the mechanism behind partials_enabled=false in config.
    live_session passes ratio=0.0 when the flag is off, so _compute_partial_volume
    always returns None → partial close is skipped, breakeven/trail still run.
    """
    mgr = _make_manager(partial_close_ratio=0.0)
    assert mgr._compute_partial_volume(0.10) is None
    assert mgr._compute_partial_volume(1.00) is None
    assert mgr._compute_partial_volume(100.0) is None


# ---------------------------------------------------------------------------
# _one_r_hit
# ---------------------------------------------------------------------------


def test_one_r_hit_buy_not_yet() -> None:
    mgr = _make_manager()
    pos = _make_pos(side="BUY", open_price=1.10000, sl=1.09800, current_price=1.10100)
    # 1R = 200 pips, only 100 pips moved
    assert mgr._one_r_hit(pos) is False


def test_one_r_hit_buy_exactly() -> None:
    mgr = _make_manager()
    pos = _make_pos(side="BUY", open_price=1.10000, sl=1.09800, current_price=1.10200)
    assert mgr._one_r_hit(pos) is True


def test_one_r_hit_buy_beyond() -> None:
    mgr = _make_manager()
    pos = _make_pos(side="BUY", open_price=1.10000, sl=1.09800, current_price=1.10300)
    assert mgr._one_r_hit(pos) is True


def test_one_r_hit_sell_not_yet() -> None:
    mgr = _make_manager()
    pos = _make_pos(side="SELL", open_price=1.10000, sl=1.10200, current_price=1.09900)
    assert mgr._one_r_hit(pos) is False


def test_one_r_hit_sell_exactly() -> None:
    mgr = _make_manager()
    pos = _make_pos(side="SELL", open_price=1.10000, sl=1.10200, current_price=1.09800)
    assert mgr._one_r_hit(pos) is True


def test_one_r_hit_zero_sl_distance_returns_false() -> None:
    mgr = _make_manager()
    pos = _make_pos(side="BUY", open_price=1.10000, sl=1.10000, current_price=1.10500)
    assert mgr._one_r_hit(pos) is False


# ---------------------------------------------------------------------------
# Full partial-close + breakeven flow (0.02 lot, partial close succeeds)
# ---------------------------------------------------------------------------


def test_1r_triggers_partial_close_and_breakeven() -> None:
    mt5 = _MockMT5(retcodes=[_RETCODE_DONE, _RETCODE_DONE])
    mgr = _make_manager(mt5)

    # BUY: open=1.10, sl=1.09800 (200 pips), 1R = 1.10200
    pos = _make_pos(
        side="BUY", lot_size=0.02,
        open_price=1.10000, sl=1.09800, current_price=1.10200,
    )

    events = mgr.process_positions([pos])

    event_types = [e.event_type for e in events]
    assert "partial_close_sent" in event_types
    assert "breakeven_set" in event_types
    assert len(mt5.requests) == 2


def test_partial_close_request_is_opposite_side_buy() -> None:
    mt5 = _MockMT5(retcodes=[_RETCODE_DONE, _RETCODE_DONE])
    mgr = _make_manager(mt5)
    pos = _make_pos(side="BUY", lot_size=0.02, open_price=1.10000, sl=1.09800, current_price=1.10200)
    mgr.process_positions([pos])

    pc_request = mt5.requests[0]
    # Partial close of a BUY is a SELL
    assert pc_request["type"] == _MockMT5.ORDER_TYPE_SELL
    assert pc_request["volume"] == 0.01
    assert pc_request["position"] == 1001


def test_partial_close_request_is_opposite_side_sell() -> None:
    mt5 = _MockMT5(retcodes=[_RETCODE_DONE, _RETCODE_DONE])
    mgr = _make_manager(mt5)
    pos = _make_pos(side="SELL", lot_size=0.02, open_price=1.10000, sl=1.10200, current_price=1.09800)
    mgr.process_positions([pos])

    pc_request = mt5.requests[0]
    assert pc_request["type"] == _MockMT5.ORDER_TYPE_BUY
    assert pc_request["position"] == 1001


def test_breakeven_sl_set_below_open_for_buy() -> None:
    mt5 = _MockMT5(retcodes=[_RETCODE_DONE, _RETCODE_DONE])
    mgr = _make_manager(mt5, breakeven_buffer=0.0001)
    pos = _make_pos(side="BUY", lot_size=0.02, open_price=1.10000, sl=1.09800, current_price=1.10200)
    mgr.process_positions([pos])

    sltp_request = mt5.requests[1]
    assert sltp_request["action"] == _MockMT5.TRADE_ACTION_SLTP
    # BUY breakeven: open_price - buffer = 1.09999
    assert abs(sltp_request["sl"] - (1.10000 - 0.0001)) < 1e-6


def test_breakeven_sl_set_above_open_for_sell() -> None:
    mt5 = _MockMT5(retcodes=[_RETCODE_DONE, _RETCODE_DONE])
    mgr = _make_manager(mt5, breakeven_buffer=0.0001)
    pos = _make_pos(side="SELL", lot_size=0.02, open_price=1.10000, sl=1.10200, current_price=1.09800)
    mgr.process_positions([pos])

    sltp_request = mt5.requests[1]
    assert abs(sltp_request["sl"] - (1.10000 + 0.0001)) < 1e-6


def test_trail_state_set_after_breakeven() -> None:
    mt5 = _MockMT5(retcodes=[_RETCODE_DONE, _RETCODE_DONE])
    mgr = _make_manager(mt5, trail_distance_r=0.5)
    # sl_distance = 0.002 (200 pips); trail = 0.001 (100 pips)
    pos = _make_pos(side="BUY", lot_size=0.02, open_price=1.10000, sl=1.09800, current_price=1.10200)
    mgr.process_positions([pos])

    assert pos.breakeven_set is True
    assert pos.trail_active is True
    assert abs(pos.trail_distance - 0.001) < 1e-8
    assert pos.best_price == 1.10200
    assert pos.partial_closed is True
    assert pos.partial_close_volume == 0.01


# ---------------------------------------------------------------------------
# Partial close skipped (lot too small)
# ---------------------------------------------------------------------------


def test_partial_close_skipped_when_lot_too_small() -> None:
    mt5 = _MockMT5(retcodes=[_RETCODE_DONE])  # only one call expected (SLTP)
    mgr = _make_manager(mt5, min_lot=0.01)

    pos = _make_pos(side="BUY", lot_size=0.01, open_price=1.10000, sl=1.09800, current_price=1.10200)
    events = mgr.process_positions([pos])

    event_types = [e.event_type for e in events]
    assert "partial_close_skipped" in event_types
    assert "breakeven_set" in event_types
    assert "partial_close_sent" not in event_types
    # Only 1 MT5 call (the SLTP) — no partial close call
    assert len(mt5.requests) == 1


def test_partial_close_skipped_breakeven_still_sets_trail() -> None:
    mt5 = _MockMT5(retcodes=[_RETCODE_DONE])
    mgr = _make_manager(mt5, min_lot=0.01, trail_distance_r=0.5)
    pos = _make_pos(side="BUY", lot_size=0.01, open_price=1.10000, sl=1.09800, current_price=1.10200)
    mgr.process_positions([pos])

    assert pos.breakeven_set is True
    assert pos.trail_active is True
    assert pos.partial_closed is False  # no partial close happened


# ---------------------------------------------------------------------------
# Partial close failure — SL must NOT move
# ---------------------------------------------------------------------------


def test_partial_close_failure_blocks_breakeven() -> None:
    mt5 = _MockMT5(retcodes=[10004])  # TRADE_RETCODE_REQUOTE — failure
    mgr = _make_manager(mt5)
    pos = _make_pos(side="BUY", lot_size=0.02, open_price=1.10000, sl=1.09800, current_price=1.10200)
    events = mgr.process_positions([pos])

    event_types = [e.event_type for e in events]
    assert "partial_close_failed" in event_types
    assert "breakeven_set" not in event_types
    assert "breakeven_failed" not in event_types
    # Only 1 MT5 call — the failed partial close. SLTP never sent.
    assert len(mt5.requests) == 1
    # Position state unchanged
    assert pos.breakeven_set is False
    assert pos.trail_active is False


# ---------------------------------------------------------------------------
# Trail movement
# ---------------------------------------------------------------------------


def test_trail_moves_when_price_improves_buy() -> None:
    mt5 = _MockMT5(retcodes=[_RETCODE_DONE])
    mgr = _make_manager(mt5)

    pos = _make_pos(side="BUY", lot_size=0.02, open_price=1.10000, sl=1.09900, current_price=1.10300)
    # Manually set trail state (as if 1R was already hit)
    pos.breakeven_set = True
    pos.trail_active = True
    pos.trail_distance = 0.001  # 10 pips
    pos.best_price = 1.10250
    pos.sl = 1.09999  # current breakeven SL

    events = mgr.process_positions([pos])

    event_types = [e.event_type for e in events]
    assert "trail_moved" in event_types
    # new SL = best_price - trail_distance = 1.10300 - 0.001 = 1.10200
    assert abs(pos.sl - 1.10200) < 1e-5
    sltp_request = mt5.requests[0]
    assert sltp_request["action"] == _MockMT5.TRADE_ACTION_SLTP
    assert abs(sltp_request["sl"] - 1.10200) < 1e-5


def test_trail_moves_when_price_improves_sell() -> None:
    mt5 = _MockMT5(retcodes=[_RETCODE_DONE])
    mgr = _make_manager(mt5)

    pos = _make_pos(side="SELL", lot_size=0.02, open_price=1.10000, sl=1.10001, current_price=1.09700)
    pos.breakeven_set = True
    pos.trail_active = True
    pos.trail_distance = 0.001
    pos.best_price = 1.09800
    pos.sl = 1.10001

    events = mgr.process_positions([pos])

    assert any(e.event_type == "trail_moved" for e in events)
    # new SL = best_price + trail = 1.09700 + 0.001 = 1.09800
    assert abs(pos.sl - 1.09800) < 1e-5


def test_trail_does_not_move_when_price_unchanged() -> None:
    mt5 = _MockMT5()
    mgr = _make_manager(mt5)

    pos = _make_pos(side="BUY", lot_size=0.02, open_price=1.10000, sl=1.09999)
    pos.breakeven_set = True
    pos.trail_active = True
    pos.trail_distance = 0.001
    pos.best_price = 1.10200
    pos.current_price = 1.10200  # same as best_price — no improvement

    events = mgr.process_positions([pos])
    assert events == []
    assert mt5.requests == []


def test_trail_does_not_move_when_new_sl_not_better_buy() -> None:
    """If price improves but new trail SL is still below current SL, skip."""
    mt5 = _MockMT5()
    mgr = _make_manager(mt5)

    pos = _make_pos(side="BUY", lot_size=0.02, open_price=1.10000, sl=1.10150)
    pos.breakeven_set = True
    pos.trail_active = True
    pos.trail_distance = 0.001
    pos.best_price = 1.10100
    pos.current_price = 1.10110  # tiny improvement

    events = mgr.process_positions([pos])
    # new_sl = 1.10110 - 0.001 = 1.10010 < current sl 1.10150 → no move
    assert events == []


# ---------------------------------------------------------------------------
# No action before 1R
# ---------------------------------------------------------------------------


def test_no_action_before_1r() -> None:
    mt5 = _MockMT5()
    mgr = _make_manager(mt5)
    pos = _make_pos(side="BUY", lot_size=0.02, open_price=1.10000, sl=1.09800, current_price=1.10050)
    events = mgr.process_positions([pos])
    assert events == []
    assert mt5.requests == []


def test_no_action_on_closed_position() -> None:
    mt5 = _MockMT5()
    mgr = _make_manager(mt5)
    pos = _make_pos(side="BUY", lot_size=0.02, open_price=1.10000, sl=1.09800, current_price=1.10300)
    pos.status = "CLOSED"
    events = mgr.process_positions([pos])
    assert events == []
    assert mt5.requests == []


# ---------------------------------------------------------------------------
# Breakeven not repeated after already set
# ---------------------------------------------------------------------------


def test_no_duplicate_breakeven_on_second_cycle() -> None:
    """Once breakeven_set=True, process_positions should only run trail logic."""
    mt5 = _MockMT5()
    mgr = _make_manager(mt5)

    pos = _make_pos(side="BUY", lot_size=0.02, open_price=1.10000, sl=1.09999)
    pos.breakeven_set = True
    pos.trail_active = False  # trail not yet active — shouldn't trigger anything
    pos.trail_distance = 0.0
    pos.best_price = None
    pos.current_price = 1.10300  # price still at 1R — no trail improvement

    events = mgr.process_positions([pos])
    assert events == []
    assert mt5.requests == []


# ---------------------------------------------------------------------------
# MT5 unavailable
# ---------------------------------------------------------------------------


def test_no_mt5_returns_failed_events() -> None:
    mgr = TrailingManager(None, min_lot=0.01, lot_step=0.01)
    pos = _make_pos(side="BUY", lot_size=0.02, open_price=1.10000, sl=1.09800, current_price=1.10200)
    events = mgr.process_positions([pos])
    # partial close attempt + breakeven attempt, both failed due to no mt5
    event_types = [e.event_type for e in events]
    assert "partial_close_failed" in event_types


def test_order_send_exception_handled() -> None:
    class ExplodingMT5:
        TRADE_ACTION_DEAL = 1
        TRADE_ACTION_SLTP = 6
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1
        ORDER_FILLING_IOC = 1

        def order_send(self, _request):
            raise RuntimeError("broker connection lost")

    mgr = _make_manager(ExplodingMT5())
    pos = _make_pos(side="BUY", lot_size=0.02, open_price=1.10000, sl=1.09800, current_price=1.10200)
    # Should not raise — exception is caught and returned as failed event
    events = mgr.process_positions([pos])
    assert any(e.event_type == "partial_close_failed" for e in events)
