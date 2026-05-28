from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from src.config.schema import validate_config_dict
from src import cli as cli_module
from src.core.enums import Namespace
from src.ops.namespace_guard import NamespaceGuard, NamespaceViolationError
from src.ops.schema_validator import validate_decision_record
from src.replay.parity_checker import compare_decisions
from src.replay.replayer import ReplayEngine
from src.replay.snapshot import SnapshotStore


def _base_decision() -> dict:
    return {
        "run_id": "eval_20260430_001",
        "scan_id": "EURUSD_20260430_0800",
        "decision_id": "11111111-1111-1111-1111-111111111111",
        "timestamp": "2026-04-30T08:00:00Z",
        "symbol": "EURUSD",
        "session": "LONDON",
        "execution_side": "BUY",
        "stage_entered": "CONFLUENCE",
        "stage_failed": "EXIT_PLAN",
        "failure_code": "rr_fallback_disabled_no_structural_tp",
        "failure_detail": "no m15/h1 structural TP found above 1.3 RR",
        "final_decision": "REJECTED_EXIT_PLAN",
        "final_decision_reason": "rr_fallback_disabled_no_structural_tp",
        "config_hash": "abc",
        "snapshot_id": "snap_001",
        "tp_debug": {"schema_version": "1", "found": [], "rejected": [], "selected": {}},
        "record_valid": True,
        "record_invalid_reasons": [],
    }


def _base_parity_decision() -> dict:
    return {
        "final_decision": "REJECTED_EXIT_PLAN",
        "failure_code": "rr_fallback_disabled_no_structural_tp",
        "setup_class": "OB_WITH_BOS",
        "confidence_tier": "A",
        "direction": "BULLISH",
        "planned_sl": 1.0848,
        "planned_tp": 1.0862,
        "planned_rr": 1.31,
        "detector_quality": 0.456,
        "effective_quality": 0.812,
        "tp_source": "ORDER_BLOCK",
        "structural_count": 3,
        "hard_rejects": [],
        "soft_penalties": ["h1_neutral"],
    }


def test_namespace_guard_blocks_eval_shadow_to_prod(tmp_path: Path) -> None:
    guard = NamespaceGuard(str(tmp_path / "logs"))

    for namespace in (Namespace.EVAL, Namespace.SHADOW):
        target = (tmp_path / "logs" / "prod" / "decisions_2026-04-30.jsonl").resolve()
        try:
            guard.assert_write_allowed(namespace=namespace, target_path=target)
            raised = False
        except NamespaceViolationError:
            raised = True
        assert raised


def test_frozen_config_constraints_are_empty() -> None:
    # FROZEN_CONSTRAINTS was cleared when risk_per_trade_pct was moved to a
    # configurable parameter. Verify that varying risk_per_trade_pct no longer
    # triggers a frozen_constraint_violation.
    from src.config.schema import FROZEN_CONSTRAINTS
    assert FROZEN_CONSTRAINTS == {}, "FROZEN_CONSTRAINTS must be empty — no hardcoded value locks"

    config = json.loads(Path("src/config/defaults.json").read_text(encoding="utf-8"))
    config["risk"]["risk_per_trade_pct"] = 0.1

    result = validate_config_dict(config)

    assert not any("frozen_constraint_violation" in err for err in result.errors)


def test_auto_execute_live_true_allowed_only_for_approved_prod_live_profile() -> None:
    config = json.loads(Path("src/config/defaults.json").read_text(encoding="utf-8"))
    config["runtime"]["mode"] = "live"
    config["runtime"]["namespace"] = "prod"
    config["execution"]["auto_execute_live"] = True
    config["execution"]["live_confirmed"] = True
    config["execution"]["arming_required"] = True
    config["execution"]["max_orders_per_run"] = 1
    config["execution"]["symbol_whitelist"] = ["EURUSD"]
    config["execution"]["debug_bypass"] = False
    config["execution"]["allow_overrides"] = False
    config["execution"]["retry_policy"] = "no_retry"
    config["risk"]["fixed_lot_size"] = 0.01

    result = validate_config_dict(config)

    assert result.valid


def test_auto_execute_live_true_rejected_outside_approved_profile() -> None:
    config = json.loads(Path("src/config/defaults.json").read_text(encoding="utf-8"))
    config["runtime"]["mode"] = "live"
    config["runtime"]["namespace"] = "eval"
    config["execution"]["auto_execute_live"] = True

    result = validate_config_dict(config)

    assert not result.valid
    assert "unsafe_default:auto_execute_live_must_default_false_outside_prod_live" in result.errors


def test_schema_validator_flags_missing_required_fields() -> None:
    decision = _base_decision()
    decision.pop("symbol")

    result = validate_decision_record(decision)

    assert not result.valid
    assert "missing_field:symbol" in result.reasons


