"""Tests for src/decision/entry_gate.py

Coverage:
- OB as primary trigger: entry within zone passes
- OB as primary trigger: entry outside zone (below low, above high) fails
- OB as primary trigger: edge at tolerance boundary
- OB in structural confirmations (SWEEP_WITH_OB): same logic
- Non-OB setup (REJECTION_WITH_FVG): gate not applied, always passes
- proximity_atr_mult=0 collapses to exact-zone check
- Zero ATR: tolerance is 0 so exact boundary required
- Bearish OB: entry above zone high fails, within zone passes
- evaluate_entry_proximity returns correct failure code
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.core.enums import (
    ConfidenceTier,
    Direction,
    SetupClass,
    StructureType,
    Timeframe,
)
from src.core.models import ConfluenceResult, DetectedStructure
from src.decision.entry_gate import evaluate_entry_proximity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TF = Timeframe.M15
ATR = 0.0010  # 10 pips — typical EURUSD ATR on M15


def _struct(
    structure_type: StructureType,
    direction: Direction,
    price_high: float,
    price_low: float,
    bar_index: int = 10,
) -> DetectedStructure:
    return DetectedStructure(
        structure_type=structure_type,
        direction=direction,
        price_high=price_high,
        price_low=price_low,
        quality=0.75,
        age_bars=2,
        atr_relative_size=0.8,
        timeframe=TF,
        bar_index=bar_index,
        bar_time=datetime(2026, 5, 1, 8, 0, tzinfo=UTC),
        metadata={},
    )


def _confluence(
    primary: DetectedStructure,
    confirmations: list[DetectedStructure] | None = None,
    setup_class: SetupClass = SetupClass.OB_WITH_BOS,
    direction: Direction = Direction.BULLISH,
) -> ConfluenceResult:
    return ConfluenceResult(
        setup_class=setup_class,
        direction=direction,
        primary_trigger=primary,
        structural_confirmations=confirmations or [],
        structural_labels=[],
        minor_confluences=[],
        hard_rejects=[],
        soft_penalties=[],
        structural_count=1,
        minor_count=0,
        quality_penalty=0.0,
        effective_quality=0.75,
        confluence_pass=True,
        confidence_tier=ConfidenceTier.B,
        tier_reason="tier_b_confirmations",
    )


# ---------------------------------------------------------------------------
# OB as primary trigger: Bullish
# ---------------------------------------------------------------------------

class TestBullishOBPrimary:
    """Bullish OB zone: price_low=1.0990, price_high=1.1020.
    Default proximity_atr_mult=0.5, ATR=0.0010 -> tolerance=0.0005.
    Effective range: [1.0985, 1.1025].
    """

    OB_LOW = 1.0990
    OB_HIGH = 1.1020

    def _make(self) -> ConfluenceResult:
        ob = _struct(StructureType.ORDER_BLOCK, Direction.BULLISH, self.OB_HIGH, self.OB_LOW)
        bos = _struct(StructureType.BREAK_OF_STRUCTURE, Direction.BULLISH, 1.1030, 1.1030, bar_index=12)
        return _confluence(ob, [bos], SetupClass.OB_WITH_BOS, Direction.BULLISH)

    def test_entry_within_ob_zone_passes(self):
        cf = self._make()
        ok, code = evaluate_entry_proximity(cf, entry_price=1.1005, atr=ATR)
        assert ok is True
        assert code == ""

    def test_entry_at_ob_low_passes(self):
        cf = self._make()
        ok, _ = evaluate_entry_proximity(cf, entry_price=self.OB_LOW, atr=ATR)
        assert ok is True

    def test_entry_at_ob_high_passes(self):
        cf = self._make()
        ok, _ = evaluate_entry_proximity(cf, entry_price=self.OB_HIGH, atr=ATR)
        assert ok is True

    def test_entry_within_tolerance_below_low_passes(self):
        # 1.0990 - 0.0005 = 1.0985 -> within tolerance
        cf = self._make()
        ok, _ = evaluate_entry_proximity(cf, entry_price=1.0985, atr=ATR)
        assert ok is True

    def test_entry_within_tolerance_above_high_passes(self):
        # 1.1020 + 0.0005 = 1.1025 -> within tolerance
        cf = self._make()
        ok, _ = evaluate_entry_proximity(cf, entry_price=1.1025, atr=ATR)
        assert ok is True

    def test_entry_just_outside_tolerance_below_fails(self):
        # 1.0990 - 0.0005 - epsilon -> outside
        cf = self._make()
        ok, code = evaluate_entry_proximity(cf, entry_price=1.0984, atr=ATR)
        assert ok is False
        assert code == "entry_outside_ob_zone"

    def test_entry_just_outside_tolerance_above_fails(self):
        # 1.1020 + 0.0005 + epsilon -> outside
        cf = self._make()
        ok, code = evaluate_entry_proximity(cf, entry_price=1.1026, atr=ATR)
        assert ok is False
        assert code == "entry_outside_ob_zone"

    def test_entry_far_above_zone_fails(self):
        cf = self._make()
        ok, code = evaluate_entry_proximity(cf, entry_price=1.1100, atr=ATR)
        assert ok is False
        assert code == "entry_outside_ob_zone"

    def test_entry_far_below_zone_fails(self):
        cf = self._make()
        ok, code = evaluate_entry_proximity(cf, entry_price=1.0900, atr=ATR)
        assert ok is False
        assert code == "entry_outside_ob_zone"


# ---------------------------------------------------------------------------
# OB as primary trigger: Bearish
# ---------------------------------------------------------------------------

class TestBearishOBPrimary:
    """Bearish OB zone: price_low=1.1020, price_high=1.1040.
    Tolerance=0.0005. Effective range: [1.1015, 1.1045].
    """

    OB_LOW = 1.1020
    OB_HIGH = 1.1040

    def _make(self) -> ConfluenceResult:
        ob = _struct(StructureType.ORDER_BLOCK, Direction.BEARISH, self.OB_HIGH, self.OB_LOW)
        bos = _struct(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, 1.1010, 1.1010, bar_index=12)
        return _confluence(ob, [bos], SetupClass.OB_WITH_BOS, Direction.BEARISH)

    def test_entry_within_bearish_ob_zone_passes(self):
        cf = self._make()
        ok, _ = evaluate_entry_proximity(cf, entry_price=1.1030, atr=ATR)
        assert ok is True

    def test_entry_within_tolerance_above_high_passes(self):
        cf = self._make()
        ok, _ = evaluate_entry_proximity(cf, entry_price=1.1045, atr=ATR)
        assert ok is True

    def test_entry_outside_tolerance_above_fails(self):
        cf = self._make()
        ok, code = evaluate_entry_proximity(cf, entry_price=1.1046, atr=ATR)
        assert ok is False
        assert code == "entry_outside_ob_zone"

    def test_entry_outside_tolerance_below_fails(self):
        cf = self._make()
        ok, code = evaluate_entry_proximity(cf, entry_price=1.1014, atr=ATR)
        assert ok is False
        assert code == "entry_outside_ob_zone"


# ---------------------------------------------------------------------------
# OB in confirmations (SWEEP_WITH_OB)
# ---------------------------------------------------------------------------

class TestSweepWithOB:
    """Primary is SWEEP, OB is in structural_confirmations."""

    OB_LOW = 1.0995
    OB_HIGH = 1.1015

    def _make(self) -> ConfluenceResult:
        sweep = _struct(StructureType.LIQUIDITY_SWEEP, Direction.BULLISH, 1.0985, 1.0985, bar_index=9)
        ob = _struct(StructureType.ORDER_BLOCK, Direction.BULLISH, self.OB_HIGH, self.OB_LOW, bar_index=8)
        return _confluence(sweep, [ob], SetupClass.SWEEP_WITH_OB, Direction.BULLISH)

    def test_entry_within_ob_confirmation_zone_passes(self):
        cf = self._make()
        ok, _ = evaluate_entry_proximity(cf, entry_price=1.1005, atr=ATR)
        assert ok is True

    def test_entry_outside_ob_confirmation_zone_fails(self):
        cf = self._make()
        ok, code = evaluate_entry_proximity(cf, entry_price=1.0989, atr=ATR)
        assert ok is False
        assert code == "entry_outside_ob_zone"


# ---------------------------------------------------------------------------
# Non-OB setups: gate does not apply
# ---------------------------------------------------------------------------

class TestNonOBSetups:
    def test_rejection_with_fvg_always_passes(self):
        rejection = _struct(StructureType.REJECTION, Direction.BEARISH, 1.1050, 1.1040)
        fvg = _struct(StructureType.FAIR_VALUE_GAP, Direction.BEARISH, 1.1035, 1.1025)
        cf = _confluence(rejection, [fvg], SetupClass.REJECTION_WITH_FVG, Direction.BEARISH)

        # Entry far from any OB — but no OB in setup so gate doesn't apply
        ok, code = evaluate_entry_proximity(cf, entry_price=1.2000, atr=ATR)
        assert ok is True
        assert code == ""

    def test_rejection_with_fvg_passes_even_with_zero_atr(self):
        rejection = _struct(StructureType.REJECTION, Direction.BULLISH, 1.1000, 1.0990)
        fvg = _struct(StructureType.FAIR_VALUE_GAP, Direction.BULLISH, 1.1010, 1.1005)
        cf = _confluence(rejection, [fvg], SetupClass.REJECTION_WITH_FVG, Direction.BULLISH)

        ok, _ = evaluate_entry_proximity(cf, entry_price=1.1100, atr=0.0)
        assert ok is True


# ---------------------------------------------------------------------------
# Edge cases: tolerance variants
# ---------------------------------------------------------------------------

class TestToleranceVariants:
    OB_LOW = 1.1000
    OB_HIGH = 1.1020

    def _ob_confluence(self) -> ConfluenceResult:
        ob = _struct(StructureType.ORDER_BLOCK, Direction.BULLISH, self.OB_HIGH, self.OB_LOW)
        bos = _struct(StructureType.BREAK_OF_STRUCTURE, Direction.BULLISH, 1.1030, 1.1030, bar_index=12)
        return _confluence(ob, [bos])

    def test_zero_atr_mult_means_exact_zone_only(self):
        """With proximity_atr_mult=0 and ATR=0.001: tolerance=0, exact zone."""
        cf = self._ob_confluence()

        # Exactly at price_low: passes
        ok, _ = evaluate_entry_proximity(cf, entry_price=self.OB_LOW, atr=ATR, proximity_atr_mult=0.0)
        assert ok is True

        # 1 pip below price_low: fails
        ok, _ = evaluate_entry_proximity(cf, entry_price=self.OB_LOW - 0.0001, atr=ATR, proximity_atr_mult=0.0)
        assert ok is False

    def test_large_atr_mult_widens_range(self):
        """proximity_atr_mult=2.0: tolerance = 2 * ATR = 0.002."""
        cf = self._ob_confluence()

        # 0.0015 below price_low (within 2xATR tolerance): passes
        ok, _ = evaluate_entry_proximity(
            cf, entry_price=self.OB_LOW - 0.0015, atr=ATR, proximity_atr_mult=2.0
        )
        assert ok is True

        # 0.0025 below price_low (outside 2xATR tolerance): fails
        ok, _ = evaluate_entry_proximity(
            cf, entry_price=self.OB_LOW - 0.0025, atr=ATR, proximity_atr_mult=2.0
        )
        assert ok is False

    def test_zero_atr_with_default_mult_means_exact_zone(self):
        """Zero ATR: tolerance = 0, exact zone boundary required."""
        cf = self._ob_confluence()

        ok, _ = evaluate_entry_proximity(cf, entry_price=self.OB_LOW, atr=0.0)
        assert ok is True

        ok, code = evaluate_entry_proximity(cf, entry_price=self.OB_LOW - 0.00001, atr=0.0)
        assert ok is False
        assert code == "entry_outside_ob_zone"
