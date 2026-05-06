from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid5, NAMESPACE_DNS

from src.config.loader import config_hash
from src.core.models import ConfluenceResult, ContextSnapshot, ExitPlan, RiskVerdict, TradeIntent


@dataclass(frozen=True)
class PositionManagementVerdict:
    approved: bool
    reason: str
    trade_intent: TradeIntent | None



def _trade_id(*, symbol: str, bar_time_iso: str, setup_class: str, direction: str) -> str:
    seed = f"devi4|{symbol}|{bar_time_iso}|{setup_class}|{direction}"
    return str(uuid5(NAMESPACE_DNS, seed))



def build_trade_intent(
    *,
    context: ContextSnapshot,
    confluence: ConfluenceResult,
    exit_plan: ExitPlan,
    risk_verdict: RiskVerdict,
    entry_price: float,
    config: dict[str, Any],
) -> PositionManagementVerdict:
    """Build a TradeIntent from approved confluence, exit plan, and risk verdict.

    This is a TradeIntent builder, NOT full position management.
    It does NOT manage: full lifecycle, trailing, breakeven, external closures, or broker sync.
    """
    if not risk_verdict.approved:
        return PositionManagementVerdict(approved=False, reason="risk_not_approved", trade_intent=None)

    if risk_verdict.lot_size <= 0:
        return PositionManagementVerdict(approved=False, reason="invalid_lot_size", trade_intent=None)

    management_cfg = config.get("exits", {}).get("management", {})
    if bool(management_cfg.get("partials_enabled", False)):
        return PositionManagementVerdict(approved=False, reason="partials_not_supported_phase1", trade_intent=None)

    intent = TradeIntent(
        trade_id=_trade_id(
            symbol=context.symbol,
            bar_time_iso=context.bar_time.isoformat(),
            setup_class=confluence.setup_class.value,
            direction=confluence.direction.value,
        ),
        symbol=context.symbol,
        direction=confluence.direction,
        setup_class=confluence.setup_class,
        confidence_tier=confluence.confidence_tier,
        session=context.session,
        entry_price=float(entry_price),
        exit_plan=exit_plan,
        risk_verdict=risk_verdict,
        confluence=confluence,
        context=context,
        config_hash=config_hash(config),
        bar_time=context.bar_time,
    )

    return PositionManagementVerdict(approved=True, reason="approved", trade_intent=intent)
