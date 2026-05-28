from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.context.regime import simple_atr
from src.core.enums import Direction, StructureType, Timeframe
from src.core.models import Bar, DetectedStructure
from src.detectors.fair_value_gap import FairValueGapDetector
from src.detectors.order_block import OrderBlockDetector


class H4Alignment(str, Enum):
    """Verdict returned by H4ContextGate for a proposed trade direction.

    ALIGNED  — current price is inside an H4 structure that supports the trade.
    NEUTRAL  — no strong H4 structure at the current price level.
    COUNTER  — current price is inside an H4 structure that opposes the trade.
               This is a hard reject signal for SWEEP_REVERSAL setups.
    """

    ALIGNED = "ALIGNED"
    NEUTRAL = "NEUTRAL"
    COUNTER = "COUNTER"


@dataclass(frozen=True)
class H4ContextResult:
    """Output of H4ContextGate.evaluate()."""

    alignment: H4Alignment
    reason: str
    structures_checked: int
    conflicting_structures: list[str]  # brief description of counter-structures found


@dataclass(frozen=True)
class H4ContextGate:
    """Evaluates H4-level structure context for a proposed trade direction.

    Designed to prevent entries where price is sitting inside an H4 FVG or OB
    that directly opposes the intended direction — the root cause of the NZDUSD
    sell-into-bullish-H4-FVG problem.

    Usage
    -----
    gate = H4ContextGate()
    result = gate.evaluate(
        direction=Direction.BEARISH,
        current_price=0.58279,
        bars_h4=bars_h4,
        config=config,
    )
    if result.alignment == H4Alignment.COUNTER:
        # hard reject
    """

    atr_period: int = 14
    ob_min_body_atr_mult: float = 0.3   # looser than M15 defaults — H4 structures are bigger
    ob_max_age_bars: int = 50           # H4 bars — 50 * 4h = ~200 trading hours (~8 weeks)
    ob_min_quality: float = 0.35
    fvg_min_gap_atr_mult: float = 0.25
    fvg_max_age_bars: int = 60
    fvg_min_quality: float = 0.35

    def evaluate(
        self,
        direction: Direction,
        current_price: float,
        bars_h4: list[Bar],
        config: dict[str, Any] | None = None,
    ) -> H4ContextResult:
        """Evaluate H4 alignment for a proposed trade direction at current_price.

        Args:
            direction:     The intended trade direction (BULLISH or BEARISH).
            current_price: The proposed entry price.
            bars_h4:       H4 bars for the symbol (oldest first).
            config:        Optional config dict; if provided, overrides atr_period
                           via config["detection"]["atr_period"].

        Returns:
            H4ContextResult with alignment verdict and diagnostic fields.
        """
        if not bars_h4 or len(bars_h4) < self.atr_period + 1:
            return H4ContextResult(
                alignment=H4Alignment.NEUTRAL,
                reason="insufficient_h4_bars",
                structures_checked=0,
                conflicting_structures=[],
            )

        if direction == Direction.NEUTRAL:
            return H4ContextResult(
                alignment=H4Alignment.NEUTRAL,
                reason="direction_neutral",
                structures_checked=0,
                conflicting_structures=[],
            )

        atr_period = self.atr_period
        if config is not None:
            atr_period = int(config.get("detection", {}).get("atr_period", self.atr_period))

        atr = simple_atr(bars_h4, atr_period)
        if atr <= 0:
            return H4ContextResult(
                alignment=H4Alignment.NEUTRAL,
                reason="atr_zero",
                structures_checked=0,
                conflicting_structures=[],
            )

        h4_structures = self._detect_h4_structures(bars_h4, atr)
        if not h4_structures:
            return H4ContextResult(
                alignment=H4Alignment.NEUTRAL,
                reason="no_h4_structures_detected",
                structures_checked=0,
                conflicting_structures=[],
            )

        # Determine which direction would be "counter" to the proposed trade.
        # BEARISH trade opposes BULLISH H4 structures (if price is inside them).
        # BULLISH trade opposes BEARISH H4 structures (if price is inside them).
        opposing_direction = Direction.BULLISH if direction == Direction.BEARISH else Direction.BEARISH

        counter_labels: list[str] = []
        aligned_labels: list[str] = []

        for structure in h4_structures:
            if not self._price_inside_structure(current_price, structure):
                continue

            label = f"{structure.structure_type.value}_{structure.direction.value}_{structure.price_low:.5f}_{structure.price_high:.5f}"

            if structure.direction == opposing_direction:
                counter_labels.append(label)
            elif structure.direction == direction:
                aligned_labels.append(label)

        if counter_labels:
            return H4ContextResult(
                alignment=H4Alignment.COUNTER,
                reason="price_inside_opposing_h4_structure",
                structures_checked=len(h4_structures),
                conflicting_structures=counter_labels,
            )

        if aligned_labels:
            return H4ContextResult(
                alignment=H4Alignment.ALIGNED,
                reason="price_inside_supporting_h4_structure",
                structures_checked=len(h4_structures),
                conflicting_structures=[],
            )

        return H4ContextResult(
            alignment=H4Alignment.NEUTRAL,
            reason="no_h4_structure_at_current_price",
            structures_checked=len(h4_structures),
            conflicting_structures=[],
        )

    def _detect_h4_structures(
        self,
        bars_h4: list[Bar],
        atr: float,
    ) -> list[DetectedStructure]:
        """Run OB and FVG detectors on H4 bars and tag structures with H4 timeframe."""
        current_bar_index = bars_h4[-1].bar_index

        ob_detector = OrderBlockDetector(
            min_body_atr_mult=self.ob_min_body_atr_mult,
            max_age_bars=self.ob_max_age_bars,
            min_quality=self.ob_min_quality,
        )
        fvg_detector = FairValueGapDetector(
            min_gap_atr_mult=self.fvg_min_gap_atr_mult,
            max_age_bars=self.fvg_max_age_bars,
            min_quality=self.fvg_min_quality,
        )

        ob_structures = ob_detector.detect(bars_h4, atr, current_bar_index=current_bar_index)
        fvg_structures = fvg_detector.detect(bars_h4, atr, current_bar_index=current_bar_index)

        # Tag all H4 structures with H4 timeframe so callers can distinguish them.
        all_structures: list[DetectedStructure] = []
        for s in ob_structures + fvg_structures:
            # DetectedStructure is frozen — rebuild with H4 timeframe if needed.
            if s.timeframe != Timeframe.H4:
                s = DetectedStructure(
                    structure_type=s.structure_type,
                    direction=s.direction,
                    price_high=s.price_high,
                    price_low=s.price_low,
                    quality=s.quality,
                    age_bars=s.age_bars,
                    atr_relative_size=s.atr_relative_size,
                    timeframe=Timeframe.H4,
                    bar_index=s.bar_index,
                    bar_time=s.bar_time,
                    metadata=s.metadata,
                )
            all_structures.append(s)

        return all_structures

    @staticmethod
    def _price_inside_structure(price: float, structure: DetectedStructure) -> bool:
        """Return True if price falls within the structure's price range."""
        return structure.price_low <= price <= structure.price_high
