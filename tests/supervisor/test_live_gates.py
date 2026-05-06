"""Tests proving live execution cannot proceed without all safety gates.

These tests verify:
  - Kill switch blocks before supervisor
  - Live mode requires arming token
  - Paper mode is completely unaffected by arming/kill switch
  - Symbol authorization is enforced for live
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.core.arming import ArmingService
from src.core.enums import Direction, HTFAgreement, Regime, Session
from src.core.kill_switch import KillSwitch
from src.core.models import ContextSnapshot
from src.core.runtime_state import RuntimeState
from src.supervisor.gate import SupervisorVerdict, evaluate_supervisor


def _make_context(symbol: str = "EURUSD") -> ContextSnapshot:
    now = datetime.now(tz=UTC)
    return ContextSnapshot(
        symbol=symbol,
        bar_time=now,
        session=Session.LONDON,
        micro_window=False,
        trend_m15=Direction.BULLISH,
        trend_h1=Direction.BULLISH,
        htf_agreement=HTFAgreement.AGREES,
        regime=Regime.TRENDING,
        atr_current=0.0010,
        atr_percentile=0.5,
        spread_atr_ratio=0.1,
        stale_entry=False,
        news_blocked=False,
        nearby_structures=[],
    )


def _live_config(**overrides) -> dict:
    base = {
        "execution": {
            "max_orders_per_run": 1,
            "live_confirmed": True,
            "kill_switch_enabled": False,
        },
        "runtime": {
            "mode": "live",
        },
    }
    for key, val in overrides.items():
        if "." in key:
            section, sub = key.split(".", 1)
            base[section][sub] = val
        else:
            base[key] = val
    return base


def _paper_config() -> dict:
    return {
        "execution": {
            "max_orders_per_run": 1,
            "live_confirmed": False,
            "kill_switch_enabled": False,
        },
        "runtime": {
            "mode": "paper",
        },
    }


# --- Kill Switch Tests ---


def test_kill_switch_blocks_execution() -> None:
    ks = KillSwitch()
    ks.trigger("manual panic")
    verdict = evaluate_supervisor(
        context=_make_context(),
        config=_live_config(),
        kill_switch=ks,
    )
    assert verdict.approved is False
    assert "kill_switch_active" in verdict.reason


def test_kill_switch_from_config_flag() -> None:
    ks = KillSwitch()
    verdict = evaluate_supervisor(
        context=_make_context(),
        config=_live_config(**{"execution.kill_switch_enabled": True}),
        kill_switch=ks,
    )
    assert verdict.approved is False
    assert "kill_switch_active" in verdict.reason


# --- Arming Tests ---


def test_live_without_arming_rejected() -> None:
    verdict = evaluate_supervisor(
        context=_make_context(),
        config=_live_config(),
        arming_service=ArmingService(),  # not armed
    )
    assert verdict.approved is False
    assert verdict.reason == "live_not_armed"


def test_live_with_arming_approved() -> None:
    arming = ArmingService()
    arming.arm(
        run_id="run_001",
        armed_by="op",
        reason="test",
        symbols=["EURUSD"],
        max_orders=1,
    )
    verdict = evaluate_supervisor(
        context=_make_context("EURUSD"),
        config=_live_config(),
        arming_service=arming,
    )
    assert verdict.approved is True
    assert verdict.reason == "approved"


def test_live_unauthorized_symbol_rejected() -> None:
    arming = ArmingService()
    arming.arm(
        run_id="run_001",
        armed_by="op",
        reason="test",
        symbols=["EURUSD"],  # only EURUSD authorized
        max_orders=1,
    )
    verdict = evaluate_supervisor(
        context=_make_context("GBPUSD"),  # trying GBPUSD
        config=_live_config(),
        arming_service=arming,
    )
    assert verdict.approved is False
    assert verdict.reason == "symbol_not_authorized_for_live"


def test_live_expired_token_rejected() -> None:
    arming = ArmingService()
    arming.arm(
        run_id="run_001",
        armed_by="op",
        reason="test",
        symbols=["EURUSD"],
        max_orders=1,
        ttl_minutes=-1,  # expired immediately
    )
    verdict = evaluate_supervisor(
        context=_make_context(),
        config=_live_config(),
        arming_service=arming,
    )
    assert verdict.approved is False
    assert verdict.reason == "live_not_armed"


def test_live_not_confirmed_in_config() -> None:
    arming = ArmingService()
    arming.arm(run_id="run_001", armed_by="op", reason="test", symbols=["EURUSD"], max_orders=1)
    verdict = evaluate_supervisor(
        context=_make_context(),
        config=_live_config(**{"execution.live_confirmed": False}),
        arming_service=arming,
    )
    assert verdict.approved is False
    assert verdict.reason == "live_not_confirmed_in_config"


# --- Paper Mode Unaffected ---


def test_paper_ignores_arming_and_kill_switch() -> None:
    """Paper mode should pass even with kill switch triggered and no arming."""
    ks = KillSwitch()
    ks.trigger("manual")
    verdict = evaluate_supervisor(
        context=_make_context(),
        config=_paper_config(),
        kill_switch=ks,
        arming_service=ArmingService(),  # not armed
    )
    assert verdict.approved is True
    assert verdict.reason == "approved"


def test_paper_with_runtime_state_still_works() -> None:
    rs = RuntimeState(run_id="run_001")
    rs.record_trade("t1")
    config = _paper_config()
    config["execution"]["max_orders_per_run"] = 2
    verdict = evaluate_supervisor(
        context=_make_context(),
        config=config,
        runtime_state=rs,
    )
    assert verdict.approved is True
    assert verdict.reason == "approved"


# --- Integration: All Gates Together ---


def test_all_gates_must_pass_for_live() -> None:
    """Live requires: kill switch OFF, armed, valid token, authorized symbol, max_orders not exceeded."""
    arming = ArmingService()
    arming.arm(run_id="run_001", armed_by="op", reason="test", symbols=["EURUSD"], max_orders=1)
    ks = KillSwitch()
    rs = RuntimeState(run_id="run_001")

    verdict = evaluate_supervisor(
        context=_make_context("EURUSD"),
        config=_live_config(),
        runtime_state=rs,
        kill_switch=ks,
        arming_service=arming,
    )
    assert verdict.approved is True


def test_live_max_orders_blocks_after_one_trade() -> None:
    arming = ArmingService()
    arming.arm(run_id="run_001", armed_by="op", reason="test", symbols=["EURUSD"], max_orders=1)
    ks = KillSwitch()
    rs = RuntimeState(run_id="run_001")
    rs.record_trade("trade_001")

    verdict = evaluate_supervisor(
        context=_make_context(),
        config=_live_config(),
        runtime_state=rs,
        kill_switch=ks,
        arming_service=arming,
    )
    assert verdict.approved is False
    assert verdict.reason == "max_orders_per_run_exceeded"
