from __future__ import annotations

from dataclasses import dataclass

from src.core.enums import Direction, SetupClass, StructureType
from src.core.models import DetectedStructure


@dataclass(frozen=True)
class SetupCandidate:
    setup_class: SetupClass
    direction: Direction
    primary_trigger: DetectedStructure
    structural_confirmations: list[DetectedStructure]


SETUP_CATALOG: dict[SetupClass, tuple[StructureType, StructureType]] = {
    SetupClass.OB_WITH_BOS: (StructureType.ORDER_BLOCK, StructureType.BREAK_OF_STRUCTURE),
    SetupClass.OB_WITH_ENGULFING: (StructureType.ORDER_BLOCK, StructureType.ENGULFING),
    SetupClass.OB_WITH_FVG: (StructureType.ORDER_BLOCK, StructureType.FAIR_VALUE_GAP),
    SetupClass.REJECTION_WITH_FVG: (StructureType.REJECTION, StructureType.FAIR_VALUE_GAP),
    SetupClass.SWEEP_WITH_OB: (StructureType.LIQUIDITY_SWEEP, StructureType.ORDER_BLOCK),
    # Judas Sweep primary: Asian range manipulation signal; OB confirms entry zone.
    # The proximity gate applies to the OB in structural_confirmations, same as SWEEP_WITH_OB.
    SetupClass.JUDAS_WITH_OB: (StructureType.JUDAS_SWEEP, StructureType.ORDER_BLOCK),
}


def _candidate_sort_key(item: SetupCandidate) -> tuple[float, float, str]:
    top_confirmation_quality = max((s.quality for s in item.structural_confirmations), default=0.0)
    return (-item.primary_trigger.quality, -top_confirmation_quality, item.setup_class.value)


def match_setup_candidates(structures: list[DetectedStructure], direction: Direction) -> list[SetupCandidate]:
    same_direction = [item for item in structures if item.direction == direction]
    candidates: list[SetupCandidate] = []

    for setup_class, (trigger_type, confirmation_type) in SETUP_CATALOG.items():
        triggers = [item for item in same_direction if item.structure_type == trigger_type]
        confirmations = [item for item in same_direction if item.structure_type == confirmation_type]
        for trigger in triggers:
            for confirmation in confirmations:
                if trigger.bar_index == confirmation.bar_index and trigger.structure_type == confirmation.structure_type:
                    continue
                extra = [
                    item
                    for item in same_direction
                    if item is not trigger and item is not confirmation and item.structure_type != trigger.structure_type
                ]
                ranked_confirmations = sorted(
                    [confirmation, *extra],
                    key=lambda s: (-s.quality, s.age_bars, s.bar_index, s.structure_type.value),
                )
                candidates.append(
                    SetupCandidate(
                        setup_class=setup_class,
                        direction=direction,
                        primary_trigger=trigger,
                        structural_confirmations=ranked_confirmations,
                    )
                )

    return sorted(candidates, key=_candidate_sort_key)
