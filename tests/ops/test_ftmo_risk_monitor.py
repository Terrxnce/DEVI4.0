"""Tests for FTMORiskMonitor — FTMO daily and total loss limit enforcement."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from src.ops.ftmo_risk_monitor import FTMORiskMonitor, _prague_today


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_monitor(
    tmp_path: Path,
    *,
    initial_balance: float = 10000.0,
    max_daily_loss_pct: float = 0.05,
    max_total_loss_pct: float = 0.10,
    daily_buffer_pct: float = 0.0,   # zero buffer for clean maths in tests
    total_buffer_pct: float = 0.0,
) -> FTMORiskMonitor:
    return FTMORiskMonitor(
        initial_balance=initial_balance,
        max_daily_loss_pct=max_daily_loss_pct,
        max_total_loss_pct=max_total_loss_pct,
        daily_buffer_pct=daily_buffer_pct,
        total_buffer_pct=total_buffer_pct,
        state_path=tmp_path / "ftmo_state.json",
    )


# ---------------------------------------------------------------------------
# evaluate — daily floor
# ---------------------------------------------------------------------------


def test_evaluate_ok_when_equity_above_daily_floor(tmp_path: Path) -> None:
    mon = _make_monitor(tmp_path, initial_balance=10000.0)
    mon.start_of_day_snapshot(10000.0)
    result = mon.evaluate(equity=9600.0, balance=9600.0)
    assert result.daily_ok is True   # floor = 10000 - 500 = 9500


def test_evaluate_fails_at_daily_floor(tmp_path: Path) -> None:
    mon = _make_monitor(tmp_path, initial_balance=10000.0)
    mon.start_of_day_snapshot(10000.0)
    # floor = 10000 - 500 = 9500; equity = 9499 → breach
    result = mon.evaluate(equity=9499.0, balance=9499.0)
    assert result.daily_ok is False


def test_evaluate_exactly_at_daily_floor_fails(tmp_path: Path) -> None:
    """Equity exactly equal to floor is NOT ok (> not >=)."""
    mon = _make_monitor(tmp_path, initial_balance=10000.0)
    mon.start_of_day_snapshot(10000.0)
    result = mon.evaluate(equity=9500.0, balance=9500.0)
    assert result.daily_ok is False


def test_daily_floor_rises_with_day_start_balance(tmp_path: Path) -> None:
    """If you had profits yesterday, today's floor is higher."""
    mon = _make_monitor(tmp_path, initial_balance=10000.0)
    mon.start_of_day_snapshot(10500.0)  # account grew by $500
    result = mon.evaluate(equity=10100.0, balance=10100.0)
    # floor = 10500 - 500 = 10000; equity 10100 > 10000 → ok
    assert result.daily_ok is True
    assert abs(result.daily_floor - 10000.0) < 0.01

    result2 = mon.evaluate(equity=9950.0, balance=9950.0)
    # equity 9950 < 10000 → breach
    assert result2.daily_ok is False


# ---------------------------------------------------------------------------
# evaluate — total floor
# ---------------------------------------------------------------------------


def test_evaluate_ok_when_equity_above_total_floor(tmp_path: Path) -> None:
    mon = _make_monitor(tmp_path, initial_balance=10000.0)
    mon.start_of_day_snapshot(10000.0)
    result = mon.evaluate(equity=9100.0, balance=9100.0)
    assert result.total_ok is True   # floor = 10000 - 1000 = 9000


def test_evaluate_fails_at_total_floor(tmp_path: Path) -> None:
    mon = _make_monitor(tmp_path, initial_balance=10000.0)
    mon.start_of_day_snapshot(10000.0)
    result = mon.evaluate(equity=8999.0, balance=8999.0)
    assert result.total_ok is False


def test_total_floor_is_based_on_initial_not_day_start(tmp_path: Path) -> None:
    """Total floor never moves — always based on initial_balance."""
    mon = _make_monitor(tmp_path, initial_balance=10000.0)
    mon.start_of_day_snapshot(10500.0)
    result = mon.evaluate(equity=9001.0, balance=9001.0)
    # total floor = 10000 - 1000 = 9000; equity 9001 > 9000 → still ok
    assert result.total_ok is True
    assert abs(result.total_floor - 9000.0) < 0.01


