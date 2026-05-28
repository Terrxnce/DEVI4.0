from __future__ import annotations

import json
from pathlib import Path

from src.execution.gate import evaluate_execution


def _config() -> dict:
    return json.loads(Path("src/config/defaults.json").read_text(encoding="utf-8"))


def test_execution_gate_approves_non_live_defaults() -> None:
    verdict = evaluate_execution(_config())

    assert verdict.approved is True
    assert verdict.reason == "approved"


def test_execution_gate_rejects_live_in_phase1() -> None:
    cfg = _config()
    cfg["runtime"] = dict(cfg["runtime"])
    cfg["runtime"]["mode"] = "live"

    verdict = evaluate_execution(cfg)

    assert verdict.approved is False
    assert verdict.reason == "live_not_confirmed_in_config"


def test_execution_gate_rejects_invalid_mode() -> None:
    cfg = _config()
    cfg["execution"] = dict(cfg["execution"])
    cfg["execution"]["mode"] = "LIMIT"

    verdict = evaluate_execution(cfg)

    assert verdict.approved is False
    assert verdict.reason == "unsupported_execution_mode"


def test_execution_gate_rejects_invalid_runtime_mode() -> None:
    cfg = _config()
    cfg["runtime"] = dict(cfg["runtime"])
    cfg["runtime"]["mode"] = "invalid"

    verdict = evaluate_execution(cfg)

    assert verdict.approved is False
    assert verdict.reason == "invalid_runtime_mode"
