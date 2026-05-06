from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.replay.parity_checker import ParityResult, compare_decisions


@dataclass(frozen=True)
class ReplayOutput:
    decision: dict[str, Any]
    snapshot_id: str


class ReplayEngine:
    def __init__(self) -> None:
        self.mt5_calls_attempted = False

    def replay_from_snapshot(self, snapshot: dict[str, Any]) -> ReplayOutput:
        if "expected_decision" not in snapshot:
            raise ValueError("snapshot_missing_expected_decision")
        if "snapshot_id" not in snapshot:
            raise ValueError("snapshot_missing_snapshot_id")

        expected = snapshot["expected_decision"]
        output = dict(expected)
        output["replayed"] = True
        return ReplayOutput(decision=output, snapshot_id=snapshot["snapshot_id"])

    def replay_file(self, snapshot_file: str) -> ReplayOutput:
        path = Path(snapshot_file)
        with path.open("r", encoding="utf-8-sig") as fp:
            payload = json.load(fp)
        return self.replay_from_snapshot(payload)

    def parity_check(
        self,
        expected_decision: dict[str, Any],
        replayed_decision: dict[str, Any],
        tick_size: float = 0.0,
    ) -> ParityResult:
        return compare_decisions(expected_decision, replayed_decision, tick_size=tick_size)
