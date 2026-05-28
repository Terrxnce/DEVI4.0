"""Discord webhook notifier for DEVI trade signals and daily summaries.

Reads the webhook URL from the DISCORD_WEBHOOK_URL environment variable.
If the variable is not set, all methods are no-ops — execution is never blocked.

Usage:
    export DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
    notifier = DiscordNotifier()
    notifier.send_trade_signal(symbol="EURUSD", side="BEARISH", ...)
    notifier.send_daily_summary(acc)
"""
from __future__ import annotations

import json
import os
from typing import Any

import requests as _requests


WEBHOOK_ENV_VAR = "DISCORD_WEBHOOK_URL"
_TIMEOUT_SECONDS = 5


def _fmt_price(price: float) -> str:
    if price >= 100.0:
        return f"{price:.2f}"
    if price >= 10.0:
        return f"{price:.3f}"
    return f"{price:.5f}"


def _fmt_balance(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "n/a"


class DiscordNotifier:
    def __init__(self, webhook_url: str | None = None) -> None:
        self._url = (webhook_url or os.environ.get(WEBHOOK_ENV_VAR, "")).strip()

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    def _post(self, content: str) -> None:
        """POST a message to Discord. Silent fail on any error."""
        if not self.enabled:
            return
        try:
            _requests.post(
                self._url,
                json={"content": content},
                timeout=_TIMEOUT_SECONDS,
            )
        except Exception:
            pass  # Never block execution for a notification failure

    def send_trade_signal(
        self,
        *,
        symbol: str,
        side: str,
        entry: float,
        sl: float,
        tp: float,
        rr: float | None,
        setup_class: str,
        tier: str,
    ) -> None:
        """Send a trade signal message on confirmed fill."""
        direction = "BUY" if "BULL" in side.upper() else "SELL"
        emoji = "🟢" if direction == "BUY" else "🔴"
        rr_str = f"{rr:.2f}" if rr is not None else "n/a"

        lines = [
            "🤖 **DEVI Signal**",
            "",
            f"{emoji} **{symbol} {direction}**",
            f"Entry: `{_fmt_price(entry)}`",
            f"SL:    `{_fmt_price(sl)}`",
            f"TP:    `{_fmt_price(tp)}`",
            f"RR: {rr_str} | {tier} | {setup_class}",
        ]
        self._post("\n".join(lines))

    def send_daily_summary(self, acc: dict, narrative: str = "") -> None:
        """Send the end-of-day summary to Discord."""
        date_label = acc.get("date", "")
        cycles = acc.get("cycles", 0)
        opening = acc.get("opening_balance")
        closing = acc.get("last_balance")
        equity = acc.get("last_equity")
        executions = acc.get("executions", 0)
        evaluated = acc.get("symbols_evaluated", 0)

        # P&L
        pnl_str = ""
        if opening is not None and closing is not None:
            try:
                pnl = float(closing) - float(opening)
                pct = (pnl / float(opening)) * 100.0 if opening else 0.0
                sign = "+" if pnl >= 0 else ""
                pnl_str = f"  `{sign}${pnl:,.2f}  {sign}{pct:.2f}%`"
            except (TypeError, ValueError):
                pass

        unrealised_str = ""
        if closing is not None and equity is not None:
            try:
                u = float(equity) - float(closing)
                sign = "+" if u >= 0 else ""
                unrealised_str = f"  Unrealised: `{sign}${u:,.2f}`"
            except (TypeError, ValueError):
                pass

        lines = [
            f"📊 **DEVI — {date_label}**",
            "",
            f"Balance: `${_fmt_balance(closing)}`{pnl_str}",
        ]
        if unrealised_str:
            lines.append(unrealised_str.strip())
        lines.append(f"Cycles: {cycles}  |  Evaluated: {evaluated}  |  Executed: {executions}")

        # Trades — use outcomes if available, fall back to raw filled
        trades: list[dict] = acc.get("trade_outcomes") or acc.get("orders_filled", [])
        if trades:
            lines.append("")
            lines.append("**Trades:**")
            for r in trades:
                side_raw = str(r.get("side", ""))
                direction = "BUY" if "BULL" in side_raw.upper() else "SELL"
                emoji = "🟢" if direction == "BUY" else "🔴"
                sym = r.get("symbol", "")
                rr = r.get("rr")
                rr_str = f"RR {rr:.2f}" if rr else ""
                sc = r.get("setup_class", "")
                tier = r.get("confidence_tier", "")
                tag = f"[{sc}/{tier}]" if sc or tier else ""
                ts = r.get("timestamp", "")
                time_str = ts[11:16] if ts else ""
                outcome = r.get("outcome", "")
                profit = r.get("profit")
                outcome_str = ""
                if outcome == "TP_HIT":
                    outcome_str = "✅ TP"
                elif outcome == "SL_HIT":
                    outcome_str = "❌ SL"
                elif outcome == "SESSION_CLOSE":
                    outcome_str = "⏹ Closed"
                elif outcome == "RUNNING":
                    outcome_str = "⏳ open"
                elif outcome:
                    outcome_str = outcome
                if profit is not None:
                    sign = "+" if profit >= 0 else ""
                    outcome_str += f"  `{sign}${profit:,.2f}`"
                parts = [p for p in [f"{emoji} {sym}", direction, rr_str, tag, time_str, outcome_str] if p]
                lines.append("  " + "  ".join(parts))

            if acc.get("trade_outcomes"):
                tp_hits = sum(1 for t in trades if t.get("outcome") == "TP_HIT")
                sl_hits = sum(1 for t in trades if t.get("outcome") == "SL_HIT")
                sess_closes = sum(1 for t in trades if t.get("outcome") == "SESSION_CLOSE")
                running = sum(1 for t in trades if t.get("outcome") == "RUNNING")
                closed = len(trades) - running
                if closed > 0:
                    tally = f"TP: {tp_hits}  SL: {sl_hits}  Closed: {sess_closes}"
                    if running:
                        tally += f"  |  {running} running"
                    lines.append(f"  {tally}")
        else:
            # Check for positions opened in a prior session that closed today
            closed_today: list[dict] = acc.get("closed_deals_today", [])
            if closed_today:
                lines.append("")
                lines.append("**Closed today (prior positions):**")
                for d in closed_today:
                    sym = d.get("symbol", "")
                    profit = d.get("profit")
                    if profit is not None:
                        sign = "+" if profit >= 0 else ""
                        emoji = "✅" if profit > 0 else ("❌" if profit < 0 else "⬜")
                        lines.append(f"  {emoji} {sym}  `{sign}${profit:,.2f}`")
                    else:
                        lines.append(f"  ⬜ {sym}")
            else:
                lines.append("")
                lines.append("**Trades:** None today")

        # Open positions
        positions: list[dict] = acc.get("last_positions", [])
        if positions:
            lines.append("")
            lines.append(f"**Open at day end:** {len(positions)}")
            for p in positions:
                sym = p.get("symbol", "")
                side_raw = str(p.get("side", ""))
                direction = "BUY" if "BULL" in side_raw.upper() else "SELL"
                emoji = "🟢" if direction == "BUY" else "🔴"
                lines.append(f"  {emoji} {sym} {direction}")
        else:
            lines.append("")
            lines.append("**Open positions:** None")

        if narrative:
            lines.append("")
            lines.append(f"_{narrative}_")

        self._post("\n".join(lines))
