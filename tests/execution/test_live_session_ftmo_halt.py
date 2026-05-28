"""Regression test for LiveSessionResult field mismatch on FTMO floor breach.

Bug: when FTMO daily or total floor was breached, live_session.run() returned
LiveSessionResult(account_snapshot=account, ...) which is not a field on the
dataclass. This caused a TypeError at exactly the moment the drawdown
protection was supposed to fire.

Fix: both early-return branches now pass the correct fields:
  account_balance, account_equity, decision_count, trade_count,
  open_position_count, live_positions.

These tests prove the fix by verifying LiveSessionResult construction with
the field set used in each halt branch and by running the FTMO halt path
through a fully mocked LiveSession.run() call.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.execution.live_session import LiveSession, LiveSessionResult
from src.execution.live_position_tracker import LivePosition


# ---------------------------------------------------------------------------
# Direct dataclass construction — proves fields are correct
# ---------------------------------------------------------------------------

def _open_position(ticket: int = 1001, symbol: str = "EURUSD") -> LivePosition:
    return LivePosition(
        ticket=ticket,
        symbol=symbol,
        side="BUY",
        lot_size=0.01,
        open_price=1.1000,
        current_price=1.1010,
        sl=1.0960,
        tp=1.1080,
        profit=10.0,
        swap=0.0,
        open_time=datetime(2026, 5, 1, 8, 0, tzinfo=UTC).isoformat(),
        trade_id="trade_001",
        decision_id="dec_001",
        status="OPEN",
    )


def test_live_session_result_ftmo_daily_halt_fields():
    """LiveSessionResult with FTMO daily halt field set must not raise TypeError."""
    open_positions = [_open_position()]
    result = LiveSessionResult(
        run_id="run_001",
        symbol_results={},
        account_balance=10000.0,
        account_equity=9850.0,
        decision_count=0,
        trade_count=0,
        open_position_count=len([p for p in open_positions if p.status == "OPEN"]),
        live_positions=open_positions,
        execution_summary={
            "halted": "ftmo_daily_floor_breached",
            "reason": "daily_pnl_pct=-5.5%",
        },
    )
    assert result.run_id == "run_001"
    assert result.symbol_results == {}
    assert result.account_balance == 10000.0
    assert result.account_equity == 9850.0
    assert result.decision_count == 0
    assert result.trade_count == 0
    assert result.open_position_count == 1
    assert len(result.live_positions) == 1
    assert result.execution_summary["halted"] == "ftmo_daily_floor_breached"


def test_live_session_result_ftmo_total_halt_fields():
    """LiveSessionResult with FTMO total halt field set must not raise TypeError."""
    result = LiveSessionResult(
        run_id="run_002",
        symbol_results={},
        account_balance=10000.0,
        account_equity=8900.0,
        decision_count=0,
        trade_count=0,
        open_position_count=0,
        live_positions=[],
        execution_summary={
            "halted": "ftmo_total_floor_breached",
            "reason": "total_pnl_pct=-10.5%",
        },
    )
    assert result.execution_summary["halted"] == "ftmo_total_floor_breached"
    assert result.live_positions == []
    assert result.open_position_count == 0


def test_live_session_result_old_field_name_raises():
    """Confirm that using the old wrong field name 'account_snapshot' raises TypeError."""
    with pytest.raises(TypeError):
        LiveSessionResult(
            run_id="run_bad",
            symbol_results={},
            account_snapshot={"balance": 10000.0},  # wrong field — should error
            execution_summary={},
        )


# ---------------------------------------------------------------------------
# Mocked LiveSession.run() — FTMO daily breach path
# ---------------------------------------------------------------------------

def _make_mock_mt5():
    mt5 = MagicMock()
    mt5.initialize.return_value = True
    mt5.account_info.return_value = MagicMock(
        balance=10000.0, equity=9400.0, margin=0.0, margin_free=10000.0, currency="USD"
    )
    mt5.symbol_info_tick.return_value = MagicMock(bid=1.10000, ask=1.10015)
    mt5.symbol_info.return_value = MagicMock(
        digits=5, point=0.00001, trade_tick_size=0.00001,
        trade_contract_size=100000.0, volume_step=0.01, volume_min=0.01, volume_max=100.0,
        trade_tick_value=1.0,
    )
    mt5.copy_rates_from_pos.return_value = []
    mt5.positions_get.return_value = []
    mt5.history_deals_get.return_value = []
    return mt5


def _live_config() -> dict:
    cfg = json.loads(Path("src/config/live_one_order_test.json").read_text(encoding="utf-8"))
    # Force FTMO breach: initial_balance=10000, equity=9400 → daily loss > 5%
    cfg["ftmo"] = {
        "initial_balance": 10000.0,
        "max_daily_loss_pct": 0.05,
        "max_total_loss_pct": 0.10,
        "daily_buffer_pct": 0.0,
        "total_buffer_pct": 0.0,
    }
    return cfg


def test_ftmo_daily_halt_returns_valid_live_session_result(tmp_path):
    """LiveSession.run() must return a valid LiveSessionResult on FTMO daily breach.

    Before the fix this raised:
        TypeError: __init__() got an unexpected keyword argument 'account_snapshot'
    """
    from src.data.mt5_source import MT5DataSource
    from src.core.arming import ArmingService, LiveArmingToken
    from src.core.kill_switch import KillSwitch

    mock_mt5 = _make_mock_mt5()
    data_source = MT5DataSource(mt5_client=mock_mt5)

    cfg = _live_config()

    from src.core.enums import Namespace
    with patch("src.execution.live_session.MT5DataSource", return_value=data_source):
        session = LiveSession(
            config=cfg,
            logs_root=str(tmp_path),
            namespace=Namespace.PROD,
            symbols=["EURUSD"],
        )
        session.data = data_source

    token = LiveArmingToken(
        token_id="tok_001",
        run_id="run_halt",
        armed_at=datetime.now(tz=UTC),
        expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        armed_by="test",
        reason="test_halt",
        symbols=["EURUSD"],
        max_orders=1,
    )

    # Patch FTMORiskMonitor to force a daily breach
    from src.ops.ftmo_risk_monitor import FTMORiskResult
    mock_ftmo_result = FTMORiskResult(
        daily_ok=False,
        total_ok=True,
        daily_pnl_pct=-6.0,
        total_pnl_pct=-2.0,
        daily_floor=9500.0,
        total_floor=9000.0,
        day_start_balance=10000.0,
        initial_balance=10000.0,
        reason="daily_floor_breached",
    )

    with patch("src.execution.live_session.FTMORiskMonitor") as MockFTMO, \
         patch("src.execution.live_session.TrailingManager") as MockTrailing, \
         patch("src.execution.live_session.EconomicCalendar") as MockCal:

        MockFTMO.return_value.start_of_day_snapshot.return_value = None
        MockFTMO.return_value.evaluate.return_value = mock_ftmo_result
        MockTrailing.return_value.process_positions.return_value = []
        MockCal.return_value.refresh_if_stale.return_value = None
        MockCal.return_value.is_news_blocked.return_value = (False, "")

        result = session.run(run_id="run_halt", token=token)

    # Must return a valid result, not raise TypeError
    assert isinstance(result, LiveSessionResult)
    assert result.run_id == "run_halt"
    assert result.symbol_results == {}
    assert result.execution_summary.get("halted") == "ftmo_daily_floor_breached"
    assert isinstance(result.account_balance, float)
    assert isinstance(result.account_equity, float)
    assert isinstance(result.live_positions, list)
    assert isinstance(result.open_position_count, int)
