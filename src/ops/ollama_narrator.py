"""Ollama narrator — plain-English daily summary via local LLM.

Calls Ollama at localhost:11434 using llama3:latest.
Silent fail on any error — never blocks execution or Discord posting.

Usage:
    from src.ops.ollama_narrator import generate_narrative
    text = generate_narrative(acc)   # "" if Ollama is down
"""
from __future__ import annotations

from typing import Any

import requests as _requests


_OLLAMA_URL = "http://localhost:11434/api/generate"
_MODEL = "llama3.1:8b"
_TIMEOUT_SECONDS = 20


def generate_narrative(acc: dict[str, Any]) -> str:
    """Return a 2-3 sentence narrative for the daily summary.

    Returns empty string on any error — Ollama not running, timeout, etc.
    """
    try:
        prompt = _build_prompt(acc)
        resp = _requests.post(
            _OLLAMA_URL,
            json={"model": _MODEL, "prompt": prompt, "stream": False},
            timeout=_TIMEOUT_SECONDS,
        )
        data = resp.json()
        return str(data.get("response", "")).strip()
    except BaseException:
        return ""


def _build_prompt(acc: dict[str, Any]) -> str:
    date = acc.get("date", "today")

    opening = acc.get("opening_balance")
    closing = acc.get("last_balance")
    pnl_str = "P&L: unavailable"
    if opening and closing:
        pnl = closing - opening
        pnl_pct = (pnl / opening) * 100.0
        pnl_str = f"P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%)"

    trades = acc.get("trade_outcomes") or acc.get("orders_filled", [])
    trade_lines: list[str] = []
    for t in trades:
        sym = t.get("symbol", "")
        side = "BUY" if "BULL" in str(t.get("side", "")).upper() else "SELL"
        outcome = t.get("outcome", "")
        profit = t.get("profit")
        profit_str = f"${profit:+.2f}" if profit is not None else ""
        rr = t.get("rr")
        rr_str = f"RR {rr:.1f}" if rr else ""
        sc = t.get("setup_class", "")
        tier = t.get("confidence_tier", "")
        tag = f"[{sc}/{tier}]" if sc else ""
        trade_lines.append(
            f"  {sym} {side} {rr_str} {tag} {outcome} {profit_str}".strip()
        )

    trades_str = "\n".join(trade_lines) if trade_lines else "No trades taken today"

    rejection_counter: dict = acc.get("rejection_counter", {})
    top_blocks = [
        f"{code} ({count})"
        for code, count in list(rejection_counter.items())[:3]
    ]
    blocks_str = ", ".join(top_blocks) if top_blocks else "none"

    positions = acc.get("last_positions", [])
    open_str = f"{len(positions)} position(s) still open at day end" if positions else "no open positions at day end"

    return (
        "You are a concise trading system analyst reviewing an automated FX trading bot.\n"
        "Write exactly 2-3 sentences summarising today's performance.\n"
        "Be factual and direct. No encouragement, no filler.\n\n"
        f"Date: {date}\n"
        f"{pnl_str}\n"
        f"Trades:\n{trades_str}\n"
        f"Status: {open_str}\n"
        f"Top rejection reasons: {blocks_str}\n"
        f"Cycles run: {acc.get('cycles', 0)}, Symbols evaluated: {acc.get('symbols_evaluated', 0)}\n"
    )
