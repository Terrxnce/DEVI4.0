"""Tests for EconomicCalendar — news window blocking and feed parsing."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.ops.economic_calendar import (
    EconomicCalendar,
    _currencies_for_symbol,
    _parse_event_time,
)


# ---------------------------------------------------------------------------
# _currencies_for_symbol
# ---------------------------------------------------------------------------


def test_currencies_for_eurusd() -> None:
    assert _currencies_for_symbol("EURUSD") == {"EUR", "USD"}


def test_currencies_for_gbpjpy() -> None:
    assert _currencies_for_symbol("GBPJPY") == {"GBP", "JPY"}


def test_currencies_for_xauusd() -> None:
    assert _currencies_for_symbol("XAUUSD") == {"USD"}


def test_currencies_for_nas100() -> None:
    assert _currencies_for_symbol("NAS100") == {"USD"}


def test_currencies_for_ger40() -> None:
    assert _currencies_for_symbol("GER40") == {"EUR"}


def test_currencies_for_lowercase() -> None:
    assert _currencies_for_symbol("eurusd") == {"EUR", "USD"}


def test_currencies_for_unknown_returns_empty() -> None:
    # 4-char symbol — matches no known proxy and no 6-char pair pattern
    assert _currencies_for_symbol("XYZW") == set()


# ---------------------------------------------------------------------------
# _parse_event_time
# ---------------------------------------------------------------------------


def test_parse_iso_with_offset() -> None:
    raw = "2026-05-16T08:30:00-04:00"
    result = _parse_event_time(raw)
    assert result is not None
    assert result.tzinfo is not None
    # 08:30 ET (UTC-4) = 12:30 UTC
    assert result.hour == 12
    assert result.minute == 30


def test_parse_iso_utc_Z() -> None:
    raw = "2026-05-16T13:30:00+00:00"
    result = _parse_event_time(raw)
    assert result is not None
    assert result.hour == 13


def test_parse_bare_datetime_treated_as_utc() -> None:
    raw = "2026-05-16T13:30:00"
    result = _parse_event_time(raw)
    assert result is not None
    assert result.hour == 13
    assert result.tzinfo == UTC


def test_parse_empty_string_returns_none() -> None:
    assert _parse_event_time("") is None


def test_parse_garbage_returns_none() -> None:
    assert _parse_event_time("not-a-date") is None


# ---------------------------------------------------------------------------
# Helpers for building mock feed data
# ---------------------------------------------------------------------------


def _make_event(
    currency: str = "USD",
    title: str = "Non-Farm Payroll",
    impact: str = "High",
    event_time: datetime | None = None,
) -> dict:
    if event_time is None:
        event_time = datetime.now(tz=UTC)
    return {
        "title": title,
        "country": "US",
        "date": event_time.isoformat(),
        "impact": impact,
        "currency": currency,
    }


def _make_calendar(
    events: list[dict],
    *,
    cache_path: Path | None = None,
    pre: int = 30,
    post: int = 15,
    block_on_failure: bool = True,
) -> EconomicCalendar:
    cal = EconomicCalendar(
        cache_path=cache_path,
        pre_event_minutes=pre,
        post_event_minutes=post,
        block_on_fetch_failure=block_on_failure,
    )
    # Inject pre-parsed events directly (bypasses network)
    cal._events = cal._parse_events(events)
    cal._last_fetched = datetime.now(tz=UTC)
    cal._fetch_failed = False
    return cal


# ---------------------------------------------------------------------------
# is_news_blocked — basic blocking
# ---------------------------------------------------------------------------


def test_blocked_during_pre_event_window() -> None:
    event_time = datetime(2026, 5, 16, 12, 30, 0, tzinfo=UTC)
    now = event_time - timedelta(minutes=20)  # 20 min before — inside 30-min window
    cal = _make_calendar([_make_event("USD", event_time=event_time)])
    blocked, reason = cal.is_news_blocked("EURUSD", now)
    assert blocked is True
    assert "Non-Farm" in reason or "news_window" in reason


def test_blocked_during_post_event_window() -> None:
    event_time = datetime(2026, 5, 16, 12, 30, 0, tzinfo=UTC)
    now = event_time + timedelta(minutes=10)  # 10 min after — inside 15-min window
    cal = _make_calendar([_make_event("USD", event_time=event_time)])
    blocked, _ = cal.is_news_blocked("EURUSD", now)
    assert blocked is True


def test_not_blocked_before_pre_event_window() -> None:
    event_time = datetime(2026, 5, 16, 12, 30, 0, tzinfo=UTC)
    now = event_time - timedelta(minutes=35)  # 35 min before — outside 30-min window
    cal = _make_calendar([_make_event("USD", event_time=event_time)])
    blocked, _ = cal.is_news_blocked("EURUSD", now)
    assert blocked is False


def test_not_blocked_after_post_event_window() -> None:
    event_time = datetime(2026, 5, 16, 12, 30, 0, tzinfo=UTC)
    now = event_time + timedelta(minutes=20)  # 20 min after — outside 15-min window
    cal = _make_calendar([_make_event("USD", event_time=event_time)])
    blocked, _ = cal.is_news_blocked("EURUSD", now)
    assert blocked is False


def test_blocked_at_exact_event_time() -> None:
    event_time = datetime(2026, 5, 16, 12, 30, 0, tzinfo=UTC)
    cal = _make_calendar([_make_event("USD", event_time=event_time)])
    blocked, _ = cal.is_news_blocked("EURUSD", event_time)
    assert blocked is True


# ---------------------------------------------------------------------------
# Currency matching
# ---------------------------------------------------------------------------


def test_usd_event_blocks_eurusd() -> None:
    event_time = datetime(2026, 5, 16, 12, 30, 0, tzinfo=UTC)
    cal = _make_calendar([_make_event("USD", event_time=event_time)])
    blocked, _ = cal.is_news_blocked("EURUSD", event_time)
    assert blocked is True


def test_eur_event_blocks_eurusd() -> None:
    event_time = datetime(2026, 5, 16, 12, 30, 0, tzinfo=UTC)
    cal = _make_calendar([_make_event("EUR", event_time=event_time)])
    blocked, _ = cal.is_news_blocked("EURUSD", event_time)
    assert blocked is True


def test_gbp_event_does_not_block_eurusd() -> None:
    event_time = datetime(2026, 5, 16, 12, 30, 0, tzinfo=UTC)
    cal = _make_calendar([_make_event("GBP", event_time=event_time)])
    blocked, _ = cal.is_news_blocked("EURUSD", event_time)
    assert blocked is False


def test_usd_event_blocks_xauusd() -> None:
    event_time = datetime(2026, 5, 16, 12, 30, 0, tzinfo=UTC)
    cal = _make_calendar([_make_event("USD", event_time=event_time)])
    blocked, _ = cal.is_news_blocked("XAUUSD", event_time)
    assert blocked is True


def test_eur_event_blocks_ger40() -> None:
    event_time = datetime(2026, 5, 16, 12, 30, 0, tzinfo=UTC)
    cal = _make_calendar([_make_event("EUR", event_time=event_time)])
    blocked, _ = cal.is_news_blocked("GER40", event_time)
    assert blocked is True


# ---------------------------------------------------------------------------
# Impact filter — Medium events should not block
# ---------------------------------------------------------------------------


def test_medium_impact_does_not_block() -> None:
    event_time = datetime(2026, 5, 16, 12, 30, 0, tzinfo=UTC)
    cal = _make_calendar([_make_event("USD", impact="Medium", event_time=event_time)])
    blocked, _ = cal.is_news_blocked("EURUSD", event_time)
    assert blocked is False


def test_low_impact_does_not_block() -> None:
    event_time = datetime(2026, 5, 16, 12, 30, 0, tzinfo=UTC)
    cal = _make_calendar([_make_event("USD", impact="Low", event_time=event_time)])
    blocked, _ = cal.is_news_blocked("EURUSD", event_time)
    assert blocked is False


# ---------------------------------------------------------------------------
# Fetch failure behaviour
# ---------------------------------------------------------------------------


def test_fetch_failure_blocks_when_block_on_failure_true() -> None:
    cal = EconomicCalendar(block_on_fetch_failure=True)
    cal._fetch_failed = True
    blocked, reason = cal.is_news_blocked("EURUSD", datetime.now(tz=UTC))
    assert blocked is True
    assert "fetch_failed" in reason


def test_fetch_failure_does_not_block_when_flag_false() -> None:
    cal = EconomicCalendar(block_on_fetch_failure=False)
    cal._fetch_failed = True
    blocked, _ = cal.is_news_blocked("EURUSD", datetime.now(tz=UTC))
    assert blocked is False


# ---------------------------------------------------------------------------
# Cache — save and load
# ---------------------------------------------------------------------------


def test_cache_saved_after_fetch(tmp_path: Path) -> None:
    cache_file = tmp_path / "calendar_cache.json"
    events = [_make_event("USD", event_time=datetime.now(tz=UTC))]

    cal = EconomicCalendar(cache_path=cache_file, block_on_fetch_failure=False)
    # Manually trigger _save_cache
    cal._last_fetched = datetime.now(tz=UTC)
    cal._save_cache(events)

    assert cache_file.exists()
    data = json.loads(cache_file.read_text())
    assert "fetched_at" in data
    assert "events" in data


def test_cache_loaded_on_init(tmp_path: Path) -> None:
    cache_file = tmp_path / "calendar_cache.json"
    event_time = datetime(2026, 5, 16, 12, 30, 0, tzinfo=UTC)
    events = [_make_event("USD", event_time=event_time)]

    # Save cache manually
    wrapper = {
        "fetched_at": datetime.now(tz=UTC).isoformat(),
        "events": events,
    }
    cache_file.write_text(json.dumps(wrapper), encoding="utf-8")

    # Load via new instance
    cal = EconomicCalendar(cache_path=cache_file, block_on_fetch_failure=False)
    assert len(cal.get_events()) == 1


def test_corrupt_cache_starts_empty(tmp_path: Path) -> None:
    cache_file = tmp_path / "calendar_cache.json"
    cache_file.write_text("not json", encoding="utf-8")
    cal = EconomicCalendar(cache_path=cache_file, block_on_fetch_failure=False)
    assert cal.get_events() == []


# ---------------------------------------------------------------------------
# refresh_if_stale
# ---------------------------------------------------------------------------


def test_refresh_not_called_when_cache_fresh() -> None:
    cal = EconomicCalendar(block_on_fetch_failure=False, cache_ttl_hours=12.0)
    cal._last_fetched = datetime.now(tz=UTC)  # just fetched

    with patch.object(cal, "_fetch") as mock_fetch:
        refreshed = cal.refresh_if_stale()

    assert refreshed is False
    mock_fetch.assert_not_called()


def test_refresh_called_when_cache_stale() -> None:
    cal = EconomicCalendar(block_on_fetch_failure=False, cache_ttl_hours=12.0)
    cal._last_fetched = datetime.now(tz=UTC) - timedelta(hours=13)  # stale

    with patch.object(cal, "_fetch") as mock_fetch:
        refreshed = cal.refresh_if_stale()

    assert refreshed is True
    mock_fetch.assert_called_once()


def test_refresh_called_when_never_fetched() -> None:
    cal = EconomicCalendar(block_on_fetch_failure=False)
    # _last_fetched is None by default

    with patch.object(cal, "_fetch") as mock_fetch:
        refreshed = cal.refresh_if_stale()

    assert refreshed is True
    mock_fetch.assert_called_once()
