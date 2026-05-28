"""Pre-trade rechecks — run immediately before any execution attempt.

These checks re-validate conditions that may have changed between decision
time and execution time. All checks must pass before order_send is called.

Safety:
  - Paper mode does not use these rechecks (paper fills are synthetic).
  - Live mode requires arming + kill switch clear BEFORE these checks.
  - Each check returns a canonical failure code for audit logging.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.core.models import TradeIntent


@dataclass(frozen=True)
class RecheckVerdict:
    passed: bool
    reason: str = ""  # canonical failure code if passed is False


def is_market_open_from_snapshot(
    *,
    bid: float,
    ask: float,
    tradable: bool,
    session_deals: Any | None,
) -> bool:
    """Unified market-open decision used by CLI account-check and rechecks.

    Rules:
      - Requires valid tick: bid > 0, ask > 0, ask >= bid
      - Requires symbol tradable
      - If session_deals is explicitly False, reject
      - If session_deals is missing/None, rely on tick validity + tradable
    """
    tick_valid = bid > 0.0 and ask > 0.0 and ask >= bid
    if not tick_valid or not tradable:
        return False
    if session_deals is False:
        return False
    return True


def market_open_diagnostics_from_snapshot(
    *,
    bid: float,
    ask: float,
    trade_allowed: bool,
    trade_mode: int,
    trade_mode_full: int,
    session_deals: Any | None,
    session_deals_present: bool,
) -> dict[str, Any]:
    ask_ge_bid = ask >= bid
    tick_valid = bid > 0.0 and ask > 0.0 and ask_ge_bid
    tradable = bool(trade_allowed) and int(trade_mode) == int(trade_mode_full)

    if not session_deals_present:
        session_deals_source = "missing"
    elif session_deals is None:
        session_deals_source = "none"
    else:
        session_deals_source = "present"

    session_deals_explicit_false = isinstance(session_deals, bool) and session_deals is False
    session_deals_allows = not session_deals_explicit_false
    market_open = tick_valid and tradable and session_deals_allows
    return {
        "bid": float(bid),
        "ask": float(ask),
        "ask_ge_bid": bool(ask_ge_bid),
        "tick_valid": bool(tick_valid),
        "trade_allowed": bool(trade_allowed),
        "trade_mode": int(trade_mode),
        "trade_mode_full": int(trade_mode_full),
        "tradable": bool(tradable),
        "session_deals": session_deals,
        "session_deals_source": session_deals_source,
        "session_deals_explicit_false": bool(session_deals_explicit_false),
        "session_deals_allows": bool(session_deals_allows),
        "market_open": bool(market_open),
    }


def _format_market_diagnostics(diagnostics: dict[str, Any]) -> str:
    ordered_keys = [
        "bid",
        "ask",
        "ask_ge_bid",
        "tick_valid",
        "trade_allowed",
        "trade_mode",
        "trade_mode_full",
        "tradable",
        "session_deals",
        "session_deals_source",
        "session_deals_explicit_false",
        "session_deals_allows",
        "market_open",
    ]
    return ";".join(f"{key}={diagnostics.get(key)}" for key in ordered_keys)


class PreTradeRecheck:
    """Runs all pre-trade validation checks before execution.

    Usage:
        recheck = PreTradeRecheck(data_source=mt5_data_source)
        verdict = recheck.run_all(intent, decision_spread=0.0002)
        if not verdict.passed:
            log.warning(f"Pre-trade recheck failed: {verdict.reason}")
            return  # do not execute
    """

    def __init__(self, *, data_source: Any | None = None) -> None:
        self._data = data_source

    def _symbol_trade_mode_full(self) -> int:
        mt5_client = getattr(self._data, "mt5_client", None)
        if mt5_client is None:
            return 4
        return int(getattr(mt5_client, "SYMBOL_TRADE_MODE_FULL", 4))

    # ------------------------------------------------------------------
    # Individual checks (public for testing / selective use)
    # ------------------------------------------------------------------

    def recheck_spread(
        self,
        intent: TradeIntent,
        *,
        decision_spread: float,
        max_widening_factor: float = 2.0,
        spread_max_price: float | None = None,
    ) -> RecheckVerdict:
        """Verify current spread has not widened beyond tolerance.

        Failure codes:
            spread_widened      – current > decision * max_widening_factor
            spread_zero         – current spread is zero (suspicious)
            spread_cap_exceeded – current spread > absolute cap (spread_max_price)
        """
        if self._data is None:
            return RecheckVerdict(passed=False, reason="recheck_no_data_source")

        tick = self._data.fetch_tick(intent.symbol)
        current_spread = abs(tick["ask"] - tick["bid"])

        if current_spread == 0:
            return RecheckVerdict(passed=False, reason="spread_zero")

        if spread_max_price is not None and current_spread > spread_max_price:
            return RecheckVerdict(
                passed=False,
                reason=f"spread_cap_exceeded:{current_spread:.5f}>{spread_max_price:.5f}",
            )

        if current_spread > decision_spread * max_widening_factor:
            return RecheckVerdict(
                passed=False,
                reason=f"spread_widened:{decision_spread:.5f}->{current_spread:.5f}",
            )

        return RecheckVerdict(passed=True, reason="")

    def recheck_account(
        self,
        intent: TradeIntent,
        *,
        equity_min_factor: float = 0.50,
    ) -> RecheckVerdict:
        """Verify account balance and equity are sufficient.

        Failure codes:
            account_balance_zero   – balance <= 0
            equity_below_threshold   – equity < balance * equity_min_factor
            insufficient_margin      – free margin < rough margin required
        """
        if self._data is None:
            return RecheckVerdict(passed=False, reason="recheck_no_data_source")

        account = self._data.fetch_account_info()
        balance = account.get("balance", 0.0)
        equity = account.get("equity", 0.0)
        free_margin = account.get("free_margin", 0.0)

        if balance <= 0:
            return RecheckVerdict(passed=False, reason="account_balance_zero")

        if equity < balance * equity_min_factor:
            return RecheckVerdict(
                passed=False,
                reason=f"equity_below_threshold:{equity:.2f}<{balance * equity_min_factor:.2f}",
            )

        # Margin estimate — use MT5 order_calc_margin when available (accurate,
        # handles all currency pairs and account currencies correctly).
        # Fall back to a conservative per-lot estimate ($1,000/lot at ~100:1
        # leverage) that does NOT use entry_price, avoiding the JPY-cross bug
        # where a 115-handle JPY price was being multiplied as if it were USD.
        lot_size = intent.risk_verdict.lot_size
        mt5_client = getattr(self._data, "mt5_client", None)
        order_calc_margin = getattr(mt5_client, "order_calc_margin", None) if mt5_client else None
        margin_required = 0.0
        if callable(order_calc_margin):
            try:
                import MetaTrader5 as _mt5  # type: ignore
                action = _mt5.ORDER_TYPE_BUY if intent.direction.value == "BULLISH" else _mt5.ORDER_TYPE_SELL
                calc = order_calc_margin(action, intent.symbol, lot_size, intent.entry_price)
                margin_required = float(calc) if calc is not None else 0.0
            except Exception:
                margin_required = lot_size * 1000.0
        else:
            # Safe fallback: ~$1,000 per standard lot at 100:1 leverage.
            # Conservative enough to catch genuine margin shortfalls without
            # false-positives from currency conversion errors.
            margin_required = lot_size * 1000.0
        if margin_required > 0 and free_margin < margin_required:
            return RecheckVerdict(
                passed=False,
                reason=f"insufficient_margin:{free_margin:.2f}<{margin_required:.2f}",
            )

        return RecheckVerdict(passed=True, reason="")

    def recheck_risk(
        self,
        intent: TradeIntent,
        *,
        max_lot_deviation_pct: float = 20.0,
        dynamic_lot_sizing: bool = True,
        fixed_lot_size: float | None = None,
        risk_pct: float = 0.01,
    ) -> RecheckVerdict:
        """Re-run lot sizing and verify deviation from intent is small.

        Failure code:
            lot_size_deviation  – recalculated lot differs by > max_lot_deviation_pct

        Fixed-lot mode failure codes:
            fixed_lot_mismatch      – intent lot != fixed lot setting
            fixed_lot_out_of_bounds – fixed lot outside broker min/max
            fixed_lot_step_mismatch – fixed lot not aligned to broker lot step
        """
        if self._data is None:
            return RecheckVerdict(passed=False, reason="recheck_no_data_source")

        profile = self._data.fetch_instrument_profile(intent.symbol)

        # Pull profile fields (supports dict or object)
        def _get(key: str):
            if isinstance(profile, dict):
                return profile.get(key, 0.0)
            return getattr(profile, key, 0.0)

        point = _get("point") or 1e-05
        lot_step = _get("lot_step") or 0.01
        min_lot = _get("min_lot") or 0.01
        max_lot = _get("max_lot") or 100.0
        intended_lot = float(intent.risk_verdict.lot_size)

        if not dynamic_lot_sizing:
            fixed_lot = float(fixed_lot_size) if fixed_lot_size is not None else intended_lot

            if abs(intended_lot - fixed_lot) > 1e-9:
                return RecheckVerdict(
                    passed=False,
                    reason=f"fixed_lot_mismatch:{intended_lot:.2f}!={fixed_lot:.2f}",
                )

            if fixed_lot < min_lot or fixed_lot > max_lot:
                return RecheckVerdict(
                    passed=False,
                    reason=f"fixed_lot_out_of_bounds:{fixed_lot:.2f} not in [{min_lot:.2f},{max_lot:.2f}]",
                )

            aligned_lot = round(fixed_lot / lot_step) * lot_step
            if abs(aligned_lot - fixed_lot) > 1e-9:
                return RecheckVerdict(
                    passed=False,
                    reason=f"fixed_lot_step_mismatch:{fixed_lot:.2f} step={lot_step:.2f}",
                )

            return RecheckVerdict(passed=True, reason="")

        account = self._data.fetch_account_info()
        current_balance = account.get("balance", 0.0)
        contract_size = _get("contract_size") or 100000.0

        # Re-derive lot using the configured risk %, same formula as the risk module.
        # Caller must pass the actual risk_pct from config to avoid spurious deviation failures.
        risk_amount = current_balance * risk_pct
        sl_distance = abs(intent.entry_price - intent.exit_plan.stop_loss)
        if sl_distance <= 0:
            return RecheckVerdict(passed=False, reason="lot_size_deviation:sl_zero")

        # Use actual tick_value from instrument profile (in account currency per tick per lot).
        # This matches the risk evaluator's formula and avoids the systematic deviation
        # on non-USD-quote pairs (e.g. GBPCHF tick_value ~1.275 USD, not 1.0).
        # tick_value = point * contract_size was wrong for any pair where quote != USD.
        tick_value_from_profile = _get("tick_value") or 1.0
        tick_size_profile = _get("tick_size") or point
        tick_value = tick_value_from_profile * (point / tick_size_profile)
        ticks_at_risk = sl_distance / point
        raw_lot = risk_amount / (ticks_at_risk * tick_value)
        recalculated_lot = max(min_lot, min(max_lot, round(raw_lot / lot_step) * lot_step))

        if intended_lot <= 0:
            return RecheckVerdict(passed=False, reason="lot_size_deviation:intent_zero")

        deviation = abs(recalculated_lot - intended_lot) / intended_lot * 100.0
        if deviation > max_lot_deviation_pct:
            return RecheckVerdict(
                passed=False,
                reason=f"lot_size_deviation:{deviation:.1f}%>{max_lot_deviation_pct:.1f}%",
            )

        return RecheckVerdict(passed=True, reason="")

    def recheck_symbol(self, intent: TradeIntent) -> RecheckVerdict:
        """Verify symbol is tradable on the broker.

        Failure codes:
            symbol_trade_disabled     – trade_allowed is False
            symbol_trade_mode_invalid – trade_mode != full access mode
        """
        if self._data is None:
            return RecheckVerdict(passed=False, reason="recheck_no_data_source")

        info = self._data.fetch_symbol_info(intent.symbol)

        if not info.get("trade_allowed", False):
            return RecheckVerdict(passed=False, reason="symbol_trade_disabled")

        full_mode = self._symbol_trade_mode_full()
        trade_mode = int(info.get("trade_mode", -1))
        if trade_mode != full_mode:
            return RecheckVerdict(
                passed=False,
                reason=f"symbol_trade_mode_invalid:{trade_mode}",
            )

        return RecheckVerdict(passed=True, reason="")

    def recheck_market(self, intent: TradeIntent) -> RecheckVerdict:
        """Verify market is open for the symbol.

        Failure code:
            market_closed – no valid tick or session_deals is False
        """
        if self._data is None:
            return RecheckVerdict(passed=False, reason="recheck_no_data_source")

        info = self._data.fetch_symbol_info(intent.symbol)
        tick = self._data.fetch_tick(intent.symbol)

        bid = float(tick.get("bid", 0.0))
        ask = float(tick.get("ask", 0.0))
        trade_allowed = bool(info.get("trade_allowed", False))
        trade_mode = int(info.get("trade_mode", -1))
        trade_mode_full = self._symbol_trade_mode_full()
        session_deals_present = "session_deals" in info
        session_deals = info.get("session_deals")

        diagnostics = market_open_diagnostics_from_snapshot(
            bid=bid,
            ask=ask,
            trade_allowed=trade_allowed,
            trade_mode=trade_mode,
            trade_mode_full=trade_mode_full,
            session_deals=session_deals,
            session_deals_present=session_deals_present,
        )

        if not diagnostics["market_open"]:
            return RecheckVerdict(
                passed=False,
                reason=f"market_closed:{_format_market_diagnostics(diagnostics)}",
            )

        return RecheckVerdict(passed=True, reason="")

    # ------------------------------------------------------------------
    # Orchestrator
    # ------------------------------------------------------------------

    def run_all(
        self,
        intent: TradeIntent,
        *,
        decision_spread: float,
        dynamic_lot_sizing: bool = True,
        fixed_lot_size: float | None = None,
        risk_pct: float = 0.01,
        spread_max_price: float | None = None,
    ) -> RecheckVerdict:
        """Run all 5 rechecks in order. Return first failure or pass."""
        checks = [
            ("spread", lambda: self.recheck_spread(intent, decision_spread=decision_spread, spread_max_price=spread_max_price)),
            ("account", lambda: self.recheck_account(intent)),
            (
                "risk",
                lambda: self.recheck_risk(
                    intent,
                    dynamic_lot_sizing=dynamic_lot_sizing,
                    fixed_lot_size=fixed_lot_size,
                    risk_pct=risk_pct,
                ),
            ),
            ("symbol", lambda: self.recheck_symbol(intent)),
            ("market", lambda: self.recheck_market(intent)),
        ]

        for check_name, check_fn in checks:
            verdict = check_fn()
            if not verdict.passed:
                return RecheckVerdict(
                    passed=False,
                    reason=f"{check_name}:{verdict.reason}",
                )

        return RecheckVerdict(passed=True, reason="")
