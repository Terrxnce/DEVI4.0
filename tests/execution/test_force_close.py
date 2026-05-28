"""Tests for emergency force close module.

All tests use a mocked MT5 client. No real broker calls are made.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.execution.force_close import ForceCloseResult, close_devi_positions, is_devi_position


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


class _MockPosition:
    """Fake MT5 TradePosition."""

    def __init__(
        self,
        *,
        ticket: int,
        symbol: str,
        pos_type: int = 0,  # 0=BUY, 1=SELL
        volume: float = 0.01,
        price_open: float = 1.1000,
        comment: str = "devi:run_001:trade_001",
    ) -> None:
        self.ticket = ticket
        self.symbol = symbol
        self.type = pos_type
        self.volume = volume
        self.price_open = price_open
        self.comment = comment


class _MockTick:
    def __init__(self, bid: float = 1.0998, ask: float = 1.1002) -> None:
        self.bid = bid
        self.ask = ask


class _MockOrderResult:
    def __init__(self, *, retcode: int = 10009, price: float = 1.0998, comment: str = "done") -> None:
        self.retcode = retcode
        self.price = price
        self.comment = comment


class _MockMT5:
    """Fake MT5 client for force close testing."""

    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_FILLING_IOC = 1

    def __init__(
        self,
        *,
        positions: list[_MockPosition] | None = None,
        order_retcode: int = 10009,
        order_send_raises: Exception | None = None,
        order_send_returns_none: bool = False,
        tick_bid: float = 1.0998,
        tick_ask: float = 1.1002,
    ) -> None:
        self._positions = positions or []
        self._order_retcode = order_retcode
        self._order_send_raises = order_send_raises
        self._order_send_returns_none = order_send_returns_none
        self._tick = _MockTick(bid=tick_bid, ask=tick_ask)
        self.close_calls: list[dict] = []

    def positions_get(self) -> list[_MockPosition]:
        return self._positions

    def symbol_info_tick(self, symbol: str) -> _MockTick:
        return self._tick

    def order_send(self, request: dict) -> Any:
        self.close_calls.append(request)
        if self._order_send_raises is not None:
            raise self._order_send_raises
        if self._order_send_returns_none:
            return None
        return _MockOrderResult(retcode=self._order_retcode, price=self._tick.bid)


# ---------------------------------------------------------------------------
# is_devi_position
# ---------------------------------------------------------------------------


def test_devi_comment_prefix_recognised() -> None:
    pos = _MockPosition(ticket=1, symbol="EURUSD", comment="devi:run_01:trade_01")
    assert is_devi_position(pos) is True


def test_non_devi_comment_rejected() -> None:
    pos = _MockPosition(ticket=1, symbol="EURUSD", comment="manual_trade")
    assert is_devi_position(pos) is False


def test_empty_comment_rejected() -> None:
    pos = _MockPosition(ticket=1, symbol="EURUSD", comment="")
    assert is_devi_position(pos) is False


def test_none_comment_rejected() -> None:
    pos = _MockPosition(ticket=1, symbol="EURUSD", comment="")
    pos.comment = None  # type: ignore[assignment]
    assert is_devi_position(pos) is False


# ---------------------------------------------------------------------------
# No positions
# ---------------------------------------------------------------------------


def test_no_positions_returns_empty() -> None:
    mt5 = _MockMT5(positions=[])
    results = close_devi_positions(mt5)
    assert results == []


def test_only_non_devi_positions_returns_empty() -> None:
    pos = _MockPosition(ticket=1, symbol="EURUSD", comment="manual_trade")
    mt5 = _MockMT5(positions=[pos])
    results = close_devi_positions(mt5)
    assert results == []
    assert mt5.close_calls == []


def test_none_mt5_returns_empty() -> None:
    results = close_devi_positions(None)
    assert results == []


def test_mt5_without_positions_get_returns_empty() -> None:
    class NoPositionsGet:
        pass
    results = close_devi_positions(NoPositionsGet())
    assert results == []


# ---------------------------------------------------------------------------
# Successful close — BUY position
# ---------------------------------------------------------------------------


def test_close_buy_position_sends_sell_request() -> None:
    pos = _MockPosition(ticket=11111, symbol="EURUSD", pos_type=0, volume=0.01, price_open=1.1000)
    mt5 = _MockMT5(positions=[pos], order_retcode=10009)

    results = close_devi_positions(mt5)

    assert len(results) == 1
    r = results[0]
    assert r.status == "closed"
    assert r.ticket == 11111
    assert r.symbol == "EURUSD"
    assert r.side == "BUY"
    assert r.retcode == 10009
    assert r.reason == "market_close_ok"

    assert len(mt5.close_calls) == 1
    req = mt5.close_calls[0]
    assert req["position"] == 11111
    assert req["type"] == _MockMT5.ORDER_TYPE_SELL  # opposite of BUY
    assert req["volume"] == 0.01
    assert req["comment"] == "devi_force_close"


def test_close_sell_position_sends_buy_request() -> None:
    pos = _MockPosition(ticket=22222, symbol="GBPUSD", pos_type=1, volume=0.02, price_open=1.2500)
    mt5 = _MockMT5(positions=[pos], order_retcode=10009, tick_ask=1.2502)

    results = close_devi_positions(mt5)

    assert len(results) == 1
    r = results[0]
    assert r.status == "closed"
    assert r.side == "SELL"

    req = mt5.close_calls[0]
    assert req["type"] == _MockMT5.ORDER_TYPE_BUY  # opposite of SELL


def test_close_multiple_positions() -> None:
    positions = [
        _MockPosition(ticket=1001, symbol="EURUSD", pos_type=0),
        _MockPosition(ticket=1002, symbol="GBPUSD", pos_type=1),
        _MockPosition(ticket=1003, symbol="USDJPY", pos_type=0),
    ]
    mt5 = _MockMT5(positions=positions, order_retcode=10009)

    results = close_devi_positions(mt5)

    assert len(results) == 3
    assert all(r.status == "closed" for r in results)
    assert len(mt5.close_calls) == 3


def test_non_devi_positions_skipped_among_mixed_list() -> None:
    positions = [
        _MockPosition(ticket=1001, symbol="EURUSD", comment="devi:run_1:trade_1"),
        _MockPosition(ticket=1002, symbol="GBPUSD", comment="manual"),
        _MockPosition(ticket=1003, symbol="USDJPY", comment="devi:run_1:trade_2"),
    ]
    mt5 = _MockMT5(positions=positions, order_retcode=10009)

    results = close_devi_positions(mt5)

    assert len(results) == 2
    tickets = {r.ticket for r in results}
    assert tickets == {1001, 1003}
    assert len(mt5.close_calls) == 2


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


def test_failed_retcode_marks_as_failed() -> None:
    pos = _MockPosition(ticket=9999, symbol="EURUSD")
    mt5 = _MockMT5(positions=[pos], order_retcode=10027)  # NO_MONEY

    results = close_devi_positions(mt5)

    assert len(results) == 1
    r = results[0]
    assert r.status == "failed"
    assert r.retcode == 10027
    assert "10027" in r.reason


def test_order_send_exception_marks_as_failed() -> None:
    pos = _MockPosition(ticket=9999, symbol="EURUSD")
    mt5 = _MockMT5(positions=[pos], order_send_raises=RuntimeError("connection lost"))

    results = close_devi_positions(mt5)

    assert len(results) == 1
    r = results[0]
    assert r.status == "failed"
    assert "order_send_exception" in r.reason
    assert "connection lost" in r.reason


def test_order_send_none_marks_as_failed() -> None:
    pos = _MockPosition(ticket=9999, symbol="EURUSD")
    mt5 = _MockMT5(positions=[pos], order_send_returns_none=True)

    results = close_devi_positions(mt5)

    assert len(results) == 1
    r = results[0]
    assert r.status == "failed"
    assert r.reason == "order_send_returned_none"


def test_positions_get_exception_returns_empty() -> None:
    class FailingMT5:
        TRADE_ACTION_DEAL = 1
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1
        ORDER_FILLING_IOC = 1

        def positions_get(self):
            raise RuntimeError("MT5 disconnected")

    results = close_devi_positions(FailingMT5())
    assert results == []


# ---------------------------------------------------------------------------
# Partial failure — some close, some fail
# ---------------------------------------------------------------------------


def test_partial_failure_records_both() -> None:
    """First position closes OK, second fails. Both should be in results."""

    call_count = 0

    class PartialMT5(_MockMT5):
        def order_send(self, request: dict) -> Any:
            nonlocal call_count
            call_count += 1
            self.close_calls.append(request)
            if call_count == 1:
                return _MockOrderResult(retcode=10009)
            return _MockOrderResult(retcode=10027)

    positions = [
        _MockPosition(ticket=1001, symbol="EURUSD"),
        _MockPosition(ticket=1002, symbol="GBPUSD"),
    ]
    mt5 = PartialMT5(positions=positions)

    results = close_devi_positions(mt5)

    assert len(results) == 2
    assert results[0].status == "closed"
    assert results[1].status == "failed"


# ---------------------------------------------------------------------------
# Log writing
# ---------------------------------------------------------------------------


def test_log_written_when_path_provided(tmp_path) -> None:
    pos = _MockPosition(ticket=1001, symbol="EURUSD")
    mt5 = _MockMT5(positions=[pos], order_retcode=10009)
    log_file = str(tmp_path / "force_close.jsonl")

    results = close_devi_positions(mt5, log_path=log_file)

    assert len(results) == 1
    import json
    lines = Path(log_file).read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["ticket"] == 1001
    assert record["status"] == "closed"


def test_no_log_written_when_no_results(tmp_path) -> None:
    mt5 = _MockMT5(positions=[])
    log_file = str(tmp_path / "force_close.jsonl")

    close_devi_positions(mt5, log_path=log_file)

    assert not Path(log_file).exists()


# ---------------------------------------------------------------------------
# ForceCloseResult fields
# ---------------------------------------------------------------------------


def test_result_has_all_required_fields() -> None:
    pos = _MockPosition(ticket=5555, symbol="EURUSD", pos_type=0, volume=0.01, price_open=1.1000)
    mt5 = _MockMT5(positions=[pos], order_retcode=10009, tick_bid=1.0998)

    results = close_devi_positions(mt5)
    r = results[0]

    assert r.ticket == 5555
    assert r.symbol == "EURUSD"
    assert r.side == "BUY"
    assert r.volume == 0.01
    assert r.open_price == 1.1000
    assert r.close_price is not None
    assert r.retcode == 10009
    assert r.retcode_comment is not None
    assert r.status == "closed"
    assert r.reason == "market_close_ok"
    assert r.timestamp is not None
