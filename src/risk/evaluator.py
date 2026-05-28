from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Any

from src.core.models import ContextSnapshot, RiskVerdict


@dataclass(frozen=True)
class RiskState:
    daily_pnl_pct: float = 0.0
    total_pnl_pct: float = 0.0
    open_positions_total: int = 0
    open_positions_symbol: int = 0
    new_trades_session: int = 0
    correlated_positions: int = 0
    same_direction_correlated_positions: int = 0
    usd_correlated_positions: int = 0
    jpy_correlated_positions: int = 0
    account_balance: float = 10000.0




def _coerce_state(state: dict[str, Any] | None) -> RiskState:
    if not state:
        return RiskState()
    return RiskState(
        daily_pnl_pct=float(state.get("daily_pnl_pct", 0.0)),
        total_pnl_pct=float(state.get("total_pnl_pct", 0.0)),
        open_positions_total=int(state.get("open_positions_total", 0)),
        open_positions_symbol=int(state.get("open_positions_symbol", 0)),
        new_trades_session=int(state.get("new_trades_session", 0)),
        correlated_positions=int(state.get("correlated_positions", 0)),
        same_direction_correlated_positions=int(state.get("same_direction_correlated_positions", 0)),
        usd_correlated_positions=int(state.get("usd_correlated_positions", 0)),
        jpy_correlated_positions=int(state.get("jpy_correlated_positions", 0)),
        account_balance=float(state.get("account_balance", 10000.0)),
    )



def _calculate_lot_size(
    *,
    balance: float,
    risk_pct: float,
    entry_price: float,
    stop_loss: float,
    point: float,
    tick_size: float,
    tick_value: float,
    lot_step: float,
    min_lot: float,
    max_lot: float,
) -> float:
    """Calculate lot size from risk parameters.

    Uses MT5 trade_tick_value (already in account currency) to derive the
    per-point value in account currency, which works correctly for all pairs
    including JPY crosses where the quote currency is not the account currency.

    Formula:
        point_value = tick_value * (point / tick_size)
        loss_per_lot = sl_points * point_value
        lot_size = risk_amount / loss_per_lot
    """
    if entry_price <= 0 or stop_loss <= 0 or point <= 0 or tick_size <= 0 or tick_value <= 0:
        return 0.0

    risk_amount = balance * (risk_pct / 100.0)
    sl_distance = abs(entry_price - stop_loss)
    sl_points = sl_distance / point

    # tick_value is in account currency per tick per lot (from MT5 trade_tick_value).
    # Scaling by (point / tick_size) gives the value per point per lot.
    # For most FX pairs point == tick_size so the ratio is 1.0.
    point_value = tick_value * (point / tick_size)

    if point_value <= 0 or sl_points <= 0:
        return 0.0

    loss_per_lot = sl_points * point_value
    lot_size_raw = risk_amount / loss_per_lot

    # Round down to lot step (add epsilon to avoid floating-point undershoot)
    lot_size_stepped = floor((lot_size_raw / lot_step) + 1e-9) * lot_step

    # Clamp to min/max
    return max(min_lot, min(lot_size_stepped, max_lot))



def _risk_deviation(
    *,
    balance: float,
    risk_pct: float,
    entry_price: float,
    stop_loss: float,
    lot_size: float,
    point: float,
    tick_size: float,
    tick_value: float,
) -> float:
    """Return absolute deviation between intended and actual risk percentage."""
    risk_amount = balance * (risk_pct / 100.0)
    sl_distance = abs(entry_price - stop_loss)
    sl_points = sl_distance / point
    point_value = tick_value * (point / tick_size)
    loss_per_lot = sl_points * point_value
    actual_risk_amount = lot_size * loss_per_lot
    if risk_amount <= 0:
        return 0.0
    actual_risk_pct = (actual_risk_amount / balance) * 100.0
    return abs(actual_risk_pct - risk_pct) / risk_pct



