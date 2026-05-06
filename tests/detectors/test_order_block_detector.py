from __future__ import annotations

from src.core.enums import Direction
from src.detectors.order_block import OrderBlockDetector


def test_order_block_bullish_detection(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1000, 1.1002, 1.0998, 1.1001),
        make_bar_fn(1, 1.1000, 1.1001, 1.0994, 1.0995),
        make_bar_fn(2, 1.0995, 1.1005, 1.0994, 1.1003),
    ]
    detector = OrderBlockDetector()

    found = detector.detect(bars, atr=atr_value)

    assert len(found) == 1
    assert found[0].direction == Direction.BULLISH
    assert found[0].quality == round(found[0].quality, 4)


def test_order_block_bearish_detection(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1000, 1.1002, 1.0998, 1.1001),
        make_bar_fn(1, 1.1000, 1.1007, 1.0999, 1.1006),
        make_bar_fn(2, 1.1006, 1.1007, 1.0996, 1.0999),
    ]
    detector = OrderBlockDetector()

    found = detector.detect(bars, atr=atr_value)

    assert len(found) == 1
    assert found[0].direction == Direction.BEARISH


def test_order_block_no_detection_case(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1000, 1.1002, 1.0998, 1.1001),
        make_bar_fn(1, 1.1001, 1.1003, 1.1000, 1.1002),
        make_bar_fn(2, 1.1002, 1.1004, 1.1001, 1.1003),
    ]
    detector = OrderBlockDetector()

    found = detector.detect(bars, atr=atr_value)

    assert found == []


def test_order_block_age_filtering(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1000, 1.1002, 1.0998, 1.1001),
        make_bar_fn(1, 1.1000, 1.1001, 1.0994, 1.0995),
        make_bar_fn(2, 1.0995, 1.1005, 1.0994, 1.1003),
    ]
    detector = OrderBlockDetector(max_age_bars=1)

    found = detector.detect(bars, atr=atr_value, current_bar_index=10)

    assert found == []


def test_order_block_threshold_and_ordering(atr_value: float, make_bar_fn, clone_with_index_fn) -> None:
    pattern_a = [
        make_bar_fn(0, 1.1000, 1.1002, 1.0998, 1.1001),
        make_bar_fn(1, 1.1000, 1.1001, 1.0994, 1.0995),
        make_bar_fn(2, 1.0995, 1.1007, 1.0994, 1.1005),
    ]
    pattern_b = [clone_with_index_fn(b, b.bar_index + 3) for b in pattern_a]
    bars = pattern_a + pattern_b

    detector = OrderBlockDetector(min_body_atr_mult=0.4)
    found = detector.detect(bars, atr=atr_value)

    assert len(found) >= 2
    for item in found:
        assert item.quality == round(item.quality, 4)

    assert found[0].quality >= found[1].quality

    strict = OrderBlockDetector(min_body_atr_mult=5.0)
    assert strict.detect(bars, atr=atr_value) == []
