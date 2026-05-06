from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.config.schema import validate_config_dict


class ConfigError(Exception):
    pass


def load_config(config_path: str) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        raise ConfigError(f"config_not_found:{path}")

    with path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)

    validation = validate_config_dict(payload)
    if not validation.valid:
        joined = ";".join(validation.errors)
        raise ConfigError(f"config_validation_failed:{joined}")

    return payload


def config_hash(config: dict[str, Any]) -> str:
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