def evaluate_risk(
    *,
    context: ContextSnapshot,
    config: dict[str, Any],
    entry_price: float,
    stop_loss: float,
    state: dict[str, Any] | None = None,
) -> RiskVerdict:
    risk_cfg = config["risk"]
    snapshot = _coerce_state(state)

    intended_risk = float(risk_cfg["risk_per_trade_pct"])

    # Drawdown gating (canonical failure codes)
    if snapshot.daily_pnl_pct <= float(risk_cfg["force_close_daily_pct"]):
        return RiskVerdict(False, 0.0, 0.0, intended_risk, "daily_drawdown_force_close")

    if snapshot.total_pnl_pct <= float(risk_cfg["force_close_total_pct"]):
        return RiskVerdict(False, 0.0, 0.0, intended_risk, "total_drawdown_force_close")

    if snapshot.daily_pnl_pct <= float(risk_cfg["block_new_trades_daily_pct"]):
        return RiskVerdict(False, 0.0, 0.0, intended_risk, "daily_drawdown_block")

    if snapshot.total_pnl_pct <= float(risk_cfg["block_new_trades_total_pct"]):
        return RiskVerdict(False, 0.0, 0.0, intended_risk, "total_drawdown_block")

    # Position/session limits
    if snapshot.open_positions_total >= int(risk_cfg["max_open_positions_total"]):
        return RiskVerdict(False, 0.0, 0.0, intended_risk, "max_open_positions_total")

    if snapshot.open_positions_symbol >= int(risk_cfg["max_open_positions_per_symbol"]):
        return RiskVerdict(False, 0.0, 0.0, intended_risk, "max_open_positions_per_symbol")

    if snapshot.new_trades_session >= int(risk_cfg["max_new_trades_per_session"]):
        return RiskVerdict(False, 0.0, 0.0, intended_risk, "max_new_trades_per_session")

    if snapshot.correlated_positions >= int(risk_cfg["max_correlated_positions"]):
        return RiskVerdict(False, 0.0, 0.0, intended_risk, "max_correlated_positions")

    if snapshot.same_direction_correlated_positions >= int(risk_cfg["same_direction_correlation_cap"]):
        return RiskVerdict(False, 0.0, 0.0, intended_risk, "same_direction_correlation_cap")

    # USD correlation cap: limits total open positions where USD is one leg.
    # Prevents over-concentration on USD-driven macro moves (e.g. NFP, FOMC).
    # Optional: only enforced when max_usd_correlated_positions key is present.
    usd_cap = risk_cfg.get("max_usd_correlated_positions")
    if usd_cap is not None and snapshot.usd_correlated_positions >= int(usd_cap):
        return RiskVerdict(False, 0.0, 0.0, intended_risk, "usd_correlation_cap")

    # JPY correlation cap: limits total open positions where JPY is one leg.
    # Prevents stacking USDJPY + CADJPY + EURJPY into the same BoJ/JPY macro move.
    # Optional: only enforced when max_jpy_correlated_positions key is present.
    jpy_cap = risk_cfg.get("max_jpy_correlated_positions")
    if jpy_cap is not None and snapshot.jpy_correlated_positions >= int(jpy_cap):
        return RiskVerdict(False, 0.0, 0.0, intended_risk, "jpy_correlation_cap")

    # Fixed lot mode - skip dynamic calculation, use configured lot directly.
    # Drawdown and position guards above still apply.
    if not bool(risk_cfg.get("dynamic_lot_sizing", True)):
        fixed_lot = float(risk_cfg.get("fixed_lot_size", 0.01))
        if fixed_lot <= 0:
            return RiskVerdict(False, 0.0, 0.0, intended_risk, "fixed_lot_invalid")
        instrument_cfg = config.get("instrument", {})
        min_lot = float(instrument_cfg.get("min_lot", 0.01))
        max_lot = float(instrument_cfg.get("max_lot", 100.0))
        if fixed_lot < min_lot or fixed_lot > max_lot:
            return RiskVerdict(False, 0.0, 0.0, intended_risk, "fixed_lot_out_of_bounds")
        return RiskVerdict(True, fixed_lot, intended_risk, intended_risk, "fixed_lot_approved")

    # Lot sizing with instrument profile from config
    instrument_cfg = config.get("instrument", {})
    point = float(instrument_cfg.get("point", 0.00001))
    tick_size = float(instrument_cfg.get("tick_size", point))
    tick_value = float(instrument_cfg.get("tick_value", 1.0))
    lot_step = float(instrument_cfg.get("lot_step", 0.01))
    min_lot = float(instrument_cfg.get("min_lot", 0.01))
    max_lot = float(instrument_cfg.get("max_lot", 100.0))

    # Soft reduction: halve risk if daily drawdown threshold hit
    effective_risk = intended_risk
    if snapshot.daily_pnl_pct <= float(risk_cfg["soft_daily_reduction_pct"]):
        effective_risk = round(intended_risk * 0.5, 4)

    lot_size = _calculate_lot_size(
        balance=snapshot.account_balance,
        risk_pct=effective_risk,
        entry_price=entry_price,
        stop_loss=stop_loss,
        point=point,
        tick_size=tick_size,
        tick_value=tick_value,
        lot_step=lot_step,
        min_lot=min_lot,
        max_lot=max_lot,
    )

    if lot_size <= 0:
        return RiskVerdict(False, 0.0, 0.0, intended_risk, "lot_size_calculation_failed")

    # Risk deviation check (must be <= 20%)
    deviation = _risk_deviation(
        balance=snapshot.account_balance,
        risk_pct=effective_risk,
        entry_price=entry_price,
        stop_loss=stop_loss,
        lot_size=lot_size,
        point=point,
        tick_size=tick_size,
        tick_value=tick_value,
    )
    if deviation > 0.20:
        return RiskVerdict(False, 0.0, 0.0, intended_risk, "risk_deviation_exceeded")

    actual_risk_pct = effective_risk
    if snapshot.daily_pnl_pct <= float(risk_cfg["soft_daily_reduction_pct"]):
        return RiskVerdict(True, lot_size, actual_risk_pct, intended_risk, "soft_reduction_active")

    _ = context.symbol
    return RiskVerdict(True, lot_size, actual_risk_pct, intended_risk, "approved")
