from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.core.enums import Direction, StructureType
from src.core.models import DetectedStructure


@dataclass(frozen=True)
class CHoCHResult:
    """Result of a Change-of-Character detection pass.

    A CHoCH (Change of Character) is a Break of Structure that occurs
    specifically AFTER a liquidity sweep in the same direction as the reversal.

    This is a stronger confirmation than a plain BOS because it means:
      1. Liquidity was taken (the sweep)
      2. Smart money absorbed the stop-run
      3. Price shifted character with a structural break

    For a BULLISH sweep reversal:
      - Sweep was BULLISH (price swept a low)
      - CHoCH is a BULLISH BOS (price broke a prior swing high) after the sweep
      - Confirms the structural shift from bearish to bullish

    For a BEARISH sweep reversal:
      - Sweep was BEARISH (price swept a high)
      - CHoCH is a BEARISH BOS (price broke a prior swing low) after the sweep
      - Confirms the structural shift from bullish to bearish
    """

    detected: bool
    direction: Direction | None
    structure: DetectedStructure | None  # the BOS structure that qualified as CHoCH
    bar_index: int | None
    bar_time: datetime | None
    reason: str


@dataclass(frozen=True)
class CHoCHDetector:
    """Identifies Change-of-Character events from already-detected structures.

    This detector does NOT re-run the BOS detector — it filters the existing
    detected structures list. The BOS detector must have already run over the
    M15 bars before calling this.

    Design rationale: keeping this as a filter over existing structures means:
    - No duplicate detector runs
    - No additional bar-window parameters to calibrate
    - CHoCH is a classification of an existing BOS, not a new structure type
    """

    min_bos_quality: float = 0.35

    def detect(
        self,
        structures: list[DetectedStructure],
        sweep_bar_index: int,
        direction: Direction,
    ) -> CHoCHResult:
        """Check whether a qualifying BOS exists after a sweep event.

        Args:
            structures:       List of DetectedStructure objects (M15 timeframe).
            sweep_bar_index:  The bar_index of the bar where the sweep occurred.
            direction:        The expected reversal direction (BULLISH or BEARISH).
                              A BULLISH reversal requires a BULLISH BOS after the sweep.

        Returns:
            CHoCHResult with detected=True if a qualifying BOS was found.
        """
        if direction == Direction.NEUTRAL:
            return CHoCHResult(
                detected=False,
                direction=None,
                structure=None,
                bar_index=None,
                bar_time=None,
                reason="direction_neutral",
            )

        qualifying: list[DetectedStructure] = []
        for s in structures:
            if s.structure_type != StructureType.BREAK_OF_STRUCTURE:
                continue
            if s.direction != direction:
                continue
            if s.bar_index <= sweep_bar_index:
                continue
            if s.quality < self.min_bos_quality:
                continue
            qualifying.append(s)

        if not qualifying:
            return CHoCHResult(
                detected=False,
                direction=direction,
                structure=None,
                bar_index=None,
                bar_time=None,
                reason="no_bos_after_sweep",
            )

        # Use the highest-quality qualifying BOS.
        best = sorted(qualifying, key=lambda s: (-s.quality, s.bar_index))[0]

        return CHoCHResult(
            detected=True,
            direction=direction,
            structure=best,
            bar_index=best.bar_index,
            bar_time=best.bar_time,
            reason="bos_confirmed_after_sweep",
        )
