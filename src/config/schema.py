from __future__ import annotations

from dataclasses import dataclass
from typing import Any


REQUIRED_PATHS = (
    "detection.atr_period",
    "trend.ema_periods",
    "trend.slope_lookback",
    "trend.slope_threshold_atr_mult",
    "regime.trending_threshold",
    "regime.expanding_threshold",
    "confluence.block_ranging_regime",
    "confluence.tier_c_tradable",
    "exits.min_rr",
    "exits.min_rr_neutral",
    "exits.rr_fallback_enabled",
    "exits.require_structural_tp_in_neutral",
    "risk.risk_per_trade_pct",
    "gates.allowed_setups",
    "execution.auto_execute_live",
    "execution.live_confirmed",
    "execution.max_orders_per_run",
    "execution.kill_switch_enabled",
    "runtime.namespace",
    "runtime.mode",
)


FROZEN_CONSTRAINTS: dict[str, Any] = {
    "exits.rr_fallback_enabled": False,
    "gates.allowed_setups": ["OB_WITH_BOS", "OB_WITH_ENGULFING"],
    "risk.risk_per_trade_pct": 0.4,
    "confluence.block_ranging_regime": True,
    "exits.require_structural_tp_in_neutral": True,
}


@dataclass(frozen=True)
class ConfigValidationResult:
    valid: bool
    errors: list[str]


def _resolve_path(payload: dict[str, Any], dotted_path: str) -> Any:
    current: Any = payload
    for key in dotted_path.split("."):
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _validate_auto_execute_live(payload: dict[str, Any], errors: list[str]) -> None:
    auto_execute_live = _resolve_path(payload, "execution.auto_execute_live")
    runtime_mode = _resolve_path(payload, "runtime.mode")
    runtime_namespace = _resolve_path(payload, "runtime.namespace")

    is_prod_live_profile = runtime_mode == "live" and runtime_namespace == "prod"
    if auto_execute_live is not True:
        return

    if not is_prod_live_profile:
        errors.append(
            "unsafe_default:auto_execute_live_must_default_false_outside_prod_live"
        )
        return

    required_for_auto_live: dict[str, Any] = {
        "runtime.mode": "live",
        "runtime.namespace": "prod",
        "execution.arming_required": True,
        "execution.live_confirmed": True,
        "execution.max_orders_per_run": 1,
        "execution.symbol_whitelist": ["EURUSD"],
        "risk.fixed_lot_size": 0.01,
        "execution.debug_bypass": False,
        "execution.allow_overrides": False,
        "execution.retry_policy": "no_retry",
    }
    for path, expected in required_for_auto_live.items():
        actual = _resolve_path(payload, path)
        if actual != expected:
            errors.append(
                f"unsafe_live_auto_execute_violation:{path}:expected={expected}:actual={actual}"
            )


def validate_config_dict(payload: dict[str, Any]) -> ConfigValidationResult:
    errors: list[str] = []

    for path in REQUIRED_PATHS:
        if _resolve_path(payload, path) is None:
            errors.append(f"missing_required:{path}")

    for path, expected in FROZEN_CONSTRAINTS.items():
        actual = _resolve_path(payload, path)
        if actual != expected:
            errors.append(f"frozen_constraint_violation:{path}:expected={expected}:actual={actual}")

    _validate_auto_execute_live(payload, errors)

    return ConfigValidationResult(valid=not errors, errors=errors)
