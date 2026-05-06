from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.replay.parity_checker import compare_decisions


def check_comparability(expected_file: str, actual_file: str, tick_size: float = 0.0) -> dict[str, Any]:
    with Path(expected_file).open("r", encoding="utf-8-sig") as fp:
        expected = json.load(fp)
    with Path(actual_file).open("r", encoding="utf-8-sig") as fp:
        actual = json.load(fp)

    parity = compare_decisions(expected, actual, tick_size=tick_size)
    return {
        "ok": parity.pass_all,
        "diffs": [d.__dict__ for d in parity.diffs],
    }
