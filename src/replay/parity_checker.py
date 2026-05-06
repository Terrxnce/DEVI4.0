from __future__ import annotations

from dataclasses import dataclass
from typing import Any


TOLERANCES: dict[str, float] = {
    "planned_sl": 0.0,
    "planned_tp": 0.0,
    "planned_rr": 0.01,
    "detector_quality": 0.001,
    "effective_quality": 0.001,
}

EXACT_FIELDS = (
    "final_decision",
    "failure_code",
    "setup_class",
    "confidence_tier",
    "direction",
    "tp_source",
    "structural_count",
)

UNORDERED_LIST_FIELDS = ("hard_rejects", "soft_penalties")


@dataclass(frozen=True)
class ParityDiff:
    field: str
    expected: Any
    actual: Any
    ok: bool


@dataclass(frozen=True)
class ParityResult:
    pass_all: bool
    diffs: list[ParityDiff]


def _compare_numeric(expected: Any, actual: Any, tolerance: float) -> bool:
    try:
        return abs(float(expected) - float(actual)) <= tolerance
    except (TypeError, ValueError):
        return False


def compare_decisions(expected: dict[str, Any], actual: dict[str, Any], tick_size: float = 0.0) -> ParityResult:
    diffs: list[ParityDiff] = []

    effective_tolerances = dict(TOLERANCES)
    if tick_size > 0:
        effective_tolerances["planned_sl"] = tick_size
        effective_tolerances["planned_tp"] = tick_size

    for field in EXACT_FIELDS:
        e = expected.get(field)
        a = actual.get(field)
        ok = e == a
        diffs.append(ParityDiff(field=field, expected=e, actual=a, ok=ok))

    for field, tol in effective_tolerances.items():
        e = expected.get(field)
        a = actual.get(field)
        ok = _compare_numeric(e, a, tol)
        diffs.append(ParityDiff(field=field, expected=e, actual=a, ok=ok))

    for field in UNORDERED_LIST_FIELDS:
        e = sorted(expected.get(field, []))
        a = sorted(actual.get(field, []))
        ok = e == a
        diffs.append(ParityDiff(field=field, expected=e, actual=a, ok=ok))

    return ParityResult(pass_all=all(d.ok for d in diffs), diffs=diffs)
