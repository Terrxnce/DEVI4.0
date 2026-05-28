"""ZoneTracker: persistent zone registry for a single trading session.

Tracks the lifecycle of SMC structures (Order Blocks, FVGs, BOS) across
scan iterations for one or more symbols. Zone state flows in one direction:

    ACTIVE -> MITIGATED  (price closed through the zone)
    ACTIVE -> CONSUMED   (BOS used in a confluence setup -- one-time event)
    ACTIVE -> EXPIRED    (zone age exceeded max_zone_age_bars)

Mitigated / consumed / expired zones are retained for audit but never
returned by get_active_zones().

Design constraints:
- In-memory only. No cross-run persistence in this version.
- Deterministic: same bar data always produces the same state transitions.
- Only OB and FVG zones are checked for price mitigation.
  BOS is a one-time event: consumed via mark_bos_consumed(), not by price.
  LIQUIDITY_SWEEP, REJECTION, ENGULFING are event-based: they don't hold
  a price zone and are tracked for deduplication / one-time consumption only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple

from src.core.enums import Direction, StructureType, Timeframe
from src.core.models import Bar, DetectedStructure


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Zone state
# ---------------------------------------------------------------------------

class ZoneState(str, Enum):
    ACTIVE = "ACTIVE"
    MITIGATED = "MITIGATED"   # price violated the zone boundary
    CONSUMED = "CONSUMED"     # used in a live setup (BOS / event-based structures)
    EXPIRED = "EXPIRED"       # too old to be relevant


# ---------------------------------------------------------------------------
# Zone identity key
# ---------------------------------------------------------------------------

class ZoneKey(NamedTuple):
    """Unique identifier for a zone.

    Two separate candles on the same bar_index / timeframe / type are the
    same zone. A re-detection of an existing zone (same key) is silently
    ignored by register_structures().
    """

    symbol: str
    structure_type: StructureType
    timeframe: Timeframe
    bar_index: int


# ---------------------------------------------------------------------------
# Zone record (mutable wrapper around DetectedStructure)
# ---------------------------------------------------------------------------

@dataclass
class ZoneRecord:
    key: ZoneKey
    structure: DetectedStructure
    state: ZoneState = ZoneState.ACTIVE
    registered_bar_index: int = 0
    mitigated_bar_index: int | None = None
    consumed_bar_index: int | None = None
    expired_bar_index: int | None = None

    @property
    def is_active(self) -> bool:
        return self.state == ZoneState.ACTIVE


# ---------------------------------------------------------------------------
# Mitigation logic
# ---------------------------------------------------------------------------

_ZONE_BASED_TYPES: frozenset[StructureType] = frozenset({
    StructureType.ORDER_BLOCK,
    StructureType.FAIR_VALUE_GAP,
})


def _is_price_mitigated(structure: DetectedStructure, current_bar: Bar) -> bool:
    """Return True if the current bar's close violates a zone-based structure.

    Only ORDER_BLOCK and FAIR_VALUE_GAP hold a meaningful price zone.
    Other structure types are event-based and are not mitigated by price.

    Bullish zone (demand): violated when close falls below zone low.
    Bearish zone (supply): violated when close rises above zone high.
    """
    if structure.structure_type not in _ZONE_BASED_TYPES:
        return False
    if structure.direction == Direction.BULLISH:
        return current_bar.close < structure.price_low
    if structure.direction == Direction.BEARISH:
        return current_bar.close > structure.price_high
    return False


# ---------------------------------------------------------------------------
# ZoneTracker
# ---------------------------------------------------------------------------

@dataclass
class ZoneTracker:
    """Per-session zone registry.

    Typical usage in a scan loop:

        tracker = ZoneTracker(max_zone_age_bars=50)

        # each iteration:
        changes = tracker.scan(symbol, detected_structures, current_bar)
        logger.info("zone scan: %s", changes)

        active = tracker.get_active_structures(symbol, StructureType.ORDER_BLOCK)
        # pass active structures to decision engine instead of raw detector output

    BOS consumption after a trade is placed:

        tracker.mark_bos_consumed(symbol, bos_bar_index, Timeframe.M15,
                                   current_bar_index=current_bar.bar_index)
    """

    max_zone_age_bars: int = 50
    _zones: dict[ZoneKey, ZoneRecord] = field(default_factory=dict, init=False, repr=False)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_structures(
        self,
        symbol: str,
        structures: list[DetectedStructure],
        current_bar_index: int = 0,
    ) -> int:
        """Register new structures. Skips zones that already exist (by key).

        Returns the number of newly registered zones.
        """
        count = 0
        for s in structures:
            key = ZoneKey(
                symbol=symbol,
                structure_type=s.structure_type,
                timeframe=s.timeframe,
                bar_index=s.bar_index,
            )
            if key not in self._zones:
                self._zones[key] = ZoneRecord(
                    key=key,
                    structure=s,
                    state=ZoneState.ACTIVE,
                    registered_bar_index=current_bar_index,
                )
                count += 1
        return count

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def update_mitigation(self, symbol: str, current_bar: Bar) -> list[ZoneKey]:
        """Check current bar against all active OB / FVG zones for symbol.

        Zones whose price boundaries are violated by the current close are
        transitioned to MITIGATED.

        Returns the list of newly mitigated ZoneKeys.
        """
        mitigated: list[ZoneKey] = []
        for key, record in self._zones.items():
            if key.symbol != symbol:
                continue
            if not record.is_active:
                continue
            if _is_price_mitigated(record.structure, current_bar):
                record.state = ZoneState.MITIGATED
                record.mitigated_bar_index = current_bar.bar_index
                mitigated.append(key)
                logger.debug(
                    "zone_mitigated: symbol=%s type=%s tf=%s bar=%d close=%.5f zone=[%.5f,%.5f]",
                    symbol,
                    key.structure_type.value,
                    key.timeframe.value,
                    key.bar_index,
                    current_bar.close,
                    record.structure.price_low,
                    record.structure.price_high,
                )
        return mitigated

    def mark_bos_consumed(
        self,
        symbol: str,
        bar_index: int,
        timeframe: Timeframe,
        current_bar_index: int,
    ) -> bool:
        """Mark a BOS zone as consumed (one-time use).

        Called after a BOS-based confluence setup is executed or accepted.
        Prevents the same BOS from contributing to a second setup on the
        next scan iteration.

        Returns True if the zone was found and transitioned to CONSUMED.
        Returns False if not found or already non-ACTIVE.
        """
        key = ZoneKey(
            symbol=symbol,
            structure_type=StructureType.BREAK_OF_STRUCTURE,
            timeframe=timeframe,
            bar_index=bar_index,
        )
        record = self._zones.get(key)
        if record is None or not record.is_active:
            return False
        record.state = ZoneState.CONSUMED
        record.consumed_bar_index = current_bar_index
        logger.debug(
            "zone_bos_consumed: symbol=%s tf=%s bar=%d at_bar=%d",
            symbol, timeframe.value, bar_index, current_bar_index,
        )
        return True

    def mark_consumed(
        self,
        symbol: str,
        structure_type: StructureType,
        bar_index: int,
        timeframe: Timeframe,
        current_bar_index: int,
    ) -> bool:
        """Generic one-time consumption for any structure type.

        Useful for event-based structures (ENGULFING, REJECTION, SWEEP)
        that should not be reused on the next scan once they contributed
        to an executed setup.

        Returns True if the zone was found and consumed.
        """
        key = ZoneKey(
            symbol=symbol,
            structure_type=structure_type,
            timeframe=timeframe,
            bar_index=bar_index,
        )
        record = self._zones.get(key)
        if record is None or not record.is_active:
            return False
        record.state = ZoneState.CONSUMED
        record.consumed_bar_index = current_bar_index
        logger.debug(
            "zone_consumed: symbol=%s type=%s tf=%s bar=%d at_bar=%d",
            symbol, structure_type.value, timeframe.value, bar_index, current_bar_index,
        )
        return True

    def expire_old_zones(self, symbol: str, current_bar_index: int) -> int:
        """Expire ACTIVE zones whose age exceeds max_zone_age_bars.

        Age is measured as current_bar_index minus the zone's bar_index
        (the candle where the structure was detected, not when it was registered).

        Returns count of newly expired zones.
        """
        count = 0
        for key, record in self._zones.items():
            if key.symbol != symbol:
                continue
            if not record.is_active:
                continue
            age = current_bar_index - key.bar_index
            if age > self.max_zone_age_bars:
                record.state = ZoneState.EXPIRED
                record.expired_bar_index = current_bar_index
                count += 1
                logger.debug(
                    "zone_expired: symbol=%s type=%s tf=%s bar=%d age=%d",
                    symbol, key.structure_type.value, key.timeframe.value,
                    key.bar_index, age,
                )
        return count

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_active_zones(
        self,
        symbol: str,
        structure_type: StructureType | None = None,
        timeframe: Timeframe | None = None,
    ) -> list[ZoneRecord]:
        """Return all ACTIVE ZoneRecords for symbol.

        Optionally filter by structure_type and/or timeframe.
        """
        result: list[ZoneRecord] = []
        for key, record in self._zones.items():
            if key.symbol != symbol:
                continue
            if not record.is_active:
                continue
            if structure_type is not None and key.structure_type != structure_type:
                continue
            if timeframe is not None and key.timeframe != timeframe:
                continue
            result.append(record)
        return result

    def get_active_structures(
        self,
        symbol: str,
        structure_type: StructureType | None = None,
        timeframe: Timeframe | None = None,
    ) -> list[DetectedStructure]:
        """Convenience: return active DetectedStructure objects only.

        Drop-in replacement for raw detector output in the decision pipeline.
        """
        return [r.structure for r in self.get_active_zones(symbol, structure_type, timeframe)]

    def is_active(
        self,
        symbol: str,
        structure_type: StructureType,
        timeframe: Timeframe,
        bar_index: int,
    ) -> bool:
        """Check if a specific zone is still ACTIVE."""
        key = ZoneKey(symbol, structure_type, timeframe, bar_index)
        record = self._zones.get(key)
        return record is not None and record.is_active

    def zone_count(self, symbol: str, state: ZoneState | None = None) -> int:
        """Count zones for symbol, optionally filtered by state."""
        total = 0
        for key, record in self._zones.items():
            if key.symbol != symbol:
                continue
            if state is None or record.state == state:
                total += 1
        return total

    # ------------------------------------------------------------------
    # Scan convenience
    # ------------------------------------------------------------------

    def scan(
        self,
        symbol: str,
        structures: list[DetectedStructure],
        current_bar: Bar,
    ) -> dict[str, int]:
        """Single-call update for a scan loop iteration.

        Order of operations:
        1. Expire zones that are too old (based on current bar index).
        2. Check price mitigation on active OB / FVG zones.
        3. Register new structures from this iteration's detector output.

        Returns a summary dict suitable for logging:
            {expired, mitigated, registered, active}
        """
        expired = self.expire_old_zones(symbol, current_bar.bar_index)
        mitigated_keys = self.update_mitigation(symbol, current_bar)
        registered = self.register_structures(symbol, structures, current_bar.bar_index)
        active = self.zone_count(symbol, ZoneState.ACTIVE)

        return {
            "expired": expired,
            "mitigated": len(mitigated_keys),
            "registered": registered,
            "active": active,
        }

    # ------------------------------------------------------------------
    # Audit / logging
    # ------------------------------------------------------------------

    def summary(self, symbol: str) -> dict:
        """Return a state summary dict for a symbol (suitable for JSONL logging)."""
        by_state: dict[str, int] = {}
        by_type: dict[str, int] = {}
        for key, record in self._zones.items():
            if key.symbol != symbol:
                continue
            by_state[record.state.value] = by_state.get(record.state.value, 0) + 1
            if record.is_active:
                by_type[key.structure_type.value] = by_type.get(key.structure_type.value, 0) + 1
        return {
            "symbol": symbol,
            "total": self.zone_count(symbol),
            "by_state": by_state,
            "active_by_type": by_type,
        }

    def clear_symbol(self, symbol: str) -> int:
        """Remove all zone records for a symbol (e.g. on session end).

        Returns count removed.
        """
        keys_to_delete = [k for k in self._zones if k.symbol == symbol]
        for k in keys_to_delete:
            del self._zones[k]
        return len(keys_to_delete)
