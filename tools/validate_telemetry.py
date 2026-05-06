from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.ops.schema_validator import no_candidates_found, validate_decision_record


def validate_telemetry_file(file_path: str) -> dict[str, Any]:
    path = Path(file_path)
    if not path.exists():
        return {"ok": False, "error": f"file_not_found:{path}"}

    total = 0
    invalid = 0
    tp_debug_coverage_fail = 0

    with path.open("r", encoding="utf-8-sig") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            total += 1
            record = json.loads(line)
            validation = validate_decision_record(record)
            if not validation.valid:
                invalid += 1
            if record.get("final_decision") == "REJECTED_EXIT_PLAN" and no_candidates_found(record):
                tp_debug_coverage_fail += 1

    return {
        "ok": invalid == 0 and tp_debug_coverage_fail == 0,
        "total": total,
        "invalid": invalid,
        "tp_debug_coverage_fail": tp_debug_coverage_fail,
    }
