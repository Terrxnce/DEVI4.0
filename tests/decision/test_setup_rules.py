from __future__ import annotations

from src.core.enums import Direction, SetupClass, StructureType
from src.decision.setup_rules import match_setup_candidates


def test_each_setup_match(make_structure_fn) -> None:
    structures = [
        make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, bar_index=1),
        make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BULLISH, bar_index=2),
        make_structure_fn(StructureType.ENGULFING, Direction.BULLISH, bar_index=3),
        make_structure_fn(StructureType.FAIR_VALUE_GAP, Direction.BULLISH, bar_index=4),
        make_structure_fn(StructureType.REJECTION, Direction.BULLISH, bar_index=5),
        make_structure_fn(StructureType.LIQUIDITY_SWEEP, Direction.BULLISH, bar_index=6),
    ]

    candidates = match_setup_candidates(structures, Direction.BULLISH)
    classes = {c.setup_class for c in candidates}

    assert SetupClass.OB_WITH_BOS in classes
    assert SetupClass.OB_WITH_ENGULFING in classes
    assert SetupClass.OB_WITH_FVG in classes
    assert SetupClass.REJECTION_WITH_FVG in classes
    assert SetupClass.SWEEP_WITH_OB in classes


def test_direction_mismatch_prevents_setup(make_structure_fn) -> None:
    structures = [
        make_structure_fn(StructureType.ORDER_BLOCK, Direction.BULLISH, bar_index=1),
        make_structure_fn(StructureType.BREAK_OF_STRUCTURE, Direction.BEARISH, bar_index=2),
    ]

    candidates = match_setup_candidates(structures, Direction.BULLISH)

    assert candidates == []