def test_tp_debug_required_for_rejected_exit_plan() -> None:
    decision = _base_decision()
    decision.pop("tp_debug")

    result = validate_decision_record(decision)

    assert not result.valid
    assert "missing_field:tp_debug" in result.reasons
    assert "invalid_tp_debug_type" in result.reasons


def test_snapshot_save_load_roundtrip(tmp_path: Path) -> None:
    store = SnapshotStore(logs_root=str(tmp_path / "logs"), namespace=Namespace.EVAL)
    payload = {"snapshot_id": "snap_roundtrip", "expected_decision": _base_parity_decision()}

    store.save("snap_roundtrip", payload)
    loaded = store.load("snap_roundtrip")

    assert loaded == payload


def test_replay_uses_snapshot_only_and_no_mt5_calls() -> None:
    engine = ReplayEngine()
    snapshot = {"snapshot_id": "snap_001", "expected_decision": _base_parity_decision()}

    output = engine.replay_from_snapshot(snapshot)

    assert output.snapshot_id == "snap_001"
    assert output.decision["replayed"] is True
    assert engine.mt5_calls_attempted is False


def test_parity_checker_tolerances() -> None:
    expected = _base_parity_decision()
    within = dict(expected)
    within["planned_sl"] = expected["planned_sl"] + 0.00009
    within["planned_rr"] = expected["planned_rr"] + 0.009

    outside = dict(expected)
    outside["planned_rr"] = expected["planned_rr"] + 0.02

    pass_result = compare_decisions(expected, within, tick_size=0.0001)
    fail_result = compare_decisions(expected, outside, tick_size=0.0001)

    assert pass_result.pass_all
    assert not fail_result.pass_all


def test_cli_returns_clean_verdict_and_exit_code() -> None:
    command = [
        sys.executable,
        "run.py",
        "doctor",
        "--namespace",
        "eval",
        "--mode",
        "paper",
        "--config",
        "src/config/defaults.json",
        "--run-id",
        "pytest_doctor",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)

    assert result.returncode == 0
    assert "Verdict" in result.stdout
    assert "Exit code" in result.stdout


def test_live_run_is_rejected_in_phase0() -> None:
    command = [
        sys.executable,
        "run.py",
        "run",
        "live",
        "--namespace",
        "prod",
        "--config",
        "src/config/defaults.json",
        "--run-id",
        "pytest_live",
        "--output",
        "logs/prod/reports/run_live",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)

    assert result.returncode == 1
    assert "REJECTED_EXECUTION" in result.stdout
    assert "live_not_armed" in result.stdout


def test_live_run_default_output_uses_prod_namespace_path() -> None:
    command = [
        sys.executable,
        "run.py",
        "run",
        "live",
        "--namespace",
        "prod",
        "--config",
        "src/config/defaults.json",
        "--run-id",
        "pytest_live_prod_output",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)

    expected_output = str(Path("logs") / "prod" / "reports" / "run")
    assert result.returncode == 1
    assert f"Output     : {expected_output}" in result.stdout


def test_prod_namespace_rejects_eval_output_path() -> None:
    command = [
        sys.executable,
        "run.py",
        "run",
        "live",
        "--namespace",
        "prod",
        "--config",
        "src/config/defaults.json",
        "--run-id",
        "pytest_live_prod_to_eval",
        "--output",
        "logs/eval/reports/run",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)

    assert result.returncode == 1
    assert "namespace_violation:" in result.stdout
    assert "namespace_root_mismatch:prod" in result.stdout


def test_live_arm_cli_creates_token_and_reports_expiry_and_run_binding() -> None:
    command = [
        sys.executable,
        "run.py",
        "live",
        "arm",
        "--run-id",
        "pytest_live_arm_001",
        "--symbol",
        "EURUSD",
        "--max-orders",
        "1",
        "--ttl-minutes",
        "15",
        "--reason",
        "controlled one-order EURUSD test",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)

    assert result.returncode == 0
    assert "D.E.V.I 4.0" in result.stdout
    assert "Mode       : live" in result.stdout
    assert "Run ID     : pytest_live_arm_001" in result.stdout
    assert "Token ID   :" in result.stdout
    assert "Expires At :" in result.stdout


def test_live_arm_token_not_persisted_across_separate_cli_processes() -> None:
    arm_command = [
        sys.executable,
        "run.py",
        "live",
        "arm",
        "--run-id",
        "pytest_cross_process_001",
        "--symbol",
        "EURUSD",
        "--max-orders",
        "1",
        "--ttl-minutes",
        "15",
        "--reason",
        "cross-process-token-check",
        "--json",
    ]
    arm_result = subprocess.run(arm_command, capture_output=True, text=True, check=False)
    assert arm_result.returncode == 0

    run_command = [
        sys.executable,
        "run.py",
        "run",
        "live",
        "--namespace",
        "prod",
        "--config",
        "src/config/live_preflight.json",
        "--run-id",
        "pytest_cross_process_001",
        "--json",
    ]
    run_result = subprocess.run(run_command, capture_output=True, text=True, check=False)
    payload = json.loads(run_result.stdout)

    assert run_result.returncode == 1
    assert payload["final_decision"] == "REJECTED_EXECUTION"
    assert payload["failure_code"] == "live_not_armed"


