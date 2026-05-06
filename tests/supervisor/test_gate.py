from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from src.core.enums import Direction, HTFAgreement, Regime, Session
from src.core.models import ContextSnapshot
from src.supervisor.gate import evaluate_supervisor


def _context() -> ContextSnapshot:
    return ContextSnapshot(
        symbol="EURUSD",
        bar_time=datetime(2026, 4, 30, 8, 0, tzinfo=UTC),
        session=Session.LONDON,
        micro_window=False,
        trend_m15=Direction.BULLISH,
        trend_h1=Direction.BULLISH,
        htf_agreement=HTFAgreement.AGREES,
        regime=Regime.TRENDING,
        atr_current=0.001,
        atr_percentile=0.5,
        spread_atr_ratio=0.1,
        stale_entry=False,
        news_blocked=False,
        nearby_structures=[],
    )


def _config() -> dict:
    return json.loads(Path("src/config/defaults.json").read_text(encoding="utf-8"))


def test_supervisor_approves_paper_mode_default() -> None:
    verdict = evaluate_supervisor(context=_context(), config=_config())

    assert verdict.approved is True
    assert verdict.reason == "approved"


def test_supervisor_rejects_live_when_not_confirmed() -> None:
    cfg = _config()
    cfg["runtime"] = dict(cfg["runtime"])
    cfg["execution"] = dict(cfg["execution"])
    cfg["runtime"]["mode"] = "live"
    cfg["execution"]["live_confirmed"] = False

    verdict = evaluate_supervisor(context=_context(), config=cfg)

    assert verdict.approved is False
    assert verdict.reason == "live_not_confirmed_in_config"


def test_supervisor_rejects_live_when_not_armed() -> None:
    cfg = _config()
    cfg["runtime"] = dict(cfg["runtime"])
    cfg["execution"] = dict(cfg["execution"])
    cfg["runtime"]["mode"] = "live"
    cfg["execution"]["live_confirmed"] = True

    verdict = evaluate_supervisor(context=_context(), config=cfg)

    assert verdict.approved is False
    assert verdict.reason == "live_not_armed"


def test_supervisor_rejects_invalid_max_orders_per_run() -> None:
    cfg = _config()
    cfg["execution"] = dict(cfg["execution"])
    cfg["execution"]["max_orders_per_run"] = 0

    verdict = evaluate_supervisor(context=_context(), config=cfg)

    assert verdict.approved is False
    assert verdict.reason == "max_orders_per_run_invalid"


def test_supervisor_rejects_when_max_orders_already_reached() -> None:
    cfg = _config()
    cfg["execution"] = dict(cfg["execution"])
    cfg["execution"]["max_orders_per_run"] = 1

    verdict = evaluate_supervisor(
        context=_context(), config=cfg, current_orders_this_run=1
    )

    assert verdict.approved is False
    assert verdict.reason == "max_orders_per_run_exceeded"
