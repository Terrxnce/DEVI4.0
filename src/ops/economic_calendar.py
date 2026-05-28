"""EconomicCalendar — fetches ForexFactory high-impact events and blocks trading.

Fetches the weekly calendar from the ForexFactory-compatible JSON feed:
  https://nfs.faireconomy.media/ff_calendar_thisweek.json

The feed updates during the week. The module caches the response to a local
JSON file and refreshes it once per day (configurable).

Typical usage:
    calendar = EconomicCalendar(cache_path="logs/prod/calendar_cache.json")
    calendar.refresh_if_stale()
    if calendar.is_news_blocked("EURUSD", datetime.now(tz=UTC)):
        # skip this cycle

Block window (configurable, defaults):
    - pre_event_minutes:  30  (block starts 30 min before event)
    - post_event_minutes: 15  (block ends 15 min after event)

On network failure the behaviour is controlled by block_on_fetch_failure:
    - True  (default): block trading to be safe
    - False: allow trading with a warning logged

Currency mapping:
    The feed labels events with a currency code (USD, EUR, GBP, etc.).
    Pairs are blocked if either currency in the pair has a high-impact event.
    XAUUSD is treated as USD. Indices (GER40, NAS100, etc.) block on USD/EUR.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# Pairs that have no clean base/quote currency — treated as USD-sensitive
_USD_PROXIES = {"XAUUSD", "XAGUSD", "US30", "NAS100", "SPX500", "US500", "USTEC", "DJ30"}
# EUR-sensitive indices
_EUR_PROXIES = {"GER40", "UK100"}


def _currencies_for_symbol(symbol: str) -> set[str]:
    """Extract the currency codes a symbol is sensitive to."""
    symbol = symbol.upper().rstrip(".")
    if symbol in _USD_PROXIES:
        return {"USD"}
    if symbol in _EUR_PROXIES:
        return {"EUR"}
    # Standard 6-char pair: EURUSD → EUR + USD
    if len(symbol) == 6 and symbol.isalpha():
        return {symbol[:3], symbol[3:]}
    # Broker-suffixed pair: EURUSD_micro → EUR + USD
    m = re.match(r"^([A-Z]{3})([A-Z]{3})", symbol)
    if m:
        return {m.group(1), m.group(2)}
    logger.debug("economic_calendar: unrecognised symbol format '%s' — no block", symbol)
    return set()


class EconomicCalendar:
    """Downloads, caches, and queries high-impact economic events.

    Args:
        cache_path:               Local path to cache the weekly JSON.
        calendar_url:             Feed URL (override for testing).
        pre_event_minutes:        How many minutes before an event to block.
        post_event_minutes:       How many minutes after an event to block.
        impact_filter:            Set of impact levels to block on (default: {"High"}).
        cache_ttl_hours:          How often to re-fetch the calendar (default: 12).
        block_on_fetch_failure:   If True, block trading when the feed is unreachable.
        fetch_timeout_seconds:    HTTP timeout for the calendar fetch.
    """

    def __init__(
        self,
        *,
        cache_path: Path | str | None = None,
        calendar_url: str = _CALENDAR_URL,
        pre_event_minutes: int = 30,
        post_event_minutes: int = 15,
        impact_filter: set[str] | None = None,
        cache_ttl_hours: float = 12.0,
        block_on_fetch_failure: bool = True,
        fetch_timeout_seconds: int = 10,
    ) -> None:
        self._cache_path = Path(cache_path) if cache_path is not None else None
        self._url = calendar_url
        self._pre = timedelta(minutes=pre_event_minutes)
        self._post = timedelta(minutes=post_event_minutes)
        self._impact_filter = impact_filter or {"High"}
        self._ttl = timedelta(hours=cache_ttl_hours)
        self._block_on_failure = block_on_fetch_failure
        self._timeout = fetch_timeout_seconds

        # In-memory cache: list of parsed event dicts
        self._events: list[dict[str, Any]] = []
        self._last_fetched: datetime | None = None
        self._fetch_failed: bool = False

        # Load from disk cache on startup
        if self._cache_path is not None:
            self._load_cache()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def refresh_if_stale(self) -> bool:
        """Fetch fresh calendar data if the cache is older than cache_ttl_hours.

        Returns True if a fresh fetch was attempted (regardless of success).
        Call this once per scan loop cycle before is_news_blocked().
        """
        now = datetime.now(tz=UTC)
        if self._last_fetched is not None and (now - self._last_fetched) < self._ttl:
            return False
        self._fetch()
        return True

    def is_news_blocked(self, symbol: str, utc_now: datetime | None = None) -> tuple[bool, str]:
        """Return (blocked, reason) for the given symbol at the given UTC time.

        If fetch previously failed and block_on_fetch_failure is True,
        returns (True, "fetch_failed").
        """
        if self._fetch_failed and self._block_on_failure:
            return True, "calendar_fetch_failed_blocking_as_safe"

        now = utc_now or datetime.now(tz=UTC)
        currencies = _currencies_for_symbol(symbol)
        if not currencies:
            return False, ""

        for event in self._events:
            event_currency = event.get("currency", "").upper()
            if event_currency not in currencies:
                continue
            event_time = event.get("_parsed_time")
            if event_time is None:
                continue
            window_start = event_time - self._pre
            window_end = event_time + self._post
            if window_start <= now <= window_end:
                reason = (
                    f"news_window: {event.get('title', 'unknown')} "
                    f"({event_currency}) at {event_time.strftime('%H:%M')} UTC"
                )
                logger.info(
                    "economic_calendar: blocked symbol=%s %s", symbol, reason
                )
                return True, reason

        return False, ""

    def get_events(self) -> list[dict[str, Any]]:
        """Return the current list of high-impact events (read-only copy)."""
        return list(self._events)

    # ------------------------------------------------------------------
    # Fetch and parse
    # ------------------------------------------------------------------

    def _fetch(self) -> None:
        """Download the calendar feed, parse it, and update the cache."""
        logger.info("economic_calendar: fetching %s", self._url)
        try:
            req = urllib.request.Request(
                self._url,
                headers={"User-Agent": "DEVI/4.0 economic-calendar-client"},
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            logger.warning("economic_calendar: fetch failed (%s) — using stale cache", exc)
            self._fetch_failed = True
            return
        except json.JSONDecodeError as exc:
            logger.warning("economic_calendar: invalid JSON from feed (%s)", exc)
            self._fetch_failed = True
            return

        self._fetch_failed = False
        self._events = self._parse_events(data)
        self._last_fetched = datetime.now(tz=UTC)
        logger.info(
            "economic_calendar: fetched %d high-impact events", len(self._events)
        )
        self._save_cache(data)

    def _parse_events(self, raw_data: list[Any]) -> list[dict[str, Any]]:
        """Filter and parse the raw JSON feed into usable event dicts."""
        if not isinstance(raw_data, list):
            logger.warning("economic_calendar: expected list, got %s", type(raw_data).__name__)
            return []

        events = []
        for item in raw_data:
            if not isinstance(item, dict):
                continue
            impact = item.get("impact", "")
            if impact not in self._impact_filter:
                continue
            # Parse event time — feed uses "MM-DD-YYYY'T'HH:MM:SS" or ISO 8601
            raw_time = item.get("date", "")
            parsed_time = _parse_event_time(raw_time)
            if parsed_time is None:
                logger.debug(
                    "economic_calendar: could not parse time '%s' for '%s'",
                    raw_time, item.get("title", "")
                )
                continue
            event = dict(item)
            event["_parsed_time"] = parsed_time
            events.append(event)

        return events

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def _load_cache(self) -> None:
        if self._cache_path is None or not self._cache_path.exists():
            return
        try:
            raw = self._cache_path.read_text(encoding="utf-8")
            wrapper = json.loads(raw)
            fetched_str = wrapper.get("fetched_at")
            data = wrapper.get("events", [])
            if fetched_str:
                self._last_fetched = datetime.fromisoformat(fetched_str)
            self._events = self._parse_events(data)
            logger.info(
                "economic_calendar: loaded %d events from cache (fetched=%s)",
                len(self._events), fetched_str,
            )
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning("economic_calendar: could not load cache (%s)", exc)

    def _save_cache(self, raw_data: Any) -> None:
        if self._cache_path is None:
            return
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            wrapper = {
                "fetched_at": (self._last_fetched or datetime.now(tz=UTC)).isoformat(),
                "events": raw_data,
            }
            self._cache_path.write_text(json.dumps(wrapper, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("economic_calendar: could not save cache (%s)", exc)


# ---------------------------------------------------------------------------
# Time parsing helpers
# ---------------------------------------------------------------------------


def _parse_event_time(raw: str) -> datetime | None:
    """Parse ForexFactory feed time strings into UTC-aware datetimes.

    The feed uses Eastern Time (ET = UTC-5 standard / UTC-4 DST).
    The JSON includes an offset like '2026-05-16T08:30:00-04:00' when available.
    Fall back to treating ambiguous times as UTC.
    """
    if not raw:
        return None
    raw = raw.strip()
    # Try ISO 8601 with timezone offset (preferred — feed often includes offset)
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M%z",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.astimezone(UTC)
        except ValueError:
            continue

    # No offset info — some entries have bare datetime strings; treat as UTC
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%m-%d-%YT%H:%M:%S",
        "%m-%d-%YT%H:%M",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=UTC)
        except ValueError:
            continue

    return None
