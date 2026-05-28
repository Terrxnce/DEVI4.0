"""Tests for src/zones/tracker.py

Coverage:
- Registration: new zones are added, duplicates are skipped
- OB mitigation: bullish violated by close < price_low, bearish by close > price_high
- FVG mitigation: same directional logic as OB
- BOS: not mitigated by price; consumed via mark_bos_consumed()
- Event structures (ENGULFING, REJECTION, SWEEP): not mitigated by price
- Expiry: zones older than max_zone_age_bars are expired
- Query: get_active_zones / get_active_structures only return ACTIVE zones
- is_active() check
- zone_count() filtered by state
- scan() convenience: expire -> mitigate -> register order
- mark_consumed() for generic structures
- clear_symbol()
- summary() shape
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.core.enums import Direction, StructureType, Timeframe
from src.core.models import Bar, DetectedStructure
from src.zones.tracker import ZoneState, ZoneTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SYMBOL = "EURUSD"
TF = Timeframe.M15


def _bar(
    bar_index: int,
    close: float,
    high: float | None = None,
    low: float | None = None,
) -> Bar:
    return Bar(
        symbol=SYMBOL,
        timeframe=TF,
        time=datetime(2026, 5, 1, 8, 0, tzinfo=UTC),
        open=close,
        high=high if high is not None else close + 0.0005,
        low=low if low is not None else close - 0.0005,
        close=close,
        volume=100.0,
        bar_index=bar_index,
    )


def _structure(
    structure_type: StructureType,
    direction: Direction,
    bar_index: int = 10,
    price_high: float = 1.1020,
    price_low: float = 1.0990,
    timeframe: Timeframe = TF,
) -> DetectedStructure:
    return DetectedStructure(
        structure_type=structure_type,
        direction=direction,
        price_high=price_high,
        price_low=price_low,
        quality=0.75,
        age_bars=1,
        atr_relative_size=0.8,
        timeframe=timeframe,
        bar_index=bar_index,
        bar_time=datetime(2026, 5, 1, 7, 45, tzinfo=UTC),
        metadata={},
    )


# Shorthand structure builders
def _bullish_ob(bar_index: int = 10, high: float = 1.1020, low: float = 1.0990) -> DetectedStructure:
    return _structure(StructureType.ORDER_BLOCK, Direction.BULLISH, bar_index, high, low)


def _bearish_ob(bar_index: int = 10, high: float = 1.1020, low: float = 1.0990) -> DetectedStructure:
    return _structure(StructureType.ORDER_BLOCK, Direction.BEARISH, bar_index, high, low)


def _bullish_fvg(bar_index: int = 11, high: float = 1.1010, low: float = 1.1000) -> DetectedStructure:
    return _structure(StructureType.FAIR_VALUE_GAP, Direction.BULLISH, bar_index, high, low)


def _bos(direction: Direction = Direction.BULLISH, bar_index: int = 12) -> DetectedStructure:
    level = 1.1015
    return _structure(StructureType.BREAK_OF_STRUCTURE, direction, bar_index, level, level)


def _engulfing(bar_index: int = 13) -> DetectedStructure:
    return _structure(StructureType.ENGULFING, Direction.BULLISH, bar_index)


def _sweep(bar_index: int = 14) -> DetectedStructure:
    return _structure(StructureType.LIQUIDITY_SWEEP, Direction.BULLISH, bar_index)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_register_new_zones_returns_count(self):
        tracker = ZoneTracker()
        count = tracker.register_structures(SYMBOL, [_bullish_ob(), _bullish_fvg()])
        assert count == 2

    def test_duplicate_registration_is_ignored(self):
        tracker = ZoneTracker()
        tracker.register_structures(SYMBOL, [_bullish_ob(bar_index=10)])
        count = tracker.register_structures(SYMBOL, [_bullish_ob(bar_index=10)])
        assert count == 0
        assert tracker.zone_count(SYMBOL) == 1

    def test_different_bar_index_creates_separate_zone(self):
        tracker = ZoneTracker()
        tracker.register_structures(SYMBOL, [_bullish_ob(bar_index=10)])
        count = tracker.register_structures(SYMBOL, [_bullish_ob(bar_index=11)])
        assert count == 1
        assert tracker.zone_count(SYMBOL) == 2

    def test_different_symbol_creates_separate_zone(self):
        tracker = ZoneTracker()
        tracker.register_structures(SYMBOL, [_bullish_ob(bar_index=10)])
        tracker.register_structures("GBPUSD", [_bullish_ob(bar_index=10)])
        assert tracker.zone_count(SYMBOL) == 1
        assert tracker.zone_count("GBPUSD") == 1

    def test_all_registered_zones_start_active(self):
        tracker = ZoneTracker()
        tracker.register_structures(SYMBOL, [_bullish_ob(), _bearish_ob(bar_index=20), _bos()])
        assert tracker.zone_count(SYMBOL, ZoneState.ACTIVE) == 3


# ---------------------------------------------------------------------------
# OB mitigation
# ---------------------------------------------------------------------------

class TestOBMitigation:
    def test_bullish_ob_mitigated_when_close_below_low(self):
        tracker = ZoneTracker()
        ob = _bullish_ob(bar_index=10, high=1.1020, low=1.0990)
        tracker.register_structures(SYMBOL, [ob])

        # close below price_low -> violated
        violated_bar = _bar(bar_index=15, close=1.0985)
        mitigated = tracker.update_mitigation(SYMBOL, violated_bar)

        assert len(mitigated) == 1
        assert tracker.zone_count(SYMBOL, ZoneState.MITIGATED) == 1
        assert tracker.zone_count(SYMBOL, ZoneState.ACTIVE) == 0

    def test_bullish_ob_stays_active_when_close_above_low(self):
        tracker = ZoneTracker()
        ob = _bullish_ob(bar_index=10, high=1.1020, low=1.0990)
        tracker.register_structures(SYMBOL, [ob])

        safe_bar = _bar(bar_index=15, close=1.0995)  # above price_low
        mitigated = tracker.update_mitigation(SYMBOL, safe_bar)

        assert mitigated == []
        assert tracker.zone_count(SYMBOL, ZoneState.ACTIVE) == 1

    def test_bullish_ob_stays_active_when_close_exactly_at_low(self):
        tracker = ZoneTracker()
        ob = _bullish_ob(bar_index=10, high=1.1020, low=1.0990)
        tracker.register_structures(SYMBOL, [ob])

        at_low_bar = _bar(bar_index=15, close=1.0990)  # exactly at price_low, not below
        mitigated = tracker.update_mitigation(SYMBOL, at_low_bar)

        assert mitigated == []

    def test_bearish_ob_mitigated_when_close_above_high(self):
        tracker = ZoneTracker()
        ob = _bearish_ob(bar_index=10, high=1.1020, low=1.0990)
        tracker.register_structures(SYMBOL, [ob])

        violated_bar = _bar(bar_index=15, close=1.1025)  # above price_high
        mitigated = tracker.update_mitigation(SYMBOL, violated_bar)

        assert len(mitigated) == 1
        assert tracker.zone_count(SYMBOL, ZoneState.MITIGATED) == 1

    def test_bearish_ob_stays_active_when_close_below_high(self):
        tracker = ZoneTracker()
        ob = _bearish_ob(bar_index=10, high=1.1020, low=1.0990)
        tracker.register_structures(SYMBOL, [ob])

        safe_bar = _bar(bar_index=15, close=1.1015)  # below price_high
        mitigated = tracker.update_mitigation(SYMBOL, safe_bar)

        assert mitigated == []

    def test_mitigated_zone_records_bar_index(self):
        tracker = ZoneTracker()
        ob = _bullish_ob(bar_index=10, low=1.0990)
        tracker.register_structures(SYMBOL, [ob])

        violated_bar = _bar(bar_index=20, close=1.0980)
        tracker.update_mitigation(SYMBOL, violated_bar)

        records = list(tracker._zones.values())
        assert records[0].mitigated_bar_index == 20

    def test_already_mitigated_zone_not_double_transitioned(self):
        tracker = ZoneTracker()
        ob = _bullish_ob(bar_index=10, low=1.0990)
        tracker.register_structures(SYMBOL, [ob])

        bar1 = _bar(bar_index=15, close=1.0980)
        tracker.update_mitigation(SYMBOL, bar1)
        assert tracker.zone_count(SYMBOL, ZoneState.MITIGATED) == 1

        bar2 = _bar(bar_index=16, close=1.0970)
        mitigated = tracker.update_mitigation(SYMBOL, bar2)
        assert mitigated == []  # already mitigated, not re-processed
        assert tracker.zone_count(SYMBOL, ZoneState.MITIGATED) == 1


# ---------------------------------------------------------------------------
# FVG mitigation
# ---------------------------------------------------------------------------

class TestFVGMitigation:
    def test_bullish_fvg_mitigated_when_close_below_low(self):
        tracker = ZoneTracker()
        fvg = _bullish_fvg(bar_index=11, high=1.1010, low=1.1000)
        tracker.register_structures(SYMBOL, [fvg])

        violated_bar = _bar(bar_index=16, close=1.0995)
        mitigated = tracker.update_mitigation(SYMBOL, violated_bar)

        assert len(mitigated) == 1

    def test_bullish_fvg_stays_active_when_close_within_or_above(self):
        tracker = ZoneTracker()
        fvg = _bullish_fvg(bar_index=11, high=1.1010, low=1.1000)
        tracker.register_structures(SYMBOL, [fvg])

        # close within the gap
        within_bar = _bar(bar_index=16, close=1.1005)
        mitigated = tracker.update_mitigation(SYMBOL, within_bar)

        assert mitigated == []


# ---------------------------------------------------------------------------
# BOS: not mitigated by price, consumed by use
# ---------------------------------------------------------------------------

class TestBOSBehavior:
    def test_bos_not_mitigated_by_price(self):
        tracker = ZoneTracker()
        bos = _bos(direction=Direction.BULLISH, bar_index=12)
        tracker.register_structures(SYMBOL, [bos])

        # price far below BOS level -- would violate if it were a bullish OB
        bar = _bar(bar_index=15, close=1.0000)
        mitigated = tracker.update_mitigation(SYMBOL, bar)

        assert mitigated == []
        assert tracker.zone_count(SYMBOL, ZoneState.ACTIVE) == 1

    def test_bos_consumed_via_mark_bos_consumed(self):
        tracker = ZoneTracker()
        bos = _bos(direction=Direction.BULLISH, bar_index=12)
        tracker.register_structures(SYMBOL, [bos], current_bar_index=12)

        result = tracker.mark_bos_consumed(SYMBOL, bar_index=12, timeframe=TF, current_bar_index=15)

        assert result is True
        assert tracker.zone_count(SYMBOL, ZoneState.CONSUMED) == 1
        assert tracker.zone_count(SYMBOL, ZoneState.ACTIVE) == 0

    def test_mark_bos_consumed_returns_false_if_not_found(self):
        tracker = ZoneTracker()
        result = tracker.mark_bos_consumed(SYMBOL, bar_index=99, timeframe=TF, current_bar_index=100)
        assert result is False

    def test_mark_bos_consumed_returns_false_if_already_consumed(self):
        tracker = ZoneTracker()
        bos = _bos(bar_index=12)
        tracker.register_structures(SYMBOL, [bos])
        tracker.mark_bos_consumed(SYMBOL, 12, TF, 15)

        result = tracker.mark_bos_consumed(SYMBOL, 12, TF, 20)
        assert result is False

    def test_consumed_bos_records_bar_index(self):
        tracker = ZoneTracker()
        bos = _bos(bar_index=12)
        tracker.register_structures(SYMBOL, [bos])
        tracker.mark_bos_consumed(SYMBOL, 12, TF, current_bar_index=17)

        records = list(tracker._zones.values())
        assert records[0].consumed_bar_index == 17


# ---------------------------------------------------------------------------
# Event-based structures: ENGULFING, REJECTION, SWEEP
# ---------------------------------------------------------------------------

class TestEventStructures:
    def test_engulfing_not_mitigated_by_price(self):
        tracker = ZoneTracker()
        tracker.register_structures(SYMBOL, [_engulfing(bar_index=13)])

        bar = _bar(bar_index=16, close=0.9000)  # extreme move
        mitigated = tracker.update_mitigation(SYMBOL, bar)
        assert mitigated == []

    def test_sweep_not_mitigated_by_price(self):
        tracker = ZoneTracker()
        tracker.register_structures(SYMBOL, [_sweep(bar_index=14)])

        bar = _bar(bar_index=16, close=2.0000)  # extreme move
        mitigated = tracker.update_mitigation(SYMBOL, bar)
        assert mitigated == []

    def test_mark_consumed_works_for_any_type(self):
        tracker = ZoneTracker()
        eng = _engulfing(bar_index=13)
        tracker.register_structures(SYMBOL, [eng])

        result = tracker.mark_consumed(
            SYMBOL, StructureType.ENGULFING, bar_index=13, timeframe=TF, current_bar_index=16
        )
        assert result is True
        assert tracker.zone_count(SYMBOL, ZoneState.CONSUMED) == 1


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------

class TestExpiry:
    def test_zone_expired_when_age_exceeds_max(self):
        tracker = ZoneTracker(max_zone_age_bars=10)
        tracker.register_structures(SYMBOL, [_bullish_ob(bar_index=5)])

        expired = tracker.expire_old_zones(SYMBOL, current_bar_index=16)  # age = 11 > 10
        assert expired == 1
        assert tracker.zone_count(SYMBOL, ZoneState.EXPIRED) == 1

    def test_zone_not_expired_when_age_equals_max(self):
        tracker = ZoneTracker(max_zone_age_bars=10)
        tracker.register_structures(SYMBOL, [_bullish_ob(bar_index=5)])

        expired = tracker.expire_old_zones(SYMBOL, current_bar_index=15)  # age = 10 == max, not >
        assert expired == 0
        assert tracker.zone_count(SYMBOL, ZoneState.ACTIVE) == 1

    def test_zone_not_expired_when_age_below_max(self):
        tracker = ZoneTracker(max_zone_age_bars=10)
        tracker.register_structures(SYMBOL, [_bullish_ob(bar_index=5)])

        expired = tracker.expire_old_zones(SYMBOL, current_bar_index=10)  # age = 5
        assert expired == 0

    def test_already_mitigated_zone_not_expired(self):
        tracker = ZoneTracker(max_zone_age_bars=10)
        ob = _bullish_ob(bar_index=5, low=1.0990)
        tracker.register_structures(SYMBOL, [ob])

        # mitigate first
        tracker.update_mitigation(SYMBOL, _bar(bar_index=8, close=1.0980))
        assert tracker.zone_count(SYMBOL, ZoneState.MITIGATED) == 1

        # then try to expire
        expired = tracker.expire_old_zones(SYMBOL, current_bar_index=20)
        assert expired == 0  # already mitigated, not re-expired

    def test_expired_zone_records_bar_index(self):
        tracker = ZoneTracker(max_zone_age_bars=5)
        tracker.register_structures(SYMBOL, [_bullish_ob(bar_index=5)])
        tracker.expire_old_zones(SYMBOL, current_bar_index=12)

        records = list(tracker._zones.values())
        assert records[0].expired_bar_index == 12


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

class TestQueries:
    def test_get_active_zones_returns_only_active(self):
        tracker = ZoneTracker()
        # ob_active has a lower floor (1.0960): close=1.0975 sits above it, zone survives.
        # ob_mitigated has a higher floor (1.0980): close=1.0975 falls below it, zone is mitigated.
        ob_active = _bullish_ob(bar_index=10, low=1.0960)
        ob_mitigated = _bullish_ob(bar_index=11, low=1.0980)
        tracker.register_structures(SYMBOL, [ob_active, ob_mitigated])

        # mitigate bar_index=11 OB only (1.0975 < 1.0980 but >= 1.0960)
        tracker.update_mitigation(SYMBOL, _bar(bar_index=15, close=1.0975))

        active = tracker.get_active_zones(SYMBOL)
        assert len(active) == 1
        assert active[0].key.bar_index == 10

    def test_get_active_zones_filters_by_type(self):
        tracker = ZoneTracker()
        tracker.register_structures(SYMBOL, [_bullish_ob(), _bullish_fvg(), _bos()])

        obs = tracker.get_active_zones(SYMBOL, structure_type=StructureType.ORDER_BLOCK)
        assert len(obs) == 1
        assert obs[0].key.structure_type == StructureType.ORDER_BLOCK

    def test_get_active_zones_filters_by_timeframe(self):
        tracker = ZoneTracker()
        ob_m15 = _structure(StructureType.ORDER_BLOCK, Direction.BULLISH, bar_index=10, timeframe=Timeframe.M15)
        ob_h1 = _structure(StructureType.ORDER_BLOCK, Direction.BULLISH, bar_index=10, timeframe=Timeframe.H1)
        tracker.register_structures(SYMBOL, [ob_m15, ob_h1])

        m15_zones = tracker.get_active_zones(SYMBOL, timeframe=Timeframe.M15)
        assert len(m15_zones) == 1
        assert m15_zones[0].key.timeframe == Timeframe.M15

    def test_get_active_structures_returns_detected_structures(self):
        tracker = ZoneTracker()
        ob = _bullish_ob()
        tracker.register_structures(SYMBOL, [ob])

        structures = tracker.get_active_structures(SYMBOL)
        assert len(structures) == 1
        assert structures[0] is ob

    def test_is_active_true_for_active_zone(self):
        tracker = ZoneTracker()
        tracker.register_structures(SYMBOL, [_bullish_ob(bar_index=10)])
        assert tracker.is_active(SYMBOL, StructureType.ORDER_BLOCK, TF, 10) is True

    def test_is_active_false_after_mitigation(self):
        tracker = ZoneTracker()
        tracker.register_structures(SYMBOL, [_bullish_ob(bar_index=10, low=1.0990)])
        tracker.update_mitigation(SYMBOL, _bar(bar_index=15, close=1.0980))
        assert tracker.is_active(SYMBOL, StructureType.ORDER_BLOCK, TF, 10) is False

    def test_is_active_false_for_unknown_zone(self):
        tracker = ZoneTracker()
        assert tracker.is_active(SYMBOL, StructureType.ORDER_BLOCK, TF, 999) is False

    def test_zone_count_total(self):
        tracker = ZoneTracker()
        tracker.register_structures(SYMBOL, [_bullish_ob(), _bullish_fvg(), _bos()])
        assert tracker.zone_count(SYMBOL) == 3

    def test_zone_count_filtered_by_state(self):
        tracker = ZoneTracker(max_zone_age_bars=5)
        tracker.register_structures(SYMBOL, [
            _bullish_ob(bar_index=5, low=1.0990),
            # FVG floor set below close=1.0980 so it survives mitigation and expires instead.
            _bullish_fvg(bar_index=8, low=1.0960),
            _bos(bar_index=9),
        ])
        # mitigate only the OB (close=1.0980 < OB.low=1.0990; FVG.low=1.0960 is safe)
        tracker.update_mitigation(SYMBOL, _bar(bar_index=10, close=1.0980))
        # expire the FVG (age=14-8=6 > 5); BOS (age=14-9=5) is exactly at limit, stays ACTIVE
        tracker.expire_old_zones(SYMBOL, current_bar_index=14)

        assert tracker.zone_count(SYMBOL, ZoneState.MITIGATED) == 1
        assert tracker.zone_count(SYMBOL, ZoneState.EXPIRED) == 1
        assert tracker.zone_count(SYMBOL, ZoneState.ACTIVE) == 1


# ---------------------------------------------------------------------------
# Scan convenience
# ---------------------------------------------------------------------------

class TestScan:
    def test_scan_returns_correct_summary(self):
        tracker = ZoneTracker(max_zone_age_bars=5)
        # pre-load an old zone that will expire (bar_index=1, age=9 > 5)
        tracker.register_structures(SYMBOL, [_bullish_ob(bar_index=1, low=1.0990)])
        # pre-load a recent OB that will be mitigated (bar_index=6, age=4 <= 5, survives expiry)
        tracker.register_structures(SYMBOL, [_bearish_ob(bar_index=6, high=1.1000)])

        current_bar = _bar(bar_index=10, close=1.1005)  # violates bearish OB (close > 1.1000)
        new_structures = [_bos(bar_index=10)]

        result = tracker.scan(SYMBOL, new_structures, current_bar)

        assert result["expired"] == 1       # bullish OB bar_index=1, age=9 > 5
        assert result["mitigated"] == 1     # bearish OB bar_index=6 violated by close=1.1005
        assert result["registered"] == 1    # new BOS
        assert result["active"] == 1        # only the new BOS

    def test_scan_expire_runs_before_mitigation(self):
        """Expired zones should not be mitigated in the same scan."""
        tracker = ZoneTracker(max_zone_age_bars=3)
        # zone that is both old (should expire) and would be mitigated
        tracker.register_structures(SYMBOL, [_bullish_ob(bar_index=1, low=1.0990)])

        current_bar = _bar(bar_index=10, close=1.0980)  # would violate OB
        tracker.scan(SYMBOL, [], current_bar)

        # should be EXPIRED, not MITIGATED (expiry runs first)
        assert tracker.zone_count(SYMBOL, ZoneState.EXPIRED) == 1
        assert tracker.zone_count(SYMBOL, ZoneState.MITIGATED) == 0


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

class TestMaintenance:
    def test_clear_symbol_removes_all_zones(self):
        tracker = ZoneTracker()
        tracker.register_structures(SYMBOL, [_bullish_ob(), _bullish_fvg()])
        tracker.register_structures("GBPUSD", [_bearish_ob()])

        removed = tracker.clear_symbol(SYMBOL)

        assert removed == 2
        assert tracker.zone_count(SYMBOL) == 0
        assert tracker.zone_count("GBPUSD") == 1

    def test_summary_returns_expected_keys(self):
        tracker = ZoneTracker()
        tracker.register_structures(SYMBOL, [_bullish_ob(), _bullish_fvg()])

        s = tracker.summary(SYMBOL)

        assert s["symbol"] == SYMBOL
        assert s["total"] == 2
        assert "by_state" in s
        assert "active_by_type" in s
        assert s["by_state"].get("ACTIVE") == 2

    def test_summary_reflects_state_changes(self):
        tracker = ZoneTracker()
        tracker.register_structures(SYMBOL, [_bullish_ob(bar_index=10, low=1.0990)])
        tracker.update_mitigation(SYMBOL, _bar(bar_index=15, close=1.0980))

        s = tracker.summary(SYMBOL)
        assert s["by_state"].get("MITIGATED") == 1
        assert s["by_state"].get("ACTIVE", 0) == 0
