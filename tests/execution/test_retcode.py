from __future__ import annotations

from src.execution.retcode import (
    BrokerStatus,
    RetryPolicy,
    is_success,
    map_retcode,
    should_record_failure,
)


def test_map_retcode_success_10009() -> None:
    m = map_retcode(10009)
    assert m.status == BrokerStatus.FILLED
    assert m.policy == RetryPolicy.OK


def test_map_retcode_no_money_10027() -> None:
    m = map_retcode(10027)
    assert m.status == BrokerStatus.NO_MONEY
    assert m.policy == RetryPolicy.NO_RETRY


def test_map_retcode_requote_retry() -> None:
    m = map_retcode(10004)
    assert m.status == BrokerStatus.REQUOTE
    assert m.policy == RetryPolicy.RETRY


def test_map_retcode_unknown_defaults() -> None:
    m = map_retcode(99999)
    assert m.status == BrokerStatus.UNKNOWN
    assert m.policy == RetryPolicy.NO_RETRY


def test_is_success_true_only_for_filled() -> None:
    assert is_success(10009) is True
    assert is_success(10008) is True
    assert is_success(10027) is False


def test_should_record_failure() -> None:
    assert should_record_failure(10027) is True
    assert should_record_failure(10004) is True
    assert should_record_failure(10009) is False
