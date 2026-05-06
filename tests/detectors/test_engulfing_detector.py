from __future__ import annotations

from src.core.enums import Direction
from src.detectors.engulfing import EngulfingDetector


def test_engulfing_bullish_detection(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1005, 1.1006, 1.0997, 1.1000),
        make_bar_fn(1, 1.0998, 1.1008, 1.0996, 1.1007),
    ]
    detector = EngulfingDetector()

    found = detector.detect(bars, atr=atr_value)

    assert len(found) == 1
    assert found[0].direction == Direction.BULLISH
    assert found[0].quality == round(found[0].quality, 4)


def test_engulfing_bearish_detection(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1000, 1.1007, 1.0999, 1.1006),
        make_bar_fn(1, 1.1007, 1.1008, 1.0997, 1.0998),
    ]
    detector = EngulfingDetector()

    found = detector.detect(bars, atr=atr_value)

    assert len(found) == 1
    assert found[0].direction == Direction.BEARISH


def test_engulfing_no_detection_case(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1000, 1.1004, 1.0999, 1.1002),
        make_bar_fn(1, 1.1002, 1.1005, 1.1001, 1.1003),
    ]
    detector = EngulfingDetector()

    assert detector.detect(bars, atr=atr_value) == []


def test_engulfing_age_and_threshold_filtering(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1005, 1.1006, 1.0997, 1.1000),
        make_bar_fn(1, 1.0998, 1.1008, 1.0996, 1.1007),
    ]
    old = EngulfingDetector(max_age_bars=1)
    strict = EngulfingDetector(min_body_atr_mult=5.0)

    assert old.detect(bars, atr=atr_value, current_bar_index=10) == []
    assert strict.detect(bars, atr=atr_value) == []


def test_engulfing_ordering_multiple(atr_value: float, make_bar_fn, clone_with_index_fn) -> None:
    pattern = [
        make_bar_fn(0, 1.1005, 1.1006, 1.0997, 1.1000),
        make_bar_fn(1, 1.0998, 1.1008, 1.0996, 1.1007),
    ]
    bars = pattern + [clone_with_index_fn(b, b.bar_index + 2) for b in pattern]

    detector = EngulfingDetector()
    found = detector.detect(bars, atr=atr_value)

    assert len(found) >= 2
    assert found[0].quality >= found[1].quality
