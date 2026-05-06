from __future__ import annotations

from datetime import UTC, datetime

from src.context.references import compute_reference_levels


def test_prior_day_high_low(make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.10, 1.12, 1.09, 1.11, timestamp=datetime(2026, 4, 29, 10, 0, tzinfo=UTC)),
        make_bar_fn(1, 1.11, 1.13, 1.08, 1.10, timestamp=datetime(2026, 4, 29, 11, 0, tzinfo=UTC)),
        make_bar_fn(2, 1.10, 1.14, 1.07, 1.12, timestamp=datetime(2026, 4, 30, 8, 0, tzinfo=UTC)),
    ]

    refs = compute_reference_levels(bars)

    assert refs.prior_day_high == 1.13
    assert refs.prior_day_low == 1.08


def test_prior_session_high_low(make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.10, 1.11, 1.09, 1.10, timestamp=datetime(2026, 4, 30, 6, 0, tzinfo=UTC)),
        make_bar_fn(1, 1.10, 1.14, 1.08, 1.12, timestamp=datetime(2026, 4, 30, 6, 30, tzinfo=UTC)),
        make_bar_fn(2, 1.12, 1.13, 1.11, 1.12, timestamp=datetime(2026, 4, 30, 8, 0, tzinfo=UTC)),
    ]

    refs = compute_reference_levels(bars)

    assert refs.prior_session_high == 1.14
    assert refs.prior_session_low == 1.08


def test_prominent_swing_detection(make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1000, 1.1002, 1.0996, 1.0999),
        make_bar_fn(1, 1.0999, 1.1004, 1.0995, 1.1000),
        make_bar_fn(2, 1.1000, 1.1006, 1.0994, 1.1001),
        make_bar_fn(3, 1.1001, 1.1012, 1.0988, 1.1003),
        make_bar_fn(4, 1.1003, 1.1007, 1.0993, 1.1002),
        make_bar_fn(5, 1.1002, 1.1005, 1.0995, 1.1001),
        make_bar_fn(6, 1.1001, 1.1003, 1.0997, 1.1000),
        make_bar_fn(7, 1.1000, 1.1001, 1.0998, 1.0999),
    ]

    refs = compute_reference_levels(bars)

    assert refs.prominent_swing_high == 1.1012
    assert refs.prominent_swing_low == 1.0988


def test_one_bar_fractal_alone_is_not_prominent_swing(make_bar_fn) -> None:
    bars = [
        make_bar_fn(0, 1.1000, 1.1002, 1.0998, 1.1000),
        make_bar_fn(1, 1.1000, 1.1010, 1.0997, 1.1001),
        make_bar_fn(2, 1.1001, 1.1005, 1.0998, 1.1002),
        make_bar_fn(3, 1.1002, 1.1004, 1.0999, 1.1001),
        make_bar_fn(4, 1.1001, 1.1003, 1.1000, 1.1002),
        make_bar_fn(5, 1.1002, 1.1004, 1.1000, 1.1003),
        make_bar_fn(6, 1.1003, 1.1005, 1.1001, 1.1004),
        make_bar_fn(7, 1.1004, 1.1006, 1.1002, 1.1005),
    ]

    refs = compute_reference_levels(bars)

    assert refs.prominent_swing_high is None
