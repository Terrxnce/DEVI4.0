"""Tests for live arming token flow."""
from __future__ import annotations

import pytest

from src.core.arming import ArmingService, LiveArmingToken


def test_arm_creates_valid_token() -> None:
    svc = ArmingService()
    token = svc.arm(
        run_id="run_001",
        armed_by="operator_a",
        reason="scheduled live test",
        symbols=["EURUSD"],
        max_orders=1,
        ttl_minutes=30,
    )
    assert token is not None
    assert token.is_valid is True
    assert token.is_expired is False
    assert token.run_id == "run_001"
    assert token.armed_by == "operator_a"
    assert token.symbols == ["EURUSD"]
    assert token.max_orders == 1


def test_arm_returns_none_when_already_armed() -> None:
    svc = ArmingService()
    svc.arm(run_id="run_001", armed_by="op", reason="test", symbols=["EURUSD"], max_orders=1)
    second = svc.arm(run_id="run_002", armed_by="op", reason="test2", symbols=["EURUSD"], max_orders=1)
    assert second is None


def test_disarm_clears_token() -> None:
    svc = ArmingService()
    svc.arm(run_id="run_001", armed_by="op", reason="test", symbols=["EURUSD"], max_orders=1)
    assert svc.is_armed is True

    cleared = svc.disarm("operator request")
    assert cleared is True
    assert svc.is_armed is False
    assert svc.get_valid_token() is None


def test_disarm_returns_false_when_not_armed() -> None:
    svc = ArmingService()
    assert svc.disarm("test") is False


def test_consume_token_clears_active_token() -> None:
    svc = ArmingService()
    token = svc.arm(
        run_id="run_001",
        armed_by="op",
        reason="test",
        symbols=["EURUSD"],
        max_orders=1,
    )
    assert token is not None
    consumed = svc.consume_token(token.token_id, reason="execution_attempt")
    assert consumed is True
    assert svc.is_armed is False
    assert svc.get_valid_token() is None


def test_consume_token_rejects_wrong_token_id() -> None:
    svc = ArmingService()
    token = svc.arm(
        run_id="run_001",
        armed_by="op",
        reason="test",
        symbols=["EURUSD"],
        max_orders=1,
    )
    assert token is not None
    consumed = svc.consume_token("wrong-token-id", reason="execution_attempt")
    assert consumed is False
    assert svc.is_armed is True


def test_token_expires_after_ttl() -> None:
    svc = ArmingService()
    token = svc.arm(
        run_id="run_001",
        armed_by="op",
        reason="test",
        symbols=["EURUSD"],
        max_orders=1,
        ttl_minutes=-1,  # immediately expired
    )
    assert token is not None
    assert token.is_expired is True
    assert svc.get_valid_token() is None
    assert svc.is_armed is False


def test_get_valid_token_returns_none_when_expired() -> None:
    svc = ArmingService()
    svc.arm(run_id="run_001", armed_by="op", reason="test", symbols=["EURUSD"], max_orders=1, ttl_minutes=-1)
    assert svc.get_valid_token() is None


def test_is_armed_true_only_with_valid_token() -> None:
    svc = ArmingService()
    assert svc.is_armed is False

    svc.arm(run_id="run_001", armed_by="op", reason="test", symbols=["EURUSD"], max_orders=1)
    assert svc.is_armed is True

    svc.disarm("test")
    assert svc.is_armed is False


def test_token_fields_frozen() -> None:
    token = LiveArmingToken(
        token_id="t1",
        run_id="run_1",
        armed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        expires_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        armed_by="op",
        reason="test",
        symbols=["EURUSD"],
        max_orders=1,
    )
    with pytest.raises(AttributeError):
        token.run_id = "run_2"
