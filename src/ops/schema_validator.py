from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reasons: list[str]


REQUIRED_DECISION_FIELDS = (
    "run_id",
    "scan_id",
    "decision_id",
    "timestamp",
    "symbol",
    "session",
    "execution_side",
    "stage_entered",
    "stage_failed",
    "failure_code",
    "failure_detail",
    "final_decision",
    "final_decision_reason",
    "config_hash",
    "snapshot_id",
    "tp_debug",
    "record_valid",
    "record_invalid_reasons",
)


TP_DEBUG_REQUIRED = ("schema_version", "found", "rejected", "selected")


def validate_decision_record(record: dict[str, Any]) -> ValidationResult:
    reasons: list[str] = []

    for field in REQUIRED_DECISION_FIELDS:
        if field not in record:
            reasons.append(f"missing_field:{field}")

    tp_debug = record.get("tp_debug")
    if not isinstance(tp_debug, dict):
        reasons.append("invalid_tp_debug_type")
    else:
        for field in TP_DEBUG_REQUIRED:
            if field not in tp_debug:
                reasons.append(f"missing_tp_debug_field:{field}")

        if not isinstance(tp_debug.get("found", []), list):
            reasons.append("tp_debug_found_not_list")

        if not isinstance(tp_debug.get("rejected", []), list):
            reasons.append("tp_debug_rejected_not_list")

        if not isinstance(tp_debug.get("selected", {}), dict):
            reasons.append("tp_debug_selected_not_dict")

    return ValidationResult(valid=not reasons, reasons=reasons)


def no_candidates_found(record: dict[str, Any]) -> bool:
    tp_debug = record.get("tp_debug")
    if not isinstance(tp_debug, dict):
        return True

    found = tp_debug.get("found")
    selected = tp_debug.get("selected")

    if not isinstance(found, list) or not isinstance(selected, dict):
        return True

    return len(found) == 0 and len(selected) == 0
