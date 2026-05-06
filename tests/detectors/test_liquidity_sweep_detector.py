from __future__ import annotations

from src.core.enums import Direction
from src.detectors.liquidity_sweep import LiquiditySweepDetector


def test_extrema_without_valid_swing_point_is_ignored(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1002, 1.1004, 1.0980, 1.1001),
        make_bar_fn(1, 1.1001, 1.1003, 1.0990, 1.0999),
        make_bar_fn(2, 1.0999, 1.1002, 1.0992, 1.1000),
        make_bar_fn(3, 1.1000, 1.1002, 1.0991, 1.1001),
        make_bar_fn(4, 1.0984, 1.0985, 1.0978, 1.0982),
    ]
    detector = LiquiditySweepDetector()

    assert detector.detect(bars, atr=atr_value) == []


def test_sweep_bullish_from_prior_swing_low(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1002, 1.1004, 1.0998, 1.1000),
        make_bar_fn(1, 1.1000, 1.1002, 1.0994, 1.0998),
        make_bar_fn(2, 1.0998, 1.1000, 1.0990, 1.0997),
        make_bar_fn(3, 1.0997, 1.1001, 1.0995, 1.1000),
        make_bar_fn(4, 1.0993, 1.0994, 1.0986, 1.0992),
    ]
    detector = LiquiditySweepDetector()

    found = detector.detect(bars, atr=atr_value)

    assert len(found) == 1
    assert found[0].direction == Direction.BULLISH
    assert found[0].quality == round(found[0].quality, 4)


def test_sweep_bearish_from_prior_swing_high(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1000, 1.1003, 1.0998, 1.1001),
        make_bar_fn(1, 1.1001, 1.1007, 1.0999, 1.1002),
        make_bar_fn(2, 1.1002, 1.1010, 1.1000, 1.1003),
        make_bar_fn(3, 1.1003, 1.1006, 1.1001, 1.1002),
        make_bar_fn(4, 1.1008, 1.1014, 1.1007, 1.1009),
    ]
    detector = LiquiditySweepDetector()

    found = detector.detect(bars, atr=atr_value)

    assert len(found) == 1
    assert found[0].direction == Direction.BEARISH


def test_sweep_invalid_close_back_through_level_fails(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1002, 1.1004, 1.0998, 1.1000),
        make_bar_fn(1, 1.1000, 1.1002, 1.0994, 1.0998),
        make_bar_fn(2, 1.0998, 1.1000, 1.0990, 1.0997),
        make_bar_fn(3, 1.0997, 1.1001, 1.0995, 1.1000),
        make_bar_fn(4, 1.0993, 1.0994, 1.0986, 1.0989),
    ]
    detector = LiquiditySweepDetector()

    assert detector.detect(bars, atr=atr_value) == []


def test_sweep_age_filtering_still_works(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1002, 1.1004, 1.0998, 1.1000),
        make_bar_fn(1, 1.1000, 1.1002, 1.0994, 1.0998),
        make_bar_fn(2, 1.0998, 1.1000, 1.0990, 1.0997),
        make_bar_fn(3, 1.0997, 1.1001, 1.0995, 1.1000),
        make_bar_fn(4, 1.0993, 1.0994, 1.0986, 1.0992),
    ]
    old = LiquiditySweepDetector(max_age_bars=1)

    assert old.detect(bars, atr=atr_value, current_bar_index=20) == []


def test_sweep_deterministic_ordering_still_works(atr_value: float, make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1004, 1.1006, 1.1002, 1.1005),
        make_bar_fn(1, 1.1005, 1.1007, 1.0996, 1.1002),
        make_bar_fn(2, 1.1002, 1.1004, 1.1000, 1.1003),
        make_bar_fn(3, 1.1003, 1.1004, 1.0992, 1.1001),
        make_bar_fn(4, 1.1001, 1.1002, 1.0999, 1.1000),
        make_bar_fn(5, 1.1000, 1.1002, 1.1001, 1.10015),
        make_bar_fn(6, 1.0998, 1.0999, 1.0988, 1.0997),
    ]
    detector = LiquiditySweepDetector(max_wick_atr_mult=2.0)

    first = detector.detect(bars, atr=atr_value)
    second = detector.detect(bars, atr=atr_value)

    assert len(first) >= 2
    assert [(f.direction, f.quality, f.age_bars, f.metadata["prior_swing_bar_index"]) for f in first] == [
        (f.direction, f.quality, f.age_bars, f.metadata["prior_swing_bar_index"]) for f in second
    ]
