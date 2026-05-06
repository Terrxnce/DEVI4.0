from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_evidence_pack(output_path: str, artifacts: dict[str, Any]) -> dict[str, Any]:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(artifacts, fp, indent=2, sort_keys=True)
    return {"ok": True, "output": str(path)}