def test_live_armed_run_sees_token_in_same_process_for_preflight() -> None:
    command = [
        sys.executable,
        "run.py",
        "live",
        "armed-run",
        "--config",
        "src/config/live_preflight.json",
        "--run-id",
        "pytest_armed_run_preflight",
        "--symbol",
        "EURUSD",
        "--max-orders",
        "1",
        "--ttl-minutes",
        "15",
        "--reason",
        "preflight armed-run check",
        "--json",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    payload = json.loads(result.stdout)

    assert result.returncode == 1
    assert payload["armed_token_seen"] is True
    assert payload["execution_attempted"] is False
    assert payload["failure_code"] == "preflight_no_order_send"
    assert payload["order_send_called"] is False
    assert payload["disarm_called"] is True
    assert payload["token_cleared"] is True


def test_live_armed_run_consumes_token_on_execution_attempt(monkeypatch, capsys) -> None:
    class _FakeDataSource:
        def fetch_tick(self, symbol: str) -> dict[str, float]:
            assert symbol == "EURUSD"
            return {"bid": 1.1, "ask": 1.1002, "time": 1}

        def fetch_instrument_profile(self, symbol: str):
            assert symbol == "EURUSD"
            return SimpleNamespace(point=0.0001)

        def close(self) -> None:
            return None

    call_state = {"send_called": False}

    class _FakeLiveWrapper:
        def __init__(self, data_source, telemetry_writer):
            self.data_source = data_source
            self.telemetry_writer = telemetry_writer

        def send(self, intent, **kwargs):
            call_state["send_called"] = True
            return SimpleNamespace(
                sent=False,
                status="blocked_pretrade_rechecks_failed",
                broker_retcode=10016,
                ticket=None,
                order_send_invoked=True,
            )

    monkeypatch.setattr(cli_module, "MT5DataSource", _FakeDataSource)
    monkeypatch.setattr(cli_module, "LiveOrderWrapper", _FakeLiveWrapper)

    args = SimpleNamespace(
        namespace="prod",
        config="src/config/live_one_order_test.json",
        run_id="pytest_armed_run_execution_attempt",
        symbol=["EURUSD"],
        max_orders=1,
        ttl_minutes=15,
        reason="execution attempt token consume check",
        output=None,
        logs_root="logs",
        json=True,
    )

    code = cli_module.cmd_live_armed_run(args)
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert call_state["send_called"] is True
    assert payload["execution_attempted"] is True
    assert payload["token_consumed"] is True
    assert payload["disarm_called"] is True
    assert payload["token_cleared"] is True
    assert payload["order_send_called"] is True


def test_live_one_order_config_requires_armed_run_path() -> None:
    command = [
        sys.executable,
        "run.py",
        "run",
        "live",
        "--namespace",
        "prod",
        "--config",
        "src/config/live_one_order_test.json",
        "--run-id",
        "pytest_requires_armed_run",
        "--json",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    payload = json.loads(result.stdout)

    assert result.returncode == 1
    assert payload["final_decision"] == "REJECTED_EXECUTION"
    assert payload["failure_code"] == "live_requires_armed_run"


def test_live_account_check_reports_required_fields_and_is_read_only(monkeypatch, capsys) -> None:
    class _FakeClient:
        ACCOUNT_TRADE_MODE_DEMO = 0
        ACCOUNT_TRADE_MODE_CONTEST = 1
        ACCOUNT_TRADE_MODE_REAL = 2
        SYMBOL_TRADE_MODE_FULL = 4

        def __init__(self) -> None:
            self.order_send_calls = 0

        def account_info(self):
            return SimpleNamespace(
                login=12345678,
                server="Broker-Real",
                trade_mode=self.ACCOUNT_TRADE_MODE_REAL,
                balance=10000.0,
                equity=9998.5,
                currency="USD",
            )

        def symbol_info(self, symbol: str):
            assert symbol == "EURUSD"
            return SimpleNamespace(
                trade_allowed=True,
                trade_mode=4,
                session_deals=True,
                volume_min=0.01,
                volume_step=0.01,
                volume_max=0.01,
                digits=5,
                point=0.00001,
                trade_tick_size=0.00001,
                trade_contract_size=100000.0,
            )

        def symbol_info_tick(self, symbol: str):
            assert symbol == "EURUSD"
            return SimpleNamespace(bid=1.23450, ask=1.23460, time=1714810800)

        def order_send(self, *_args, **_kwargs):
            self.order_send_calls += 1
            raise AssertionError("order_send must never be called by account-check")

    class _FakeDataSource:
        def __init__(self) -> None:
            self.mt5_client = _FakeClient()
            self.closed = False

        def fetch_account_info(self) -> dict[str, float | str]:
            return {
                "balance": 10000.0,
                "equity": 9998.5,
                "margin": 0.0,
                "free_margin": 9800.0,
                "currency": "USD",
            }

        def fetch_tick(self, symbol: str) -> dict[str, float | int]:
            assert symbol == "EURUSD"
            return {"bid": 1.23450, "ask": 1.23460, "time": 1714810800}

        def fetch_instrument_profile(self, symbol: str):
            assert symbol == "EURUSD"
            return SimpleNamespace(min_lot=0.01, lot_step=0.01, max_lot=0.01)

        def close(self) -> None:
            self.closed = True

    fake_source = _FakeDataSource()
    monkeypatch.setattr(cli_module, "MT5DataSource", lambda: fake_source)

    args = cli_module.build_parser().parse_args([
        "live",
        "account-check",
        "--namespace",
        "prod",
        "--config",
        "src/config/live_one_order_test.json",
        "--run-id",
        "pytest_account_check",
        "--json",
    ])
    exit_code = args.func(args)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["verdict"] == "PASS"
    assert payload["account_number"] == 12345678
    assert payload["server"] == "Broker-Real"
    assert payload["account_type"] == "REAL"
    assert payload["balance"] == 10000.0
    assert payload["equity"] == 9998.5
    assert payload["currency"] == "USD"
    assert payload["symbol"] == "EURUSD"
    assert payload["bid"] == 1.2345
    assert payload["ask"] == 1.2346
    assert payload["min_lot"] == 0.01
    assert payload["lot_step"] == 0.01
    assert payload["max_lot"] == 0.01
    assert payload["tradable"] is True
    assert payload["market_open"] is True
    assert payload["order_send_called"] is False
    assert payload["armed"] is False
    assert fake_source.mt5_client.order_send_calls == 0
    assert fake_source.closed is True


def test_live_account_check_fails_when_market_not_tradable(monkeypatch, capsys) -> None:
    class _ClosedMarketClient:
        ACCOUNT_TRADE_MODE_DEMO = 0
        ACCOUNT_TRADE_MODE_CONTEST = 1
        ACCOUNT_TRADE_MODE_REAL = 2
        SYMBOL_TRADE_MODE_FULL = 4

        def __init__(self) -> None:
            self.order_send_calls = 0

        def account_info(self):
            return SimpleNamespace(
                login=999111,
                server="Broker-Real",
                trade_mode=self.ACCOUNT_TRADE_MODE_REAL,
                balance=5000.0,
                equity=5000.0,
                currency="USD",
            )

        def symbol_info(self, _symbol: str):
            return SimpleNamespace(
                trade_allowed=False,
                trade_mode=1,
                session_deals=False,
                volume_min=0.01,
                volume_step=0.01,
                volume_max=0.01,
                digits=5,
                point=0.00001,
                trade_tick_size=0.00001,
                trade_contract_size=100000.0,
            )

        def symbol_info_tick(self, _symbol: str):
            return SimpleNamespace(bid=0.0, ask=0.0, time=1714810800)

        def order_send(self, *_args, **_kwargs):
            self.order_send_calls += 1
            raise AssertionError("order_send must never be called by account-check")

    class _ClosedMarketDataSource:
        def __init__(self) -> None:
            self.mt5_client = _ClosedMarketClient()

        def fetch_account_info(self) -> dict[str, float | str]:
            return {
                "balance": 5000.0,
                "equity": 5000.0,
                "margin": 0.0,
                "free_margin": 4900.0,
                "currency": "USD",
            }

        def fetch_tick(self, _symbol: str) -> dict[str, float | int]:
            return {"bid": 0.0, "ask": 0.0, "time": 1714810800}

        def fetch_instrument_profile(self, _symbol: str):
            return SimpleNamespace(min_lot=0.01, lot_step=0.01, max_lot=0.01)

        def close(self) -> None:
            return None

    closed_market_source = _ClosedMarketDataSource()
    monkeypatch.setattr(cli_module, "MT5DataSource", lambda: closed_market_source)

    args = cli_module.build_parser().parse_args([
        "live",
        "account-check",
        "--namespace",
        "prod",
        "--config",
        "src/config/live_one_order_test.json",
        "--run-id",
        "pytest_account_check_closed_market",
        "--json",
    ])
    exit_code = args.func(args)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["verdict"] == "FAIL"
    assert payload["tradable"] is False
    assert payload["market_open"] is False
    assert payload["order_send_called"] is False
    assert payload["armed"] is False
    assert closed_market_source.mt5_client.order_send_calls == 0
