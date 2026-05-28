from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.core.models import ContextSnapshot
from src.decision.engine import DecisionOutcome
from src.core.enums import Namespace
from src.ops.decision_records import build_decision_record
from src.ops.namespace_guard import NamespaceGuard
from src.ops.schema_validator import validate_decision_record

# SupabaseWriter is optional — imported lazily to avoid hard dependency.
# Type annotation only; actual instance is passed in at construction time.
try:
    from src.ops.supabase_writer import SupabaseWriter as _SupabaseWriter
except ImportError:  # pragma: no cover
    _SupabaseWriter = None  # type: ignore[assignment,misc]


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    build_id: str
    config_hash: str
    namespace: str
    mode: str
    data_source: str
    start_time: str


class TelemetryWriter:
    def __init__(
        self,
        logs_root: str,
        namespace: Namespace,
        supabase_writer: Any | None = None,
    ) -> None:
        self.namespace = namespace
        self.guard = NamespaceGuard(logs_root)
        self.guard.ensure_namespace_dirs(namespace)
        # Optional remote mirror. None means Supabase writes are skipped silently.
        self._supabase: Any | None = supabase_writer

    def _daily_jsonl_path(self, kind: str, when: datetime | None = None) -> Path:
        ts = when or datetime.now(tz=UTC)
        filename = f"{kind}_{ts.date().isoformat()}.jsonl"
        return self.guard.namespace_path(self.namespace, filename)

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(payload, separators=(",", ":"), sort_keys=True))
            fp.write("\n")

    def write_decision(self, record: dict[str, Any]) -> None:
        validation = validate_decision_record(record)
        hydrated = dict(record)
        hydrated["record_valid"] = validation.valid
        hydrated["record_invalid_reasons"] = validation.reasons
        self._append_jsonl(self._daily_jsonl_path("decisions"), hydrated)

    def write_decision_outcome(
        self,
        *,
        run_id: str,
        scan_id: str,
        config_hash: str,
        snapshot_id: str,
        context: ContextSnapshot,
        outcome: DecisionOutcome,
        decision_id: str | None = None,
        timestamp: datetime | None = None,
        entry_price: float | None = None,
        instrument_point: float | None = None,
    ) -> None:
        record = build_decision_record(
            run_id=run_id,
            scan_id=scan_id,
            config_hash=config_hash,
            snapshot_id=snapshot_id,
            context=context,
            outcome=outcome,
            decision_id=decision_id,
            timestamp=timestamp,
            entry_price=entry_price,
            instrument_point=instrument_point,
        )
        self.write_decision(record)

    def write_scan(self, record: dict[str, Any]) -> None:
        self._append_jsonl(self._daily_jsonl_path("scans"), record)

    def write_trade(self, record: dict[str, Any]) -> None:
        self._append_jsonl(self._daily_jsonl_path("trades"), record)
        if self._supabase is not None:
            self._supabase.write_trade(record)

    def write_trade_close(self, record: dict[str, Any]) -> None:
        """Append a trade close record to the trades JSONL.

        Uses the same file as write_trade. Consumers resolve final trade state
        by taking the last record per trade_id — the open record has
        status='open', the close record has status='closed' with full exit data.

        Required fields: event='trade_close', trade_id, ticket, status='closed',
        close_price, close_pnl, close_reason, close_time, timestamp.
        """
        self._append_jsonl(self._daily_jsonl_path("trades"), record)
        if self._supabase is not None:
            self._supabase.write_trade_close(record)

    def write_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._append_jsonl(self._daily_jsonl_path("snapshots"), snapshot)

    def write_live_order(self, record: dict[str, Any]) -> None:
        self._append_jsonl(self._daily_jsonl_path("live_orders"), record)

    def write_position_event(self, record: dict[str, Any]) -> None:
        """Write a position lifecycle event to position_events JSONL.

        Open event fields: event, ticket, decision_id, trade_id, run_id,
        symbol, side, lot_size, open_price, sl, tp, setup_class,
        confidence_tier, timestamp.

        Close event fields: event, ticket, symbol, side, lot_size,
        open_price, close_price, close_time, close_reason, close_pnl,
        trade_id, decision_id, run_id, timestamp.
        """
        self._append_jsonl(self._daily_jsonl_path("position_events"), record)

    def write_run_manifest(self, manifest: RunManifest) -> Path:
        path = self.guard.namespace_path(self.namespace, "reports/run_manifest.json")
        with path.open("w", encoding="utf-8") as fp:
            json.dump(manifest.__dict__, fp, indent=2, sort_keys=True)
        return path
