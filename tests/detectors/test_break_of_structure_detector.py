from __future__ import annotations

from src.core.enums import Direction
from src.detectors.break_of_structure import BreakOfStructureDetector


def test_bos_bullish_detection(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.0998, 1.1000, 1.0995, 1.0999),
        make_bar_fn(1, 1.0999, 1.1006, 1.0998, 1.1004),
        make_bar_fn(2, 1.1004, 1.1002, 1.0999, 1.1000),
        make_bar_fn(3, 1.1000, 1.1014, 1.0999, 1.1012),
    ]
    detector = BreakOfStructureDetector()

    found = detector.detect(bars, atr=atr_value)

    assert len(found) == 1
    assert found[0].direction == Direction.BULLISH
    assert found[0].quality == round(found[0].quality, 4)


def test_bos_bearish_detection(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1002, 1.1005, 1.1000, 1.1001),
        make_bar_fn(1, 1.1001, 1.1002, 1.0992, 1.0994),
        make_bar_fn(2, 1.0994, 1.0998, 1.0995, 1.0997),
        make_bar_fn(3, 1.0997, 1.0999, 1.0984, 1.0986),
    ]
    detector = BreakOfStructureDetector()

    found = detector.detect(bars, atr=atr_value)

    assert len(found) == 1
    assert found[0].direction == Direction.BEARISH


def test_bos_no_detection_case(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1000, 1.1002, 1.0998, 1.1001),
        make_bar_fn(1, 1.1001, 1.1003, 1.0999, 1.1002),
        make_bar_fn(2, 1.1002, 1.1004, 1.1000, 1.1003),
        make_bar_fn(3, 1.1003, 1.1005, 1.1001, 1.1004),
    ]
    detector = BreakOfStructureDetector()

    assert detector.detect(bars, atr=atr_value) == []


def test_bos_threshold_filtering(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.0998, 1.1000, 1.0995, 1.0999),
        make_bar_fn(1, 1.0999, 1.1006, 1.0998, 1.1004),
        make_bar_fn(2, 1.1004, 1.1002, 1.0999, 1.1000),
        make_bar_fn(3, 1.1000, 1.1014, 1.0999, 1.1012),
    ]
    strict = BreakOfStructureDetector(min_swing_atr_mult=5.0)

    assert strict.detect(bars, atr=atr_value) == []


def test_bos_ordering_multiple_structures(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.0996, 1.1002, 1.0993, 1.1000),
        make_bar_fn(1, 1.1000, 1.1008, 1.0997, 1.1006),
        make_bar_fn(2, 1.1006, 1.1004, 1.0999, 1.1001),
        make_bar_fn(3, 1.1001, 1.1010, 1.1000, 1.1008),
        make_bar_fn(4, 1.1008, 1.1007, 1.1000, 1.1002),
        make_bar_fn(5, 1.1002, 1.1018, 1.1001, 1.1016),
    ]
    detector = BreakOfStructureDetector(lookback_bars=20)

    found = detector.detect(bars, atr=atr_value)

    assert len(found) >= 2
    assert found[0].quality >= found[1].quality