# ---------------------------------------------------------------------------
# evaluate — equity vs balance (FTMO uses min of both)
# ---------------------------------------------------------------------------


def test_evaluate_uses_min_of_equity_and_balance(tmp_path: Path) -> None:
    """If open trades are losing, equity < balance — that's the value that matters."""
    mon = _make_monitor(tmp_path, initial_balance=10000.0)
    mon.start_of_day_snapshot(10000.0)
    # balance fine, equity in open drawdown hits floor
    result = mon.evaluate(equity=9499.0, balance=10000.0)
    assert result.daily_ok is False


def test_evaluate_balance_below_floor_even_if_equity_ok(tmp_path: Path) -> None:
    """If balance has dipped below floor (closed losses), block regardless of equity."""
    mon = _make_monitor(tmp_path, initial_balance=10000.0)
    mon.start_of_day_snapshot(10000.0)
    result = mon.evaluate(equity=10000.0, balance=9499.0)
    assert result.daily_ok is False


# ---------------------------------------------------------------------------
# pnl_pct values
# ---------------------------------------------------------------------------


def test_daily_pnl_pct_calculation(tmp_path: Path) -> None:
    mon = _make_monitor(tmp_path, initial_balance=10000.0)
    mon.start_of_day_snapshot(10000.0)
    result = mon.evaluate(equity=9800.0, balance=9800.0)
    # pnl = (9800 - 10000) / 10000 * 100 = -2.0%
    assert abs(result.daily_pnl_pct - (-2.0)) < 0.01


def test_total_pnl_pct_positive_when_account_grew(tmp_path: Path) -> None:
    mon = _make_monitor(tmp_path, initial_balance=10000.0)
    mon.start_of_day_snapshot(10200.0)
    result = mon.evaluate(equity=10200.0, balance=10200.0)
    assert result.total_pnl_pct > 0


# ---------------------------------------------------------------------------
# Buffer
# ---------------------------------------------------------------------------


def test_daily_buffer_tightens_floor(tmp_path: Path) -> None:
    """With a 0.5% buffer, trading stops at 4.5% loss, not 5%."""
    mon = _make_monitor(tmp_path, initial_balance=10000.0, daily_buffer_pct=0.005)
    mon.start_of_day_snapshot(10000.0)
    # effective floor = 10000 - (500 - 50) = 10000 - 450 = 9550
    result = mon.evaluate(equity=9540.0, balance=9540.0)
    assert result.daily_ok is False

    result2 = mon.evaluate(equity=9600.0, balance=9600.0)
    assert result2.daily_ok is True


# ---------------------------------------------------------------------------
# start_of_day_snapshot — new day detection
# ---------------------------------------------------------------------------


def test_snapshot_updates_on_new_day(tmp_path: Path) -> None:
    """Simulates the bot running on two different Prague days."""
    state_file = tmp_path / "ftmo_state.json"

    # Day 1 — balance 10000
    with patch("src.ops.ftmo_risk_monitor._prague_today", return_value="2026-05-14"):
        mon1 = FTMORiskMonitor(
            initial_balance=10000.0,
            daily_buffer_pct=0.0,
            total_buffer_pct=0.0,
            state_path=state_file,
        )
        mon1.start_of_day_snapshot(10000.0)
        assert mon1.get_day_start_balance() == 10000.0
        assert mon1.get_snapshot_date() == "2026-05-14"

    # Day 2 — balance grew to 10200
    with patch("src.ops.ftmo_risk_monitor._prague_today", return_value="2026-05-15"):
        mon2 = FTMORiskMonitor(
            initial_balance=10000.0,
            daily_buffer_pct=0.0,
            total_buffer_pct=0.0,
            state_path=state_file,
        )
        mon2.start_of_day_snapshot(10200.0)
        assert mon2.get_day_start_balance() == 10200.0
        assert mon2.get_snapshot_date() == "2026-05-15"


