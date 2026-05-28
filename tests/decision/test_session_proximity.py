"""Tests for session proximity gate.

Covers:
- minutes_until_session_end: correct minutes returned, None when outside session
- evaluate_session_proximity: gate disabled, passes with enough time, blocks near end
- Engine integration: near_session_end failure code returned from evaluate_decision
- Config: live_market_watch.json has correct JPY session assignments
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.decision.session_proximity import evaluate_session_proximity, minutes_until_session_end

UTC = timezone.utc

# ---------------------------------------------------------------------------
# Minimal config fixture
# ---------------------------------------------------------------------------

_SESSIONS = {
    "ASIA":   {"start": "00:00", "end": "06:00"},
    "LONDON": {"start": "07:00", "end": "11:30"},
    "NY_AM":  {"start": "13:00", "end": "16:00"},
    "NY_PM":  {"start": "16:00", "end": "19:00"},
}

_SYMBOL_SESSIONS = {
    "default": ["ASIA", "LONDON", "NY_AM", "NY_PM"],
    "GBPJPY":  ["LONDON"],
    "CHFJPY":  ["LONDON"],
    "EURJPY":  ["LONDON", "NY_AM"],
    "NZDUSD":  ["ASIA", "LONDON"],
}


def _cfg(enabled: bool = True, minutes: int = 60) -> dict:
    return {
        "sessions": _SESSIONS,
        "symbol_sessions": _SYMBOL_SESSIONS,
        "entry_gate": {
            "session_proximity_gate_enabled": enabled,
            "session_proximity_gate_minutes": minutes,
        },
    }


# ---------------------------------------------------------------------------
# minutes_until_session_end
# ---------------------------------------------------------------------------

class TestMinutesUntilSessionEnd:

    def test_mid_london_returns_minutes_remaining(self):
        now = datetime(2026, 5, 28, 9, 0, tzinfo=UTC)
        assert minutes_until_session_end("GBPJPY", now, _cfg()) == 150

    def test_near_london_end_returns_small_value(self):
        now = datetime(2026, 5, 28, 11, 15, tzinfo=UTC)
        assert minutes_until_session_end("GBPJPY", now, _cfg()) == 15

    def test_exactly_at_session_start_returns_full_window(self):
        now = datetime(2026, 5, 28, 7, 0, tzinfo=UTC)
        assert minutes_until_session_end("GBPJPY", now, _cfg()) == 270

    def test_exactly_at_session_end_returns_none(self):
        now = datetime(2026, 5, 28, 11, 30, tzinfo=UTC)
        assert minutes_until_session_end("GBPJPY", now, _cfg()) is None

    def test_between_sessions_returns_none(self):
        now = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)
        assert minutes_until_session_end("GBPJPY", now, _cfg()) is None

    def test_eurjpy_in_ny_am_returns_minutes(self):
        now = datetime(2026, 5, 28, 14, 0, tzinfo=UTC)
        assert minutes_until_session_end("EURJPY", now, _cfg()) == 120

    def test_eurjpy_in_london_returns_minutes(self):
        now = datetime(2026, 5, 28, 9, 0, tzinfo=UTC)
        assert minutes_until_session_end("EURJPY", now, _cfg()) == 150

    def test_gbpjpy_not_in_ny_am(self):
        now = datetime(2026, 5, 28, 14, 0, tzinfo=UTC)
        assert minutes_until_session_end("GBPJPY", now, _cfg()) is None

    def test_asia_session_mid_window(self):
        now = datetime(2026, 5, 28, 3, 0, tzinfo=UTC)
        assert minutes_until_session_end("NZDUSD", now, _cfg()) == 180

    def test_default_symbol_uses_default_sessions(self):
        now = datetime(2026, 5, 28, 9, 0, tzinfo=UTC)
        assert minutes_until_session_end("EURUSD", now, _cfg()) == 150


# ---------------------------------------------------------------------------
# evaluate_session_proximity
# ---------------------------------------------------------------------------

class TestEvaluateSessionProximity:

    def test_gate_disabled_always_passes(self):
        now = datetime(2026, 5, 28, 11, 15, tzinfo=UTC)
        passes, code = evaluate_session_proximity("GBPJPY", now, _cfg(enabled=False))
        assert passes is True
        assert code == ""

    def test_passes_with_plenty_of_time(self):
        now = datetime(2026, 5, 28, 9, 0, tzinfo=UTC)
        passes, code = evaluate_session_proximity("GBPJPY", now, _cfg(enabled=True, minutes=60))
        assert passes is True
        assert code == ""

    def test_blocks_within_threshold(self):
        now = datetime(2026, 5, 28, 11, 15, tzinfo=UTC)
        passes, code = evaluate_session_proximity("GBPJPY", now, _cfg(enabled=True, minutes=60))
        assert passes is False
        assert code == "near_session_end"

    def test_passes_exactly_at_threshold(self):
        # 10:30 UTC — exactly 60 min left. Gate uses < so 60 < 60 = False → passes.
        now = datetime(2026, 5, 28, 10, 30, tzinfo=UTC)
        passes, code = evaluate_session_proximity("GBPJPY", now, _cfg(enabled=True, minutes=60))
        assert passes is True
        assert code == ""

    def test_blocks_one_minute_below_threshold(self):
        # 10:31 UTC — 59 min left, threshold 60 → blocked
        now = datetime(2026, 5, 28, 10, 31, tzinfo=UTC)
        passes, code = evaluate_session_proximity("GBPJPY", now, _cfg(enabled=True, minutes=60))
        assert passes is False
        assert code == "near_session_end"

    def test_passes_one_minute_above_threshold(self):
        now = datetime(2026, 5, 28, 10, 29, tzinfo=UTC)
        passes, code = evaluate_session_proximity("GBPJPY", now, _cfg(enabled=True, minutes=60))
        assert passes is True
        assert code == ""

    def test_not_in_session_passes_through(self):
        now = datetime(2026, 5, 28, 14, 0, tzinfo=UTC)
        passes, code = evaluate_session_proximity("GBPJPY", now, _cfg(enabled=True, minutes=60))
        assert passes is True
        assert code == ""

    def test_eurjpy_ny_am_near_end_blocked(self):
        now = datetime(2026, 5, 28, 15, 30, tzinfo=UTC)
        passes, code = evaluate_session_proximity("EURJPY", now, _cfg(enabled=True, minutes=60))
        assert passes is False
        assert code == "near_session_end"

    def test_eurjpy_ny_am_early_passes(self):
        now = datetime(2026, 5, 28, 13, 0, tzinfo=UTC)
        passes, code = evaluate_session_proximity("EURJPY", now, _cfg(enabled=True, minutes=60))
        assert passes is True
        assert code == ""

    def test_custom_threshold_30_blocks_at_25_min(self):
        now = datetime(2026, 5, 28, 11, 5, tzinfo=UTC)
        passes, code = evaluate_session_proximity("GBPJPY", now, _cfg(enabled=True, minutes=30))
        assert passes is False
        assert code == "near_session_end"

    def test_custom_threshold_30_passes_at_40_min(self):
        now = datetime(2026, 5, 28, 10, 50, tzinfo=UTC)
        passes, code = evaluate_session_proximity("GBPJPY", now, _cfg(enabled=True, minutes=30))
        assert passes is True
        assert code == ""


# ---------------------------------------------------------------------------
# Engine integration: evaluate_decision returns near_session_end
# ---------------------------------------------------------------------------

def _make_structures(bar_time: datetime):
    from src.core.enums import Direction, StructureType, Timeframe
    from src.core.models import DetectedStructure
    ob = DetectedStructure(
        structure_type=StructureType.ORDER_BLOCK,
        direction=Direction.BEARISH,
        price_high=213.700,
        price_low=213.500,
        quality=0.75,
        age_bars=2,
        atr_relative_size=1.0,
        timeframe=Timeframe.M15,
        bar_index=0,
        bar_time=bar_time,
    )
    bos = DetectedStructure(
        structure_type=StructureType.BREAK_OF_STRUCTURE,
        direction=Direction.BEARISH,
        price_high=213.600,
        price_low=213.400,
        quality=0.70,
        age_bars=3,
        atr_relative_size=1.0,
        timeframe=Timeframe.M15,
        bar_index=1,
        bar_time=bar_time,
    )
    return [ob, bos]


def _make_context(bar_time: datetime):
    from src.core.enums import Direction, HTFAgreement, Regime, Session
    from src.core.models import ContextSnapshot
    return ContextSnapshot(
        symbol="GBPJPY",
        bar_time=bar_time,
        session=Session.LONDON,
        micro_window=False,
        trend_m15=Direction.BEARISH,
        trend_h1=Direction.BEARISH,
        htf_agreement=HTFAgreement.AGREES,
        regime=Regime.TRENDING,
        atr_current=0.010,
        atr_percentile=0.6,
        spread_atr_ratio=0.05,
        stale_entry=False,
        news_blocked=False,
        nearby_structures=[],
    )


def _make_config():
    return {
        "pipeline": {"enable_full_phase1_flow": True},
        "confluence": {
            "tier_a_min_confirmations": 2,
            "tier_b_min_confirmations": 1,
            "tier_c_min_confirmations": 0,
            "tier_c_tradable": False,
            "triple_penalty_quality_floor": 0.4,
            "block_ranging_regime": False,
            "atr_percentile_hard_reject": 1.0,
        },
        "exits": {
            "min_rr": 1.2,
            "min_rr_preferred": 1.5,
            "min_rr_neutral": 1.2,
            "rr_fallback_enabled": True,
            "require_structural_tp_in_neutral": False,
            "pip_floor_enabled": False,
            "sl_buffer_atr_mult": 0.1,
            "atr_fallback_sl_mult": 1.5,
            "min_sl_atr_mult": 0.5,
            "min_sl_pips": 8,
            "min_sl_spread_mult": 2.0,
            "min_sl_quality": 0.6,
            "min_sl_depth_atr_trending": 1.5,
            "min_sl_depth_atr_neutral": 1.2,
            "sl_h1_tf_weight": 1.15,
            "m15_tp_search_atr": 6.0,
            "h1_tp_search_atr": 10.0,
            "tp_h1_expand_mult": 1.25,
            "tp_h1_expand_mult_max": 1.4,
            "tp_h1_search_hard_cap_atr": 10.0,
            "tp_h1_base_relevance": 0.85,
            "tp_max_age_bars": 250,
            "swing_tp_enabled": False,
            "swing_tp_type_weight": 0.65,
            "swing_tp_max_range_atr": 10.0,
            "swing_tp_min_quality": 0.4,
            "swing_tp_overlap_atr": 0.3,
            "swing_tp_max_candidates": 3,
            "management": {
                "partials_enabled": True,
                "trailing_enabled": True,
                "session_close_exit": True,
                "breakeven_at_r": 1.0,
            },
        },
        "risk": {
            "risk_per_trade_pct": 0.1,
            "soft_daily_reduction_pct": 0.02,
            "block_new_trades_daily_pct": 0.04,
            "force_close_daily_pct": 0.05,
            "block_new_trades_total_pct": 0.08,
            "fixed_lot_size": 0.01,
            "max_lot_cap": 10.0,
        },
        "execution": {
            "mode": "paper",
            "auto_execute_live": False,
            "live_confirmed": False,
            "arming_required": False,
            "max_orders_per_run": 1,
        },
        "gates": {"allowed_setups": ["OB_WITH_BOS"], "mode": "enforce"},
        "entry_gate": {
            "proximity_gate_enabled": False,
            "session_proximity_gate_enabled": True,
            "session_proximity_gate_minutes": 60,
        },
        "sessions": {
            "ASIA":   {"start": "00:00", "end": "06:00"},
            "LONDON": {"start": "07:00", "end": "11:30"},
            "NY_AM":  {"start": "13:00", "end": "16:00"},
            "NY_PM":  {"start": "16:00", "end": "19:00"},
        },
        "symbol_sessions": {
            "default": ["ASIA", "LONDON", "NY_AM", "NY_PM"],
            "GBPJPY":  ["LONDON"],
        },
        "narrative": {"narrative_mode": False, "h4_context_gate": False},
    }


class TestEngineSessionProximityIntegration:

    def test_decision_returns_near_session_end_when_blocked(self):
        from src.core.enums import FinalDecision
        from src.decision.engine import evaluate_decision

        bar_time = datetime(2026, 5, 28, 11, 15, tzinfo=UTC)
        out = evaluate_decision(
            structures=_make_structures(bar_time),
            context=_make_context(bar_time),
            config=_make_config(),
            entry_price=213.600,
        )
        assert out.final_decision == FinalDecision.HOLD
        assert out.failure_code == "near_session_end"

    def test_decision_passes_with_sufficient_session_time(self):
        from src.decision.engine import evaluate_decision

        bar_time = datetime(2026, 5, 28, 9, 0, tzinfo=UTC)
        out = evaluate_decision(
            structures=_make_structures(bar_time),
            context=_make_context(bar_time),
            config=_make_config(),
            entry_price=213.600,
        )
        assert out.failure_code != "near_session_end"


# ---------------------------------------------------------------------------
# Config verification: JPY session assignments in live_market_watch.json
# ---------------------------------------------------------------------------

class TestLiveConfigJPYSessions:

    def _load_config(self) -> dict:
        import pathlib
        p = pathlib.Path(__file__).parents[2] / "src" / "config" / "live_market_watch.json"
        with open(p) as f:
            return json.load(f)

    def test_eurjpy_has_london_and_ny_am(self):
        assert self._load_config()["symbol_sessions"]["EURJPY"] == ["LONDON", "NY_AM"]

    def test_gbpjpy_is_london_only(self):
        assert self._load_config()["symbol_sessions"]["GBPJPY"] == ["LONDON"]

    def test_chfjpy_is_london_only(self):
        assert self._load_config()["symbol_sessions"]["CHFJPY"] == ["LONDON"]

    def test_session_proximity_gate_enabled(self):
        assert self._load_config()["entry_gate"]["session_proximity_gate_enabled"] is True

    def test_session_proximity_gate_minutes_is_60(self):
        assert self._load_config()["entry_gate"]["session_proximity_gate_minutes"] == 60
