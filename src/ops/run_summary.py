"""Run summary printer — clean terminal output after each trading day.

Called from tools/live_scan_loop.py once per day after NY_PM session closes.
Accumulates data across all cycles during the day, then prints a single
structured daily summary.
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any


_DIVIDER = "─" * 56
_SKIP_DECISIONS = {"SKIPPED", "HOLD"}


def _fmt_price(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.5f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_lot(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_balance(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_pnl(opening: Any, closing: Any) -> str:
    """Format day P&L as '$+243.50 (+0.24%)' or 'n/a'."""
    try:
        o = float(opening)
        c = float(closing)
        pnl = c - o
        pct = (pnl / o) * 100.0 if o else 0.0
        sign = "+" if pnl >= 0 else ""
        return f"${sign}{pnl:,.2f} ({sign}{pct:.2f}%)"
    except (TypeError, ValueError):
        return "n/a"


def _session_label(result: dict) -> str:
    """Best-effort session label from symbol_results."""
    rows = result.get("symbol_results") or []
    for row in rows:
        if isinstance(row, dict):
            sess = row.get("session")
            if sess:
                return str(sess)
    return ""


# ---------------------------------------------------------------------------
# Outcome matching
# ---------------------------------------------------------------------------

def _classify_outcome(
    close_price: float,
    tp: float,
    sl: float,
    profit: float,
) -> str:
    """Classify a closed trade as TP_HIT, SL_HIT, or SESSION_CLOSE.

    Uses tight price proximity (5% of TP-SL range) to detect actual TP/SL hits.
    Falls back to SESSION_CLOSE — never guesses from profit sign, which caused
    false TP_HIT/SL_HIT labels on every session-closed trade.

    SESSION_CLOSE covers: session_close_exit, trail exit, breakeven close,
    and any other managed exit that doesn't touch TP or SL exactly.
    """
    if close_price and tp and sl and tp != sl:
        tolerance = abs(tp - sl) * 0.05  # tight: 5% of range, not 10%
        if abs(close_price - tp) <= tolerance:
            return "TP_HIT"
        if abs(close_price - sl) <= tolerance:
            return "SL_HIT"
    # Price not near TP or SL — managed exit (session close, trail, etc.)
    # Do NOT fall back to profit sign: +profit ≠ TP_HIT, -profit ≠ SL_HIT
    return "SESSION_CLOSE"


def match_outcomes(
    orders_filled: list[dict],
    closed_deals: list[dict],
) -> list[dict]:
    """Enrich filled orders with close outcome from MT5 history deals.

    closed_deals: list of dicts with keys: symbol, profit, price, ticket (optional).
    Each deal's profit is already summed across partial + final closes by
    _fetch_day_outcomes, so one deal record = one complete position result.

    Matching strategy:
      1. By ticket (position_id from MT5) — precise, no cross-contamination
      2. Fall back to first unmatched deal for the same symbol

    Each deal is consumed after matching so same-symbol trades cannot
    share a deal (previously caused P&L cloning: USDJPY #2 got USDJPY #1's loss,
    GBPUSD #2 and #3 both got GBPUSD #1's profit).

    Returns enriched list — orders with no matching close get outcome=RUNNING.
    """
    # Build lookup by ticket; remaining symbol-only deals go in a fallback list
    unmatched_by_ticket: dict[int, dict] = {}
    unmatched_by_symbol: list[dict] = []
    for deal in closed_deals:
        t = deal.get("ticket")
        if t:
            unmatched_by_ticket[int(t)] = deal
        else:
            unmatched_by_symbol.append(deal)

    outcomes: list[dict] = []
    for order in orders_filled:
        tp = float(order.get("take_profit") or 0.0)
        sl = float(order.get("stop_loss") or 0.0)
        ticket = order.get("ticket")

        deal: dict | None = None

        # 1. Match by ticket — accurate even for repeated symbols
        if ticket is not None:
            deal = unmatched_by_ticket.pop(int(ticket), None)

        # 2. Fall back to first unmatched symbol deal (consumes it from pool)
        if deal is None:
            symbol = order.get("symbol", "")
            for i, d in enumerate(unmatched_by_symbol):
                if d.get("symbol") == symbol:
                    deal = unmatched_by_symbol.pop(i)
                    break

        if deal is None:
            outcomes.append({**order, "outcome": "RUNNING", "profit": None, "close_price": None})
            continue

        profit = float(deal.get("profit", 0.0))
        close_price = float(deal.get("price", 0.0))
        outcome = _classify_outcome(close_price, tp, sl, profit)
        outcomes.append({
            **order,
            "outcome": outcome,
            "profit": profit,
            "close_price": close_price,
        })
    return outcomes


# ---------------------------------------------------------------------------
# Per-cycle summary (terminal only)
# ---------------------------------------------------------------------------

def format_run_summary(result: dict, *, cycle: int | None = None) -> str:
    lines: list[str] = []
    now = datetime.now(tz=UTC).strftime("%d %b %Y %H:%M UTC")
    cycle_label = f"Cycle {cycle}  |  " if cycle is not None else ""
    verdict = result.get("verdict", "")
    verdict_tag = f"  [{verdict}]" if verdict and verdict != "PASS" else ""

    lines.append(_DIVIDER)
    lines.append(f"DEVI 4.0  |  {cycle_label}{now}{verdict_tag}")
    lines.append(_DIVIDER)

    # Account
    balance = result.get("account_balance")
    equity = result.get("account_equity")
    open_count = result.get("open_position_count", 0)
    lines.append(
        f"Account   Balance: {_fmt_balance(balance)}"
        f"  Equity: {_fmt_balance(equity)}"
        f"  Positions: {open_count}"
    )

    # Scan stats
    rows: list[dict] = [
        r for r in (result.get("symbol_results") or [])
        if isinstance(r, dict)
    ]
    total = len(rows)
    if total > 0:
        decision_counter: Counter[str] = Counter()
        failure_counter: Counter[str] = Counter()
        for row in rows:
            dec = str(row.get("decision", "UNKNOWN"))
            decision_counter[dec] += 1
            fc = row.get("failure_code") or row.get("skipped_reason") or ""
            if fc and dec not in ("EXECUTE",):
                failure_counter[str(fc)] += 1

        executed = decision_counter.get("EXECUTE", 0)
        held = decision_counter.get("HOLD", 0)
        skipped = sum(
            v for k, v in decision_counter.items()
            if k not in ("EXECUTE", "HOLD")
        )

        lines.append(
            f"Scan      {total} symbols"
            f"  |  EXECUTE: {executed}  HOLD: {held}  OTHER: {skipped}"
        )

        if failure_counter:
            top = failure_counter.most_common(5)
            lines.append("          Top rejections:")
            for code, count in top:
                lines.append(f"            {code:<42} {count}")
    else:
        halted = (result.get("execution_summary") or {}).get("halted")
        if halted:
            lines.append(f"Scan      Halted — {halted}")
        else:
            fc = result.get("failure_code") or result.get("reason") or ""
            lines.append(f"Scan      No symbol data  {('— ' + fc) if fc else ''}")

    # Orders this cycle
    filled = [r for r in rows if r.get("order_status") == "FILLED"]
    blocked = [r for r in rows if r.get("order_status") and r.get("order_status") != "FILLED"]

    if filled:
        lines.append(f"Orders    {len(filled)} filled:")
        for r in filled:
            side = str(r.get("side", "")).replace("BULLISH", "BUY").replace("BEARISH", "SELL")
            lot = _fmt_lot(r.get("lot_size"))
            entry = _fmt_price(r.get("entry_price"))
            sl = _fmt_price(r.get("stop_loss"))
            tp = _fmt_price(r.get("take_profit"))
            rr = r.get("rr")
            rr_str = f"  RR {rr:.2f}" if rr else ""
            sc = r.get("setup_class", "")
            tier = r.get("confidence_tier", "")
            tag = f"  [{sc}/{tier}]" if sc or tier else ""
            lines.append(
                f"            {r.get('symbol', '')}  {side}  {lot} lots"
                f"  @ {entry}  SL {sl}  TP {tp}{rr_str}{tag}"
            )
    elif blocked:
        lines.append(f"Orders    {len(blocked)} attempted, blocked:")
        for r in blocked[:3]:
            lines.append(
                f"            {r.get('symbol', '')}  {r.get('order_status', '')}  {r.get('failure_code', '')}"
            )
    else:
        lines.append("Orders    None this cycle")

    # Open positions
    positions = result.get("live_positions") or []
    if positions:
        lines.append(f"Positions {len(positions)} open:")
        for p in positions:
            side = str(p.get("side", "")).replace("BULLISH", "BUY").replace("BEARISH", "SELL")
            lot = _fmt_lot(p.get("lot_size"))
            entry = _fmt_price(p.get("open_price"))
            sl = _fmt_price(p.get("sl"))
            tp = _fmt_price(p.get("tp"))
            lines.append(
                f"            {p.get('symbol', '')}  {side}  {lot} lots"
                f"  @ {entry}  SL {sl}  TP {tp}"
            )

    lines.append(_DIVIDER)
    return "\n".join(lines)


def print_run_summary(result: dict, *, cycle: int | None = None) -> None:
    print(format_run_summary(result, cycle=cycle))
    print()


# ---------------------------------------------------------------------------
# Daily accumulator
# ---------------------------------------------------------------------------

def make_daily_accumulator() -> dict:
    """Return a fresh accumulator for one trading day."""
    return {
        "date": datetime.now(tz=UTC).strftime("%Y-%m-%d"),
        "cycles": 0,
        "symbols_evaluated": 0,
        "executions": 0,
        "rejection_counter": Counter(),
        "orders_filled": [],
        "opening_balance": None,
        "opening_equity": None,
        "last_balance": None,
        "last_equity": None,
        "last_positions": [],
        "trade_outcomes": [],  # populated at end of day via match_outcomes()
    }


def accumulate_cycle(acc: dict, result: dict) -> None:
    """Update acc with data from one run_scan() result."""
    acc["cycles"] += 1

    rows: list[dict] = [
        r for r in (result.get("symbol_results") or [])
        if isinstance(r, dict)
    ]
    acc["symbols_evaluated"] += len(rows)

    for row in rows:
        dec = str(row.get("decision", ""))
        if dec == "EXECUTE":
            acc["executions"] += 1
        fc = row.get("failure_code") or row.get("skipped_reason") or ""
        if fc and dec != "EXECUTE":
            acc["rejection_counter"][str(fc)] += 1

        if row.get("order_status") == "FILLED":
            acc["orders_filled"].append(row)

    balance = result.get("account_balance")
    equity = result.get("account_equity")
    if balance is not None:
        if acc["opening_balance"] is None:
            acc["opening_balance"] = balance
            acc["opening_equity"] = equity
        acc["last_balance"] = balance
    if equity is not None:
        acc["last_equity"] = equity

    positions = result.get("live_positions")
    if positions is not None:
        acc["last_positions"] = positions


# ---------------------------------------------------------------------------
# Daily summary formatter
# ---------------------------------------------------------------------------

def format_daily_summary(acc: dict) -> str:
    lines: list[str] = []
    date_label = acc.get("date", "")
    cycles = acc.get("cycles", 0)

    lines.append(_DIVIDER)
    lines.append(f"DEVI 4.0 — Daily Summary  |  {date_label}")
    lines.append(_DIVIDER)

    # Account + P&L
    opening = acc.get("opening_balance")
    closing = acc.get("last_balance")
    equity = acc.get("last_equity")
    pnl_str = _fmt_pnl(opening, closing)
    unrealised = ""
    if closing is not None and equity is not None:
        try:
            u = float(equity) - float(closing)
            sign = "+" if u >= 0 else ""
            unrealised = f"  Unrealised: {sign}${u:,.2f}"
        except (TypeError, ValueError):
            pass

    lines.append(
        f"Account   Balance: {_fmt_balance(closing)}"
        f"  ({pnl_str})"
        f"{unrealised}"
    )
    if opening is not None:
        lines.append(f"          Opening: {_fmt_balance(opening)}  Equity: {_fmt_balance(equity)}")

    # Session stats
    lines.append(
        f"Session   {cycles} cycles"
        f"  |  {acc['symbols_evaluated']} evaluations"
        f"  |  {acc['executions']} executed"
    )

    rejection_counter: Counter = acc.get("rejection_counter", Counter())
    if rejection_counter:
        lines.append("          Top rejections:")
        for code, count in rejection_counter.most_common(6):
            lines.append(f"            {code:<42} {count}")

    # Trades — use outcomes if available, fall back to raw filled orders
    trades = acc.get("trade_outcomes") or acc.get("orders_filled", [])
    if trades:
        lines.append(f"Trades    {len(trades)} filled:")
        for r in trades:
            side = str(r.get("side", "")).replace("BULLISH", "BUY").replace("BEARISH", "SELL")
            lot = _fmt_lot(r.get("lot_size"))
            entry = _fmt_price(r.get("entry_price"))
            sl = _fmt_price(r.get("stop_loss"))
            tp = _fmt_price(r.get("take_profit"))
            rr = r.get("rr")
            rr_str = f"  RR {rr:.2f}" if rr else ""
            sc = r.get("setup_class", "")
            tier = r.get("confidence_tier", "")
            tag = f"  [{sc}/{tier}]" if sc or tier else ""
            ts = r.get("timestamp", "")
            time_str = f"  {ts[11:16]}" if ts else ""
            # Outcome label
            outcome = r.get("outcome", "")
            profit = r.get("profit")
            outcome_str = ""
            if outcome:
                outcome_str = f"  → {outcome}"
                if profit is not None:
                    sign = "+" if profit >= 0 else ""
                    outcome_str += f"  ${sign}{profit:,.2f}"
            lines.append(
                f"            {r.get('symbol', '')}  {side}  {lot} lots"
                f"  @ {entry}  SL {sl}  TP {tp}{rr_str}{tag}{time_str}{outcome_str}"
            )
        # Outcome tally if outcomes available
        if acc.get("trade_outcomes"):
            tp_hits = sum(1 for t in trades if t.get("outcome") == "TP_HIT")
            sl_hits = sum(1 for t in trades if t.get("outcome") == "SL_HIT")
            sess_closes = sum(1 for t in trades if t.get("outcome") == "SESSION_CLOSE")
            running = sum(1 for t in trades if t.get("outcome") == "RUNNING")
            closed = len(trades) - running
            if closed > 0:
                tally = f"TP: {tp_hits}  SL: {sl_hits}  Session: {sess_closes}"
                if running:
                    tally += f"  |  {running} running"
                lines.append(f"          {tally}")
    else:
        lines.append("Trades    None today")

    # Positions open at end of day
    positions = acc.get("last_positions", [])
    if positions:
        lines.append(f"Positions {len(positions)} still open at day end:")
        for p in positions:
            side = str(p.get("side", "")).replace("BULLISH", "BUY").replace("BEARISH", "SELL")
            lot = _fmt_lot(p.get("lot_size"))
            entry = _fmt_price(p.get("open_price"))
            sl = _fmt_price(p.get("sl"))
            tp = _fmt_price(p.get("tp"))
            lines.append(
                f"            {p.get('symbol', '')}  {side}  {lot} lots"
                f"  @ {entry}  SL {sl}  TP {tp}"
            )
    else:
        lines.append("Positions None open at day end")

    lines.append(_DIVIDER)
    return "\n".join(lines)


def print_daily_summary(acc: dict) -> None:
    print(format_daily_summary(acc))
    print()
