from __future__ import annotations

from dataclasses import dataclass

from src.context.session_levels import SessionLevels, SessionSweep
from src.core.enums import Direction, SetupClass, StructureType
from src.core.models import Bar, DetectedStructure
from src.narrative.choch_detector import CHoCHDetector, CHoCHResult


@dataclass(frozen=True)
class NarrativeContext:
    """Output of NarrativeBuilder.evaluate().

    A complete narrative requires all four sequence steps:
      1. Sweep     - a prior-session extreme was taken out and rejected
      2. FVG       - a Fair Value Gap was left by displacement after the sweep
      3. Retrace   - price has retraced back into that FVG zone
      4. CHoCH     - (optional but scored higher) a BOS confirms structural shift

    sequence_complete=True means steps 1+2+3 are all confirmed.
    choch adds conviction but is not required for sequence_complete.
    """

    direction: Direction
    sweep: SessionSweep
    fvg_zone: DetectedStructure
    choch: CHoCHResult
    retrace_confirmed: bool
    sequence_complete: bool
    quality: float


@dataclass(frozen=True)
class NarrativeBuilder:
    """Evaluates whether the current M15 bar state satisfies a sweep reversal narrative.

    Sequence checked:
    BULLISH sweep reversal:
      [1] session_levels.sweep.direction == BULLISH
      [2] A BULLISH FVG exists with bar_index >= sweep bar_index
      [3] current_price is inside the FVG zone (retrace into entry zone)
      [4] (optional) A BULLISH BOS after the sweep (CHoCH confirmation)

    BEARISH sweep reversal: mirror logic.
    """

    min_fvg_quality: float = 0.35
    retrace_tolerance_atr: float = 0.15

    def evaluate(
        self,
        session_levels: SessionLevels,
        structures: list[DetectedStructure],
        current_price: float,
        atr: float,
    ) -> NarrativeContext | None:
        sweep = session_levels.sweep
        if sweep is None:
            return None

        direction = sweep.direction
        if direction == Direction.NEUTRAL:
            return None

        fvg_zone = self._find_post_sweep_fvg(
            structures=structures,
            direction=direction,
            sweep_bar_index=sweep.bar_index,
        )
        if fvg_zone is None:
            return None

        retrace = self._retrace_confirmed(
            current_price=current_price,
            fvg=fvg_zone,
            atr=atr,
        )

        choch_detector = CHoCHDetector()
        choch = choch_detector.detect(
            structures=structures,
            sweep_bar_index=sweep.bar_index,
            direction=direction,
        )

        sequence_complete = retrace

        quality = self._score_narrative(
            fvg=fvg_zone,
            choch=choch,
            retrace=retrace,
        )

        return NarrativeContext(
            direction=direction,
            sweep=sweep,
            fvg_zone=fvg_zone,
            choch=choch,
            retrace_confirmed=retrace,
            sequence_complete=sequence_complete,
            quality=quality,
        )

    def _find_post_sweep_fvg(
        self,
        structures: list[DetectedStructure],
        direction: Direction,
        sweep_bar_index: int,
    ) -> DetectedStructure | None:
        candidates = [
            s for s in structures
            if s.structure_type == StructureType.FAIR_VALUE_GAP
            and s.direction == direction
            and s.bar_index >= sweep_bar_index
            and s.quality >= self.min_fvg_quality
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda s: (-s.quality, s.age_bars))[0]

    def _retrace_confirmed(
        self,
        current_price: float,
        fvg: DetectedStructure,
        atr: float,
    ) -> bool:
        tolerance = self.retrace_tolerance_atr * atr
        return (fvg.price_low - tolerance) <= current_price <= (fvg.price_high + tolerance)

    @staticmethod
    def _score_narrative(
        fvg: DetectedStructure,
        choch: CHoCHResult,
        retrace: bool,
    ) -> float:
        fvg_score = fvg.quality * 0.50
        choch_score = 0.30 if choch.detected else 0.0
        retrace_score = 0.20 if retrace else 0.0
        raw = fvg_score + choch_score + retrace_score
        return round(min(max(raw, 0.0), 1.0), 4)


def narrative_to_candidate(
    narrative: NarrativeContext,
    structures: list[DetectedStructure],
) -> "SetupCandidate":
    """Convert a complete NarrativeContext into a SetupCandidate for the confluence engine.

    Primary trigger: the post-sweep FVG zone (entry zone)
    Structural confirmations (priority order):
      1. CHoCH BOS structure  - if detected (confirms character shift)
      2. LIQUIDITY_SWEEP structure close to the swept level - if found

    Setup class:
      SWEEP_REVERSAL_BULL for Direction.BULLISH
      SWEEP_REVERSAL_BEAR for Direction.BEARISH

    Tier depends on structural count:
      FVG + CHoCH + sweep structure = 3 -> Tier A (if config allows)
      FVG + CHoCH                   = 2 -> Tier B
      FVG only                      = 1 -> Tier C
    """
    from src.decision.setup_rules import SetupCandidate

    setup_class = (
        SetupClass.SWEEP_REVERSAL_BULL
        if narrative.direction == Direction.BULLISH
        else SetupClass.SWEEP_REVERSAL_BEAR
    )

    confirmations: list[DetectedStructure] = []

    if narrative.choch.detected and narrative.choch.structure is not None:
        confirmations.append(narrative.choch.structure)

    sweep_level = narrative.sweep.swept_level
    fvg_width = narrative.fvg_zone.price_high - narrative.fvg_zone.price_low
    proximity_buffer = max(fvg_width * 2.0, 0.0005)

    for s in structures:
        if s.structure_type != StructureType.LIQUIDITY_SWEEP:
            continue
        if s.direction != narrative.direction:
            continue
        center = (s.price_low + s.price_high) / 2.0
        if abs(center - sweep_level) <= proximity_buffer:
            confirmations.append(s)
            break

    return SetupCandidate(
        setup_class=setup_class,
        direction=narrative.direction,
        primary_trigger=narrative.fvg_zone,
        structural_confirmations=confirmations,
    )
