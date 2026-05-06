from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from typing import Any

from src.core.enums import (
    ConfidenceTier,
    Direction,
    HTFAgreement,
    InstrumentClass,
    Regime,
    Session,
    SetupClass,
    StructureType,
    Timeframe,
)


@dataclass(frozen=True)
class Bar:
    symbol: str
    timeframe: Timeframe
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    bar_index: int


@dataclass(frozen=True)
class DetectedStructure:
    structure_type: StructureType
    direction: Direction
    price_high: float
    price_low: float
    quality: float
    age_bars: int
    atr_relative_size: float
    timeframe: Timeframe
    bar_index: int
    bar_time: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextSnapshot:
    symbol: str
    bar_time: datetime
    session: Session
    micro_window: bool
    trend_m15: Direction
    trend_h1: Direction
    htf_agreement: HTFAgreement
    regime: Regime
    atr_current: float
    atr_percentile: float
    spread_atr_ratio: float
    stale_entry: bool
    news_blocked: bool
    nearby_structures: list[DetectedStructure]


@dataclass(frozen=True)
class ConfluenceResult:
    setup_class: SetupClass
    direction: Direction
    primary_trigger: DetectedStructure
    structural_confirmations: list[DetectedStructure]
    structural_labels: list[str]
    minor_confluences: list[str]
    hard_rejects: list[str]
    soft_penalties: list[str]
    structural_count: int
    minor_count: int
    quality_penalty: float
    effective_quality: float
    confluence_pass: bool
    confidence_tier: ConfidenceTier
    tier_reason: str


@dataclass(frozen=True)
class ExitPlan:
    stop_loss: float
    take_profit: float
    risk_reward: float
    sl_source: str
    tp_source: str
    breakeven_trigger_r: float
    session_close_exit: bool


@dataclass(frozen=True)
class RiskVerdict:
    approved: bool
    lot_size: float
    actual_risk_pct: float
    intended_risk_pct: float
    reason: str


@dataclass(frozen=True)
class TradeIntent:
    trade_id: str
    symbol: str
    direction: Direction
    setup_class: SetupClass
    confidence_tier: ConfidenceTier
    session: Session
    entry_price: float
    exit_plan: ExitPlan
    risk_verdict: RiskVerdict
    confluence: ConfluenceResult
    context: ContextSnapshot
    config_hash: str
    bar_time: datetime


@dataclass(frozen=True)
class InstrumentProfile:
    symbol: str
    instrument_class: InstrumentClass
    tick_size: float
    lot_step: float
    min_lot: float
    max_lot: float
    digits: int
    point: float
    contract_size: float
    noise_floor_atr_mult: float
    spread_warn_atr_mult: float
    stale_entry_atr_mult: float


@dataclass(frozen=True)
class DecisionRecord:
    run_id: str
    scan_id: str
    decision_id: str
    timestamp: datetime
    symbol: str
    session: Session
    execution_side: str
    stage_entered: str
    stage_failed: str
    failure_code: str
    failure_detail: str
    final_decision: str
    final_decision_reason: str
    config_hash: str
    snapshot_id: str
    tp_debug: dict[str, Any]
    record_valid: bool
    record_invalid_reasons: list[str]
    sl_distance_price: float = 0.0
    sl_distance_points: float = 0.0
    sl_distance_pips: float = 0.0


@dataclass(frozen=True)
class SnapshotRecord:
    snapshot_id: str
    symbol: str
    decision_timestamp: datetime
    session: Session
    m15_bars: list[Bar]
    h1_bars: list[Bar]
    atr_m15: float
    atr_h1: float
    spread: float
    detected_structures: list[DetectedStructure]
    context_snapshot: ContextSnapshot
    config_hash: str
    symbol_profile: InstrumentProfile


def to_primitive(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        enum_value = getattr(value, "value", None)
        if isinstance(enum_value, str):
            return enum_value
    if is_dataclass(value):
        return {k: to_primitive(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): to_primitive(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_primitive(v) for v in value]
    return value
