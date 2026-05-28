"""Tests for session loop fixes and trading_window config state (task #43).

Covers three fixes applied in the May 27-28 session:
  - Fix #44: session sleep loop — cycle counter moved after session check,
             continue added after sleep to prevent CLOSED boundary scans
  - Fix #45: trading_window open 00:00–19:00 in both live configs

The loop helpers (_is_in_any_session, _seconds_until_next_session) are
imported directly from tools.live_scan_loop so they can be unit-tested
without running the full loop.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tools.live_scan_loop import _is_in_any_session, _seconds_until_next_session


# ---------------------------------------------------------------------------
# Shared session config fixture (mirrors live configs)
# ---------------------------------------------------------------------------

SESSIONS_CFG = {
    "ASIA":   {"start": "00:00", "end": "06:00"},
    "LONDON": {"start": "07:00", "end": "11:30"},
    "NY_AM":  {"start": "13:00", "end": "16:00"},
    "NY_PM":  {"start": "16:00", "end": "19:00"},
}


# ---------------------------------------------------------------------------
# _is_in_any_session — should cover all four active session windows
# ---------------------------------------------------------------------------

class TestIsInAnySession:
    def _dt(self, hour: int, minute: int = 0) -> datetime:
        return datetime(2026, 5, 28, hour, minute, 0, tzinfo=UTC)

    def test_asia_open(self) -> None:
        assert _is_in_any_session(self._dt(2, 0), SESSIONS_CFG) is True

    def test_asia_boundary_start(self) -> None:
        assert _is_in_any_session(self._dt(0, 0), SESSIONS_CFG) is True

    def test_asia_boundary_end_exclusive(self) -> None:
        # 06:00 is NOT in ASIA (end is exclusive).  Gap between 06:00–07:00.
        assert _is_in_any_session(self._dt(6, 0), SESSIONS_CFG) is False

    def test_gap_between_asia_and_london(self) -> None:
        assert _is_in_any_session(self._dt(6, 30), SESSIONS_CFG) is False

    def test_london_open(self) -> None:
        assert _is_in_any_session(self._dt(9, 0), SESSIONS_CFG) is True

    def test_london_boundary_start(self) -> None:
        assert _is_in_any_session(self._dt(7, 0), SESSIONS_CFG) is True

    def test_gap_between_london_and_ny_am(self) -> None:
        # 11:30–13:00 is a gap
        assert _is_in_any_session(self._dt(12, 0), SESSIONS_CFG) is False

    def test_ny_am_open(self) -> None:
        assert _is_in_any_session(self._dt(14, 0), SESSIONS_CFG) is True

    def test_ny_pm_open(self) -> None:
        assert _is_in_any_session(self._dt(17, 0), SESSIONS_CFG) is True

    def test_ny_pm_boundary_start(self) -> None:
        # NY_PM starts at 16:00, which is also where NY_AM ends — should be True
        assert _is_in_any_session(self._dt(16, 0), SESSIONS_CFG) is True

    def test_after_all_sessions(self) -> None:
        # 19:00 is after NY_PM ends
        assert _is_in_any_session(self._dt(19, 0), SESSIONS_CFG) is False

    def test_late_night_closed(self) -> None:
        assert _is_in_any_session(self._dt(21, 30), SESSIONS_CFG) is False

    def test_empty_sessions_cfg(self) -> None:
        assert _is_in_any_session(self._dt(9, 0), {}) is False


# ---------------------------------------------------------------------------
# _seconds_until_next_session — should return a positive value when closed
# ---------------------------------------------------------------------------

class TestSecondsUntilNextSession:
    def test_returns_positive_when_closed(self) -> None:
        # 19:30 UTC — all sessions finished, ASIA tomorrow at 00:00
        now = datetime(2026, 5, 28, 19, 30, 0, tzinfo=UTC)
        secs = _seconds_until_next_session(now, SESSIONS_CFG)
        assert secs > 0

    def test_next_session_is_london_from_gap(self) -> None:
        # 06:30 UTC — in the gap between ASIA and LONDON (07:00)
        now = datetime(2026, 5, 28, 6, 30, 0, tzinfo=UTC)
        secs = _seconds_until_next_session(now, SESSIONS_CFG)
        expected = 30 * 60  # 30 minutes to 07:00
        assert abs(secs - expected) < 5  # within 5 seconds

    def test_rolls_to_next_day_after_ny_pm(self) -> None:
        # 20:00 UTC — past all sessions, next is ASIA 00:00 tomorrow
        now = datetime(2026, 5, 28, 20, 0, 0, tzinfo=UTC)
        secs = _seconds_until_next_session(now, SESSIONS_CFG)
        expected = 4 * 3600  # 4 hours to midnight
        assert abs(secs - expected) < 5

    def test_no_negative_wait(self) -> None:
        # Should never return a negative wait time
        for hour in (6, 11, 12, 19, 20, 22):
            now = datetime(2026, 5, 28, hour, 0, 0, tzinfo=UTC)
            secs = _seconds_until_next_session(now, SESSIONS_CFG)
            assert secs >= 0, f"Negative wait at {hour}:00 UTC: {secs}"


# ---------------------------------------------------------------------------
# trading_window config state — both live configs must cover 00:00–19:00
# ---------------------------------------------------------------------------

LIVE_CONFIGS = [
    Path("src/config/live_one_order_test.json"),
    Path("src/config/live_market_watch.json"),
]


@pytest.mark.parametrize("config_path", LIVE_CONFIGS, ids=lambda p: p.name)
def test_trading_window_covers_all_sessions(config_path: Path) -> None:
    """trading_window must span 00:00–19:00 UTC so all four sessions are active.

    Fix #45: previously 07:00–16:00 which blocked ASIA and NY_PM execution.
    """
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    tw = cfg.get("trading_window", {})

    assert tw.get("enabled") is True, f"{config_path.name}: trading_window.enabled must be true"
    assert tw.get("timezone") == "UTC", f"{config_path.name}: trading_window.timezone must be UTC"
    assert tw.get("start_utc") == "00:00", (
        f"{config_path.name}: trading_window.start_utc must be '00:00', got '{tw.get('start_utc')}'"
    )
    assert tw.get("end_utc") == "19:00", (
        f"{config_path.name}: trading_window.end_utc must be '19:00', got '{tw.get('end_utc')}'"
    )


@pytest.mark.parametrize("config_path", LIVE_CONFIGS, ids=lambda p: p.name)
def test_trading_window_includes_all_weekdays(config_path: Path) -> None:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    days = cfg.get("trading_window", {}).get("days", [])
    for day in ("MON", "TUE", "WED", "THU", "FRI"):
        assert day in days, f"{config_path.name}: trading_window.days missing {day}"


@pytest.mark.parametrize("config_path", LIVE_CONFIGS, ids=lambda p: p.name)
def test_config_is_valid_json_no_null_bytes(config_path: Path) -> None:
    """Both configs must be clean JSON with no null byte corruption.

    Regression test for the Windows/Linux mount boundary null byte issue
    that corrupted live_market_watch.json during the May 27 session.
    """
    raw = config_path.read_bytes()
    assert b"\x00" not in raw, (
        f"{config_path.name}: null bytes detected — file is corrupted"
    )
    # Must also parse without error
    cfg = json.loads(raw.decode("utf-8"))
    assert isinstance(cfg, dict)
