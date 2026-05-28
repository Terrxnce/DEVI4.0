"""Run SMC-style structure detectors on an arbitrary bar series (e.g. M15 or H1)."""
from __future__ import annotations

import copy
from typing import Any

from src.core.models import Bar, DetectedStructure
from src.detectors.break_of_structure import BreakOfStructureDetector
from src.detectors.engulfing import EngulfingDetector
from src.detectors.fair_value_gap import FairValueGapDetector
from src.detectors.judas_sweep import JudasSweepDetector
from src.detectors.liquidity_sweep import LiquiditySweepDetector
from src.detectors.order_block import OrderBlockDetector
from src.detectors.rejection import RejectionDetector


def scale_detection_cfg_for_higher_tf(detection_cfg: dict[str, Any], mult: float) -> dict[str, Any]:
    """Scale max_age_bars / BOS lookback for higher timeframe (e.g. H1 vs M15)."""
    if mult <= 0:
        mult = 1.0
    d = copy.deepcopy(detection_cfg)
    for key in ("order_block", "fair_value_gap", "sweep", "rejection", "engulfing"):
        block = d.get(key)
        if isinstance(block, dict) and "max_age_bars" in block:
            age = int(block["max_age_bars"])
            block = dict(block)
            block["max_age_bars"] = max(1, int(round(age * mult)))
            d[key] = block
    bos = d.get("break_of_structure")
    if isinstance(bos, dict) and "lookback_bars" in bos:
        lb = int(bos["lookback_bars"])
        bos = dict(bos)
        bos["lookback_bars"] = max(1, int(round(lb * mult)))
        d["break_of_structure"] = bos
    return d


def run_all_detectors(*, detection_cfg: dict[str, Any], bars: list[Bar], atr: float) -> list[DetectedStructure]:
    """Instantiate detectors from ``detection_cfg`` and return all structures for ``bars``."""
    cfg = detection_cfg
    current_idx = bars[-1].bar_index if bars else 0
    structures: list[DetectedStructure] = []

    ob = OrderBlockDetector(
        min_body_atr_mult=cfg["order_block"]["min_body_atr_mult"],
        max_age_bars=cfg["order_block"]["max_age_bars"],
        min_quality=cfg["order_block"]["min_quality"],
    )
    structures.extend(ob.detect(bars, atr, current_idx))

    bos = BreakOfStructureDetector(
        min_swing_atr_mult=cfg["break_of_structure"]["min_swing_atr_mult"],
        lookback_bars=cfg["break_of_structure"]["lookback_bars"],
        min_quality=cfg["break_of_structure"]["min_quality"],
    )
    structures.extend(bos.detect(bars, atr))

    fvg = FairValueGapDetector(
        min_gap_atr_mult=cfg["fair_value_gap"]["min_gap_atr_mult"],
        max_age_bars=cfg["fair_value_gap"]["max_age_bars"],
        min_quality=cfg["fair_value_gap"]["min_quality"],
    )
    structures.extend(fvg.detect(bars, atr, current_idx))

    sweep = LiquiditySweepDetector(
        max_wick_atr_mult=cfg["sweep"]["max_wick_atr_mult"],
        min_wick_body_ratio=cfg["sweep"]["min_wick_body_ratio"],
        max_age_bars=cfg["sweep"]["max_age_bars"],
        min_quality=cfg["sweep"]["min_quality"],
    )
    structures.extend(sweep.detect(bars, atr, current_idx))

    rej = RejectionDetector(
        min_wick_atr_mult=cfg["rejection"]["min_wick_atr_mult"],
        min_wick_body_ratio=cfg["rejection"]["min_wick_body_ratio"],
        max_age_bars=cfg["rejection"]["max_age_bars"],
        min_quality=cfg["rejection"]["min_quality"],
    )
    structures.extend(rej.detect(bars, atr, current_idx))

    eng = EngulfingDetector(
        min_body_atr_mult=cfg["engulfing"]["min_body_atr_mult"],
        max_age_bars=cfg["engulfing"]["max_age_bars"],
        min_quality=cfg["engulfing"]["min_quality"],
    )
    structures.extend(eng.detect(bars, atr, current_idx))

    # Judas Sweep is optional — enabled only when the config key is present and enabled.
    judas_cfg = cfg.get("judas_sweep", {})
    if judas_cfg and bool(judas_cfg.get("enabled", False)):
        judas = JudasSweepDetector(
            max_age_bars=int(judas_cfg.get("max_age_bars", 8)),
            min_quality=float(judas_cfg.get("min_quality", 0.45)),
            sweep_buffer_atr_mult=float(judas_cfg.get("sweep_buffer_atr_mult", 0.1)),
        )
        structures.extend(judas.detect(bars, atr, current_idx))

    return structures
