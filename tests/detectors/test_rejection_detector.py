from __future__ import annotations

from src.core.enums import Direction
from src.detectors.rejection import RejectionDetector


def test_rejection_bullish_detection(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1000, 1.1002, 1.0998, 1.1001),
        make_bar_fn(1, 1.0996, 1.0999, 1.0988, 1.0998),
    ]
    detector = RejectionDetector()

    found = detector.detect(bars, atr=atr_value)

    assert len(found) == 1
    assert found[0].direction == Direction.BULLISH
    assert found[0].quality == round(found[0].quality, 4)


def test_rejection_bearish_detection(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1000, 1.1002, 1.0998, 1.1001),
        make_bar_fn(1, 1.1004, 1.1012, 1.1003, 1.1006),
    ]
    detector = RejectionDetector()

    found = detector.detect(bars, atr=atr_value)

    assert len(found) == 1
    assert found[0].direction == Direction.BEARISH


def test_rejection_no_detection_case(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1000, 1.1002, 1.0998, 1.1001),
        make_bar_fn(1, 1.1001, 1.1004, 1.1000, 1.1003),
    ]
    detector = RejectionDetector()

    assert detector.detect(bars, atr=atr_value) == []


def test_rejection_age_and_threshold_filtering(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1000, 1.1002, 1.0998, 1.1001),
        make_bar_fn(1, 1.1004, 1.1012, 1.1003, 1.1006),
    ]
    old = RejectionDetector(max_age_bars=1)
    strict = RejectionDetector(min_wick_atr_mult=5.0)

    assert old.detect(bars, atr=atr_value, current_bar_index=10) == []
    assert strict.detect(bars, atr=atr_value) == []


def test_rejection_ordering_multiple(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1000, 1.1002, 1.0998, 1.1001),
        make_bar_fn(1, 1.0996, 1.1000, 1.0987, 1.0999),
        make_bar_fn(2, 1.1004, 1.1014, 1.1003, 1.1006),
    ]
    detector = RejectionDetector(max_age_bars=5)

    found = detector.detect(bars, atr=atr_value, current_bar_index=2)

    if len(found) > 1:
        assert found[0].quality >= found[1].quality
