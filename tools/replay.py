from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.replay.replayer import ReplayEngine


def replay_snapshot_file(snapshot_file: str) -> dict[str, Any]:
    engine = ReplayEngine()
    replay = engine.replay_file(snapshot_file)

    out_path = Path(snapshot_file).with_suffix(".replay.json")
    with out_path.open("w", encoding="utf-8") as fp:
        json.dump(replay.decision, fp, indent=2, sort_keys=True)

    return {
        "ok": True,
        "snapshot_id": replay.snapshot_id,
        "output": str(out_path),
        "mt5_calls_attempted": engine.mt5_calls_attempted,
    }
