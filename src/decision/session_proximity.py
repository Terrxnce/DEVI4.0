"""Session proximity gate: blocks new entries when a session is about to close.

Prevents D.E.V.I from taking positions with insufficient time to develop before
the session_close_exit fires. A 15-minute trade that gets cut at session end
locks in noise as a loss — the setup never had room to breathe.

Logic:
    1. Look up the symbol's configured sessions from config["symbol_sessions"].
    2. Find which session the current bar_time falls inside.
    3. Calculate minutes remaining until that session ends.
    4. If minutes remaining < threshold, reject with "near_session_end".
    5. If the symbol is not currently inside any configured session, pass through
       (session_close_exit handles the close; this gate only protects active windows).

Config keys (under "entry_gate" section):
    session_proximity_gate_enabled  bool  Enable/disable the check. Default: False.
    session_proximity_gate_minutes  int   Minimum minutes required before session end.
                                          Default: 60.
"""
from __future__ import annotations

from datetime import datetime, time


def _parse_hhmm(value: str) -> time:
    """Parse 'HH:MM' string to a time object."""
    h, m = value.split(":")
    return time(int(h), int(m))


def minutes_until_session_end(
    symbol: str,
    now_utc: datetime,
    config: dict,
) -> int | None:
    """Return minutes remaining in the symbol's current active session.

    Returns None if the symbol is not currently inside any configured session.

    Args:
        symbol:   Symbol name, e.g. "GBPJPY".
        now_utc:  Current UTC time (bar_time from ContextSnapshot).
        config:   Full D.E.V.I config dict with "sessions" and "symbol_sessions".
    """
    sym_sessions_cfg: dict = config.get("symbol_sessions", {})
    default_sessions: list[str] = sym_sessions_cfg.get("default", [])
    symbol_sessions: list[str] = sym_sessions_cfg.get(symbol, default_sessions)

    all_sessions: dict = config.get("sessions", {})
    now_t = now_utc.time().replace(second=0, microsecond=0)

    for session_name in symbol_sessions:
        sess = all_sessions.get(session_name)
        if not sess:
            continue

        start_t = _parse_hhmm(sess["start"])
        end_t = _parse_hhmm(sess["end"])

        if start_t <= now_t < end_t:
            end_min = end_t.hour * 60 + end_t.minute
            now_min = now_t.hour * 60 + now_t.minute
            return end_min - now_min

    return None


def evaluate_session_proximity(
    symbol: str,
    now_utc: datetime,
    config: dict,
) -> tuple[bool, str]:
    """Check whether sufficient session time remains to justify a new entry.

    Returns (passes, failure_code).

    passes=True, failure_code=""
        Entry is acceptable: enough session time remaining, or gate is disabled,
        or symbol is not currently inside any session.

    passes=False, failure_code="near_session_end"
        Session closes within the configured threshold. Entry blocked.

    Args:
        symbol:   Symbol name, e.g. "GBPJPY".
        now_utc:  Current UTC time (bar_time from ContextSnapshot).
        config:   Full D.E.V.I config dict.
    """
    entry_gate_cfg: dict = config.get("entry_gate", {})

    if not bool(entry_gate_cfg.get("session_proximity_gate_enabled", False)):
        return True, ""

    threshold: int = int(entry_gate_cfg.get("session_proximity_gate_minutes", 60))

    mins_left = minutes_until_session_end(symbol=symbol, now_utc=now_utc, config=config)

    if mins_left is None:
        # Not in any session window — pass through.
        return True, ""

    if mins_left < threshold:
        return False, "near_session_end"

    return True, ""
