from __future__ import annotations

from src.execution.structure_detectors import scale_detection_cfg_for_higher_tf


def test_scale_detection_cfg_for_higher_tf_multiplies_ages() -> None:
    base = {
        "atr_period": 14,
        "order_block": {"min_body_atr_mult": 0.5, "max_age_bars": 20, "min_quality": 0.6},
        "break_of_structure": {"min_swing_atr_mult": 1.0, "lookback_bars": 5, "min_quality": 0.5},
        "fair_value_gap": {"min_gap_atr_mult": 0.3, "max_age_bars": 10, "min_quality": 0.5},
        "sweep": {"max_wick_atr_mult": 1.5, "min_wick_body_ratio": 2.0, "max_age_bars": 5, "min_quality": 0.5},
        "rejection": {"min_wick_atr_mult": 0.5, "min_wick_body_ratio": 2.0, "max_age_bars": 3, "min_quality": 0.5},
        "engulfing": {"min_body_atr_mult": 0.3, "max_age_bars": 3, "min_quality": 0.5},
    }
    scaled = scale_detection_cfg_for_higher_tf(base, 2.0)
    assert scaled["order_block"]["max_age_bars"] == 40
    assert scaled["fair_value_gap"]["max_age_bars"] == 20
    assert scaled["break_of_structure"]["lookback_bars"] == 10
    assert base["order_block"]["max_age_bars"] == 20
