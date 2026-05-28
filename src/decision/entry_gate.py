"""Entry proximity gate: validates that current price is within or near the primary zone.

For OB-based setups (OB_WITH_BOS, OB_WITH_FVG, OB_WITH_ENGULFING, SWEEP_WITH_OB),
requires the entry price to be within the OB zone (with ATR-scaled tolerance).
This ensures D.E.V.I only enters on a genuine retest of the zone, not after
price has already run through it and moved to extension.

For REJECTION_WITH_FVG and any non-OB setup: no proximity gate is applied.

Config keys (under "entry_gate" section):
    proximity_gate_enabled  bool   Enable/disable the check. Default: True.
    proximity_atr_mult      float  ATR multiple added as tolerance on each side.
                                   0.5 = half an ATR wiggle room. Default: 0.5.
"""

from __future__ import annotations

from src.core.enums import StructureType
from src.core.models import ConfluenceResult, DetectedStructure


_DEFAULT_PROXIMITY_ATR_MULT: float = 0.5


def _find_ob(confluence: ConfluenceResult) -> DetectedStructure | None:
    """Find the Order Block structure to use for the proximity check.

    OB as primary trigger: returned directly (OB_WITH_BOS, OB_WITH_FVG,
    OB_WITH_ENGULFING).

    OB as confirmation: returned from structural_confirmations (SWEEP_WITH_OB).

    Returns None if no OB is involved in the setup (e.g. REJECTION_WITH_FVG).
    """
    if confluence.primary_trigger.structure_type == StructureType.ORDER_BLOCK:
        return confluence.primary_trigger
    for s in confluence.structural_confirmations:
        if s.structure_type == StructureType.ORDER_BLOCK:
            return s
    return None


def evaluate_entry_proximity(
    confluence: ConfluenceResult,
    entry_price: float,
    atr: float,
    proximity_atr_mult: float = _DEFAULT_PROXIMITY_ATR_MULT,
) -> tuple[bool, str]:
    """Check if entry_price is within the OB zone (plus ATR tolerance).

    Returns (passes, failure_code).

    passes=True, failure_code=""
        Entry is acceptable: price is within zone or no OB gate applies.

    passes=False, failure_code="entry_outside_ob_zone"
        Entry price is outside [ob.price_low - tolerance, ob.price_high + tolerance].
        This means price has moved away from the zone — the retest is stale.

    Args:
        confluence:         Passing confluence result with primary_trigger set.
        entry_price:        Current bid or ask at decision time.
        atr:                Current M15 ATR.
        proximity_atr_mult: Tolerance on each side of the zone in ATR units.
    """
    ob = _find_ob(confluence)
    if ob is None:
        # No OB in this setup — proximity gate does not apply.
        return True, ""

    tolerance = atr * proximity_atr_mult
    low = ob.price_low - tolerance
    high = ob.price_high + tolerance

    if low <= entry_price <= high:
        return True, ""

    return False, "entry_outside_ob_zone"
