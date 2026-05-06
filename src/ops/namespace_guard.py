from __future__ import annotations

from pathlib import Path

from src.core.enums import Namespace


class NamespaceViolationError(Exception):
    pass


class NamespaceGuard:
    def __init__(self, logs_root: str) -> None:
        self.logs_root = Path(logs_root).resolve()

    def namespace_path(self, namespace: Namespace, relative_path: str = "") -> Path:
        base = (self.logs_root / namespace.value).resolve()
        target = (base / relative_path).resolve()
        self.assert_write_allowed(namespace=namespace, target_path=target)
        return target

    def assert_write_allowed(self, namespace: Namespace, target_path: Path) -> None:
        target = target_path.resolve()
        prod_root = (self.logs_root / Namespace.PROD.value).resolve()

        if namespace in (Namespace.EVAL, Namespace.SHADOW):
            if target == prod_root or prod_root in target.parents:
                raise NamespaceViolationError(
                    f"namespace_write_blocked:{namespace.value}:{target}"
                )

        expected_root = (self.logs_root / namespace.value).resolve()
        if target != expected_root and expected_root not in target.parents:
            raise NamespaceViolationError(
                f"namespace_root_mismatch:{namespace.value}:{target}"
            )

    def ensure_namespace_dirs(self, namespace: Namespace) -> None:
        for rel in ("", "snapshots", "reports"):
            path = self.namespace_path(namespace, rel)
            path.mkdir(parents=True, exist_ok=True)
