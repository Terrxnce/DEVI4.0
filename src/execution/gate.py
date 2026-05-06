from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExecutionVerdict:
    approved: bool
    reason: str



def evaluate_execution(config: dict[str, Any]) -> ExecutionVerdict:
    runtime_cfg = config.get("runtime", {})
    execution_cfg = config.get("execution", {})

    runtime_mode = str(runtime_cfg.get("mode", "paper")).lower()
    execution_mode = str(execution_cfg.get("mode", "MARKET")).upper()

    if execution_mode != "MARKET":
        return ExecutionVerdict(approved=False, reason="unsupported_execution_mode")

    if int(execution_cfg.get("max_orders_per_run", 0)) < 1:
        return ExecutionVerdict(approved=False, reason="max_orders_per_run_invalid")

    if runtime_mode == "live":
        return ExecutionVerdict(approved=False, reason="live_execution_not_allowed_phase1")

    if runtime_mode not in {"paper", "backtest", "shadow"}:
        return ExecutionVerdict(approved=False, reason="invalid_runtime_mode")

    return ExecutionVerdict(approved=True, reason="approved")
