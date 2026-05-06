from __future__ import annotations

from src.core.enums import Direction
from src.detectors.fair_value_gap import FairValueGapDetector


def test_fvg_bullish_detection(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1000, 1.1001, 1.0998, 1.1000),
        make_bar_fn(1, 1.1000, 1.1007, 1.0999, 1.1006),
        make_bar_fn(2, 1.1010, 1.1014, 1.1005, 1.1012),
    ]
    detector = FairValueGapDetector()

    found = detector.detect(bars, atr=atr_value)

    assert len(found) == 1
    assert found[0].direction == Direction.BULLISH
    assert found[0].quality == round(found[0].quality, 4)


def test_fvg_bearish_detection(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1008, 1.1010, 1.1004, 1.1006),
        make_bar_fn(1, 1.1006, 1.1007, 1.0998, 1.0999),
        make_bar_fn(2, 1.0995, 1.0997, 1.0990, 1.0992),
    ]
    detector = FairValueGapDetector()

    found = detector.detect(bars, atr=atr_value)

    assert len(found) == 1
    assert found[0].direction == Direction.BEARISH


def test_fvg_no_detection_case(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1000, 1.1003, 1.0998, 1.1002),
        make_bar_fn(1, 1.1002, 1.1004, 1.1000, 1.1003),
        make_bar_fn(2, 1.1003, 1.1005, 1.1001, 1.1004),
    ]
    detector = FairValueGapDetector()

    assert detector.detect(bars, atr=atr_value) == []


def test_fvg_age_filtering(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1000, 1.1001, 1.0998, 1.1000),
        make_bar_fn(1, 1.1000, 1.1007, 1.0999, 1.1006),
        make_bar_fn(2, 1.1010, 1.1014, 1.1005, 1.1012),
    ]
    detector = FairValueGapDetector(max_age_bars=1)

    assert detector.detect(bars, atr=atr_value, current_bar_index=10) == []


def test_fvg_threshold_and_ordering(atr_value: float, make_bar_fn, clone_with_index_fn) -> None:
    a = [
        make_bar_fn(0, 1.1000, 1.1001, 1.0998, 1.1000),
        make_bar_fn(1, 1.1000, 1.1008, 1.0999, 1.1007),
        make_bar_fn(2, 1.1010, 1.1014, 1.1006, 1.1012),
    ]
    b = [clone_with_index_fn(x, x.bar_index + 3) for x in a]
    bars = a + b

    detector = FairValueGapDetector(min_gap_atr_mult=0.3)
    found = detector.detect(bars, atr=atr_value)
    assert len(found) >= 2
    assert found[0].quality >= found[1].quality

    strict = FairValueGapDetector(min_gap_atr_mult=5.0)
    assert strict.detect(bars, atr=atr_value) == []