def test_snapshot_not_updated_same_day(tmp_path: Path) -> None:
    """Multiple calls on the same day don't overwrite the snapshot."""
    with patch("src.ops.ftmo_risk_monitor._prague_today", return_value="2026-05-14"):
        mon = _make_monitor(tmp_path)
        mon.start_of_day_snapshot(10000.0)
        mon.start_of_day_snapshot(10500.0)  # second call — should be ignored
        assert mon.get_day_start_balance() == 10000.0


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def test_state_file_created_on_snapshot(tmp_path: Path) -> None:
    state_file = tmp_path / "ftmo_state.json"
    with patch("src.ops.ftmo_risk_monitor._prague_today", return_value="2026-05-14"):
        mon = FTMORiskMonitor(
            initial_balance=10000.0, daily_buffer_pct=0.0, total_buffer_pct=0.0,
            state_path=state_file,
        )
        mon.start_of_day_snapshot(10000.0)

    assert state_file.exists()
    data = json.loads(state_file.read_text())
    assert data["initial_balance"] == 10000.0
    assert data["day_start_balance"] == 10000.0
    assert data["snapshot_date"] == "2026-05-14"


def test_state_loaded_on_new_instance(tmp_path: Path) -> None:
    """A new instance reads the saved snapshot and uses the right day_start balance."""
    state_file = tmp_path / "ftmo_state.json"
    with patch("src.ops.ftmo_risk_monitor._prague_today", return_value="2026-05-14"):
        mon1 = FTMORiskMonitor(
            initial_balance=10000.0, daily_buffer_pct=0.0, total_buffer_pct=0.0,
            state_path=state_file,
        )
        mon1.start_of_day_snapshot(10300.0)

    # New instance on same day — loads saved snapshot
    with patch("src.ops.ftmo_risk_monitor._prague_today", return_value="2026-05-14"):
        mon2 = FTMORiskMonitor(
            initial_balance=10000.0, daily_buffer_pct=0.0, total_buffer_pct=0.0,
            state_path=state_file,
        )
        mon2.start_of_day_snapshot(10350.0)  # higher balance — should NOT overwrite
        assert mon2.get_day_start_balance() == 10300.0  # original snapshot preserved


def test_missing_state_file_starts_fresh(tmp_path: Path) -> None:
    state_file = tmp_path / "nonexistent" / "ftmo_state.json"
    mon = FTMORiskMonitor(
        initial_balance=10000.0, daily_buffer_pct=0.0, total_buffer_pct=0.0,
        state_path=state_file,
    )
    # Should not raise — uses initial_balance as fallback
    assert mon.get_day_start_balance() == 10000.0


def test_corrupt_state_file_starts_fresh(tmp_path: Path) -> None:
    state_file = tmp_path / "ftmo_state.json"
    state_file.write_text("not json at all", encoding="utf-8")
    mon = FTMORiskMonitor(
        initial_balance=10000.0, daily_buffer_pct=0.0, total_buffer_pct=0.0,
        state_path=state_file,
    )
    assert mon.get_day_start_balance() == 10000.0


# ---------------------------------------------------------------------------
# No state_path — in-memory only
# ---------------------------------------------------------------------------


def test_no_state_path_works_in_memory() -> None:
    mon = FTMORiskMonitor(
        initial_balance=10000.0, daily_buffer_pct=0.0, total_buffer_pct=0.0,
    )
    mon.start_of_day_snapshot(10000.0)
    result = mon.evaluate(equity=9600.0, balance=9600.0)
    assert result.daily_ok is True
    assert result.total_ok is True


# ---------------------------------------------------------------------------
# FTMORiskResult fields
# ---------------------------------------------------------------------------


def test_result_contains_floors(tmp_path: Path) -> None:
    mon = _make_monitor(tmp_path, initial_balance=10000.0)
    mon.start_of_day_snapshot(10000.0)
    result = mon.evaluate(equity=9800.0, balance=9800.0)
    assert abs(result.daily_floor - 9500.0) < 0.01
    assert abs(result.total_floor - 9000.0) < 0.01
    assert result.day_start_balance == 10000.0
    assert result.initial_balance == 10000.0
