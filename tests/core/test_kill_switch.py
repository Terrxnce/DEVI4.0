"""Tests for kill switch — irreversible halt of live trades."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.core.kill_switch import KillSwitch, KillSwitchVerdict


def test_initial_state_not_triggered() -> None:
    ks = KillSwitch()
    assert ks.is_triggered is False
    verdict = ks.evaluate()
    assert verdict.triggered is False


def test_config_flag_triggers() -> None:
    ks = KillSwitch()
    verdict = ks.evaluate(config_kill_switch_enabled=True)
    assert verdict.triggered is True
    assert verdict.reason == "config_kill_switch"
    assert ks.is_triggered is True


def test_manual_trigger() -> None:
    ks = KillSwitch()
    ks.trigger("operator panic")
    verdict = ks.evaluate()
    assert verdict.triggered is True
    assert verdict.reason == "operator panic"


def test_latch_stays_triggered_after_source_removed() -> None:
    ks = KillSwitch()
    ks.evaluate(config_kill_switch_enabled=True)
    assert ks.is_triggered is True

    # Removing the config flag does NOT reset the latch
    verdict = ks.evaluate(config_kill_switch_enabled=False)
    assert verdict.triggered is True
    assert verdict.reason == "config_kill_switch"


def test_reset_clears_trigger() -> None:
    ks = KillSwitch()
    ks.trigger("test")
    assert ks.is_triggered is True

    ks.reset()
    assert ks.is_triggered is False
    assert ks.reason == ""


def test_broker_error_rate_triggers() -> None:
    ks = KillSwitch()
    ks.record_failure()
    ks.record_failure()
    ks.record_failure()
    verdict = ks.evaluate()
    assert verdict.triggered is True
    assert verdict.reason == "broker_error_rate_exceeded"


def test_broker_error_rate_prunes_old_failures() -> None:
    ks = KillSwitch()
    # Inject old failure timestamps manually
    ks._failed_orders = [
        datetime.now(tz=UTC) - timedelta(minutes=20),
        datetime.now(tz=UTC) - timedelta(minutes=19),
        datetime.now(tz=UTC) - timedelta(minutes=18),
    ]
    verdict = ks.evaluate()
    assert verdict.triggered is False  # all older than 10 minutes


def test_drawdown_breach_triggers() -> None:
    ks = KillSwitch()
    verdict = ks.evaluate(
        current_equity=8000.0,
        initial_balance=10000.0,
        drawdown_threshold=0.20,
    )
    assert verdict.triggered is True
    assert verdict.reason == "drawdown_breach"


def test_drawdown_no_breach() -> None:
    ks = KillSwitch()
    verdict = ks.evaluate(
        current_equity=9000.0,
        initial_balance=10000.0,
        drawdown_threshold=0.20,
    )
    assert verdict.triggered is False


def test_drawdown_ignored_without_balance() -> None:
    ks = KillSwitch()
    verdict = ks.evaluate(current_equity=5000.0)
    assert verdict.triggered is False


def test_triggered_blocks_execution() -> None:
    """Simulate: kill switch triggered -> no live execution allowed."""
    ks = KillSwitch()
    ks.trigger("manual")
    verdict = ks.evaluate()
    assert verdict.triggered is True
    # In production, this would map to FinalDecision.REJECTED_EXECUTION
    assert ks.is_triggered is True
