from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.core.enums import Namespace
from src.ops.namespace_guard import NamespaceGuard


class SnapshotStore:
    def __init__(self, logs_root: str, namespace: Namespace) -> None:
        self.guard = NamespaceGuard(logs_root)
        self.namespace = namespace
        self.guard.ensure_namespace_dirs(namespace)

    def save(self, snapshot_id: str, payload: dict[str, Any]) -> Path:
        path = self.guard.namespace_path(self.namespace, f"snapshots/{snapshot_id}.json")
        with path.open("w", encoding="utf-8") as fp:
            json.dump(payload, fp, indent=2, sort_keys=True)
        return path

    def load(self, snapshot_id: str) -> dict[str, Any]:
        path = self.guard.namespace_path(self.namespace, f"snapshots/{snapshot_id}.json")
        if not path.exists():
            raise FileNotFoundError(f"snapshot_not_found:{path}")
        with path.open("r", encoding="utf-8") as fp:
            return json.load(fp)

    def load_from_path(self, file_path: str) -> dict[str, Any]:
        path = Path(file_path).resolve()
        expected_root = self.guard.namespace_path(self.namespace)
        if path != expected_root and expected_root not in path.parents:
            raise ValueError(f"snapshot_path_outside_namespace:{path}")
        with path.open("r", encoding="utf-8") as fp:
            return json.load(fp)
