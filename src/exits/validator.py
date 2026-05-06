from __future__ import annotations

from src.core.enums import Direction, Regime
from src.core.models import ContextSnapshot, ExitPlan



def validate_exit_plan(
    entry_price: float,
    direction: Direction,
    plan: ExitPlan,
    context: ContextSnapshot,
    min_rr: float,
    min_rr_neutral: float,
) -> tuple[bool, str]:
    if direction == Direction.BULLISH:
        if plan.stop_loss >= entry_price:
            return (False, "invalid_sl_not_behind_entry")
        if plan.take_profit <= entry_price:
            return (False, "invalid_tp_not_beyond_entry")
    elif direction == Direction.BEARISH:
        if plan.stop_loss <= entry_price:
            return (False, "invalid_sl_not_behind_entry")
        if plan.take_profit >= entry_price:
            return (False, "invalid_tp_not_beyond_entry")

    floor = min_rr_neutral if context.regime == Regime.NEUTRAL else min_rr
    if plan.risk_reward < floor:
        if context.regime == Regime.NEUTRAL:
            return (False, "neutral_rr_below_floor")
        return (False, "rr_below_floor")

    opposing_direction = Direction.BEARISH if direction == Direction.BULLISH else Direction.BULLISH
    for structure in context.nearby_structures:
        if structure.direction != opposing_direction:
            continue
        low = min(structure.price_low, structure.price_high)
        high = max(structure.price_low, structure.price_high)
        if low <= plan.take_profit <= high:
            return (False, "tp_inside_opposing_structure")

    return (True, "ok")
