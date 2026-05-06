from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.core.arming import ArmingService
from src.core.kill_switch import KillSwitch
from src.core.models import ContextSnapshot
from src.core.runtime_state import RuntimeState


@dataclass(frozen=True)
class SupervisorVerdict:
    approved: bool
    reason: str



def evaluate_supervisor(
    *,
    context: ContextSnapshot,
    config: dict[str, Any],
    runtime_state: RuntimeState | None = None,
    current_orders_this_run: int | None = None,
    kill_switch: KillSwitch | None = None,
    arming_service: ArmingService | None = None,
) -> SupervisorVerdict:
    execution_cfg = config.get("execution", {})
    runtime_cfg = config.get("runtime", {})
    mode = str(runtime_cfg.get("mode", "paper")).lower()
    max_orders = int(execution_cfg.get("max_orders_per_run", 0))

    # 1. Kill switch check (highest priority, live mode only)
    if mode == "live" and kill_switch is not None:
        ks_verdict = kill_switch.evaluate(
            config_kill_switch_enabled=execution_cfg.get("kill_switch_enabled", False),
        )
        if ks_verdict.triggered:
            return SupervisorVerdict(approved=False, reason=f"kill_switch_active:{ks_verdict.reason}")

    if max_orders < 1:
        return SupervisorVerdict(approved=False, reason="max_orders_per_run_invalid")

    # 2. Runtime order count
    if runtime_state is not None:
        orders_count = runtime_state.orders_this_run
    elif current_orders_this_run is not None:
        orders_count = current_orders_this_run
    else:
        orders_count = 0

    if orders_count >= max_orders:
        return SupervisorVerdict(approved=False, reason="max_orders_per_run_exceeded")

    # 3. Live mode gates
    if mode == "live":
        if not bool(execution_cfg.get("live_confirmed", False)):
            return SupervisorVerdict(approved=False, reason="live_not_confirmed_in_config")

        if arming_service is None or not arming_service.is_armed:
            return SupervisorVerdict(approved=False, reason="live_not_armed")

        token = arming_service.get_valid_token()
        if token is None:
            return SupervisorVerdict(approved=False, reason="arming_token_invalid")

        if context.symbol not in token.symbols:
            return SupervisorVerdict(approved=False, reason="symbol_not_authorized_for_live")

    _ = context.session
    return SupervisorVerdict(approved=True, reason="approved")
