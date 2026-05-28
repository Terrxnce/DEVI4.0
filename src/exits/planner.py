from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.core.enums import Direction, Regime, StructureType, Timeframe
from src.core.models import ContextSnapshot, DetectedStructure, ExitPlan
from src.context.references import PriceReferenceLevels

if TYPE_CHECKING:
    from src.context.session_levels import SessionLevels
from src.exits.tp_diagnostics import (
    TPCandidateView,
    add_found,
    add_rejected,
    new_tp_debug,
    set_selected,
)
from src.exits.validator import validate_exit_plan


class ExitFailure(Exception):
    def __init__(self, failure_code: str, tp_debug: dict[str, Any]) -> None:
        super().__init__(failure_code)
        self.failure_code = failure_code
        self.tp_debug = tp_debug


@dataclass(frozen=True)
class SLChoice:
    price: float
    source: str


@dataclass(frozen=True)
class TPChoice:
    price: float
    source: str
    kind: str
    rr: float
    distance_atr: float
    quality: float
    age_bars: int


STRUCTURE_SL_TYPE_WEIGHT: dict[StructureType, float] = {
    StructureType.ORDER_BLOCK: 1.0,
    StructureType.BREAK_OF_STRUCTURE: 0.9,
    StructureType.ENGULFING: 0.8,
    StructureType.REJECTION: 0.8,
    StructureType.LIQUIDITY_SWEEP: 0.85,
    StructureType.FAIR_VALUE_GAP: 0.7,
}

TP_SOURCE_WEIGHT: dict[str, float] = {
    "M15_STRUCTURE": 1.0,
    "H1_STRUCTURE": 1.1,
    "SWING": 0.95,
    "PRIOR_DAY": 0.9,
    "PRIOR_SESSION": 0.85,
    # Prior session H/L used as natural TP for sweep reversal setups.
    # Weighted slightly above H1_STRUCTURE — this level is the narrative's
    # structural target (return to the other side of the session range).
    "SESSION_LEVEL": 1.05,
}


def _risk_distance(entry: float, sl: float, direction: Direction) -> float:
    return (entry - sl) if direction == Direction.BULLISH else (sl - entry)


def _tp_distance(entry: float, tp: float, direction: Direction) -> float:
    return (tp - entry) if direction == Direction.BULLISH else (entry - tp)


def _regime_sl_floor_atr(regime: Regime, exits_cfg: dict[str, Any]) -> float:
    trending_floor = float(exits_cfg["min_sl_depth_atr_trending"])
    neutral_floor = float(exits_cfg["min_sl_depth_atr_neutral"])
    base_floor = float(exits_cfg["min_sl_atr_mult"])

    if regime == Regime.NEUTRAL or regime == Regime.RANGING:
        return max(neutral_floor, base_floor)
    if regime == Regime.TRENDING:
        return max(trending_floor, base_floor)
    return max(trending_floor, base_floor)


def _pick_sl(
    entry_price: float,
    direction: Direction,
    context: ContextSnapshot,
    structures: list[DetectedStructure],
    atr: float,
    config: dict[str, Any],
) -> SLChoice:
    exits_cfg = config["exits"]
    floor_atr = _regime_sl_floor_atr(context.regime, exits_cfg)
    floor_distance = floor_atr * atr

    # Absolute pip floor — guards against ATR compression in quiet sessions.
    # min_sl_pips is optional; defaults to 0 (no pip floor) if not configured.
    min_sl_pips = float(exits_cfg.get("min_sl_pips", 0.0))
    if min_sl_pips > 0.0:
        point = float(config.get("instrument", {}).get("point", 0.00001))
        pip_size = point * 10.0  # 1 pip = 10 points for 5-decimal brokers
        pip_floor_distance = min_sl_pips * pip_size
        floor_distance = max(floor_distance, pip_floor_distance)

    sl_buffer = float(exits_cfg["sl_buffer_atr_mult"]) * atr
    min_quality = float(exits_cfg["min_sl_quality"])
    h1_weight = float(exits_cfg["sl_h1_tf_weight"])

    candidates: list[tuple[float, SLChoice]] = []

    for structure in structures:
        if structure.quality < min_quality:
            continue
        if direction == Direction.BULLISH and structure.direction != Direction.BULLISH:
            continue
        if direction == Direction.BEARISH and structure.direction != Direction.BEARISH:
            continue

        if direction == Direction.BULLISH:
            price = min(structure.price_low, structure.price_high) - sl_buffer
        else:
            price = max(structure.price_low, structure.price_high) + sl_buffer

        distance = _risk_distance(entry_price, price, direction)
        if distance <= 0:
            continue

        if distance < floor_distance:
            continue

        base_weight = STRUCTURE_SL_TYPE_WEIGHT.get(structure.structure_type, 0.5)
        tf_weight = h1_weight if structure.timeframe == Timeframe.H1 else 1.0
        score = (structure.quality * base_weight * tf_weight) / distance
        candidates.append((score, SLChoice(price=price, source=structure.structure_type.value)))

    if candidates:
        return sorted(candidates, key=lambda item: (-item[0], item[1].price, item[1].source))[0][1]

    fallback_mult = float(exits_cfg["atr_fallback_sl_mult"])
    fallback_distance = fallback_mult * atr
    if fallback_distance < floor_distance:
        raise ExitFailure("sl_too_close_for_regime_floor", new_tp_debug())

    if direction == Direction.BULLISH:
        fallback_price = entry_price - fallback_distance
    else:
        fallback_price = entry_price + fallback_distance
    return SLChoice(price=fallback_price, source="ATR_FALLBACK")


def _dedup_tp_candidates(candidates: list[TPChoice], atr: float) -> list[TPChoice]:
    if not candidates:
        return []
    sorted_candidates = sorted(candidates, key=lambda c: c.price)
    deduped: list[TPChoice] = []
    threshold = 0.3 * atr
    for candidate in sorted_candidates:
        if not deduped:
            deduped.append(candidate)
            continue
        if abs(candidate.price - deduped[-1].price) <= threshold:
            better = candidate if candidate.quality > deduped[-1].quality else deduped[-1]
            deduped[-1] = better
        else:
            deduped.append(candidate)
    return deduped


def _candidate_quality(source: str, structure_quality: float, age_bars: int, distance_atr: float) -> float:
    recency_factor = max(1.0 - min(age_bars, 40) / 40.0, 0.0)
    distance_factor = max(1.0 - abs(distance_atr - 2.0) / 6.0, 0.0)
    source_weight = TP_SOURCE_WEIGHT.get(source, 0.7)
    return (0.5 * structure_quality + 0.25 * recency_factor + 0.25 * distance_factor) * source_weight


def _collect_tp_candidates(
    entry_price: float,
    direction: Direction,
    sl_price: float,
    context: ContextSnapshot,
    structures: list[DetectedStructure],
    references: PriceReferenceLevels,
    atr: float,
    config: dict[str, Any],
    tp_structures: list[DetectedStructure] | None = None,
    session_levels: "SessionLevels | None" = None,
) -> tuple[list[TPChoice], dict[str, Any]]:
    tp_debug = new_tp_debug()
    exits_cfg = config["exits"]
    risk = _risk_distance(entry_price, sl_price, direction)
    min_rr = float(exits_cfg["min_rr"])
    max_distance_atr = float(exits_cfg["tp_h1_search_hard_cap_atr"])
    # Use the wider TP pool if provided, else fall back to the entry structures list.
    # SL anchoring (same-direction, recent) stays on the entry structures list always.
    structure_pool = tp_structures if tp_structures is not None else structures
    current_bar_index = max((s.bar_index for s in structures), default=0)

    raw_candidates: list[TPChoice] = []

    opposing_direction = Direction.BEARISH if direction == Direction.BULLISH else Direction.BULLISH
    for structure in structure_pool:
        if structure.direction != opposing_direction:
            continue

        if direction == Direction.BULLISH:
            tp_price = max(structure.price_low, structure.price_high)
            if tp_price <= entry_price:
                continue
        else:
            tp_price = min(structure.price_low, structure.price_high)
            if tp_price >= entry_price:
                continue

        source = "H1_STRUCTURE" if structure.timeframe == Timeframe.H1 else "M15_STRUCTURE"
        distance = _tp_distance(entry_price, tp_price, direction)
        distance_atr = distance / atr if atr > 0 else 0.0
        rr = distance / risk if risk > 0 else 0.0
        age = max(current_bar_index - structure.bar_index, 0)
        quality = _candidate_quality(source, structure.quality, age, distance_atr)
        raw_candidates.append(
            TPChoice(
                price=tp_price,
                source=source,
                kind="STRUCTURE",
                rr=rr,
                distance_atr=distance_atr,
                quality=quality,
                age_bars=age,
            )
        )

    if references.prominent_swing_high is not None and direction == Direction.BULLISH:
        if references.prominent_swing_high > entry_price:
            distance = _tp_distance(entry_price, references.prominent_swing_high, direction)
            risk_rr = distance / risk if risk > 0 else 0.0
            raw_candidates.append(
                TPChoice(
                    price=references.prominent_swing_high,
                    source="SWING",
                    kind="SWING",
                    rr=risk_rr,
                    distance_atr=distance / atr if atr > 0 else 0.0,
                    quality=_candidate_quality("SWING", 0.7, 0, distance / atr if atr > 0 else 0.0),
                    age_bars=0,
                )
            )
    if references.prominent_swing_low is not None and direction == Direction.BEARISH:
        if references.prominent_swing_low < entry_price:
            distance = _tp_distance(entry_price, references.prominent_swing_low, direction)
            risk_rr = distance / risk if risk > 0 else 0.0
            raw_candidates.append(
                TPChoice(
                    price=references.prominent_swing_low,
                    source="SWING",
                    kind="SWING",
                    rr=risk_rr,
                    distance_atr=distance / atr if atr > 0 else 0.0,
                    quality=_candidate_quality("SWING", 0.7, 0, distance / atr if atr > 0 else 0.0),
                    age_bars=0,
                )
            )

    if references.prior_day_high is not None and direction == Direction.BULLISH and references.prior_day_high > entry_price:
        distance = _tp_distance(entry_price, references.prior_day_high, direction)
        raw_candidates.append(
            TPChoice(
                price=references.prior_day_high,
                source="PRIOR_DAY",
                kind="REFERENCE",
                rr=distance / risk if risk > 0 else 0.0,
                distance_atr=distance / atr if atr > 0 else 0.0,
                quality=_candidate_quality("PRIOR_DAY", 0.65, 0, distance / atr if atr > 0 else 0.0),
                age_bars=0,
            )
        )
    if references.prior_day_low is not None and direction == Direction.BEARISH and references.prior_day_low < entry_price:
        distance = _tp_distance(entry_price, references.prior_day_low, direction)
        raw_candidates.append(
            TPChoice(
                price=references.prior_day_low,
                source="PRIOR_DAY",
                kind="REFERENCE",
                rr=distance / risk if risk > 0 else 0.0,
                distance_atr=distance / atr if atr > 0 else 0.0,
                quality=_candidate_quality("PRIOR_DAY", 0.65, 0, distance / atr if atr > 0 else 0.0),
                age_bars=0,
            )
        )

    if references.prior_session_high is not None and direction == Direction.BULLISH and references.prior_session_high > entry_price:
        distance = _tp_distance(entry_price, references.prior_session_high, direction)
        raw_candidates.append(
            TPChoice(
                price=references.prior_session_high,
                source="PRIOR_SESSION",
                kind="REFERENCE",
                rr=distance / risk if risk > 0 else 0.0,
                distance_atr=distance / atr if atr > 0 else 0.0,
                quality=_candidate_quality("PRIOR_SESSION", 0.6, 0, distance / atr if atr > 0 else 0.0),
                age_bars=0,
            )
        )
    if references.prior_session_low is not None and direction == Direction.BEARISH and references.prior_session_low < entry_price:
        distance = _tp_distance(entry_price, references.prior_session_low, direction)
        raw_candidates.append(
            TPChoice(
                price=references.prior_session_low,
                source="PRIOR_SESSION",
                kind="REFERENCE",
                rr=distance / risk if risk > 0 else 0.0,
                distance_atr=distance / atr if atr > 0 else 0.0,
                quality=_candidate_quality("PRIOR_SESSION", 0.6, 0, distance / atr if atr > 0 else 0.0),
                age_bars=0,
            )
        )

    # Session level TP candidates — prior session H (for BULLISH) or L (for BEARISH).
    # These are the natural targets for sweep reversal setups: price swept the session
    # extreme and is expected to return to the opposite side of the session range.
    # Safe to include for any setup — the quality/RR filter handles non-viable levels.
    if session_levels is not None and session_levels.prior_completed_sessions:
        prior_session = session_levels.prior_completed_sessions[0]
        if direction == Direction.BULLISH and prior_session.high > entry_price:
            distance = _tp_distance(entry_price, prior_session.high, direction)
            dist_atr = distance / atr if atr > 0 else 0.0
            raw_candidates.append(
                TPChoice(
                    price=prior_session.high,
                    source="SESSION_LEVEL",
                    kind="REFERENCE",
                    rr=distance / risk if risk > 0 else 0.0,
                    distance_atr=dist_atr,
                    quality=_candidate_quality("SESSION_LEVEL", 0.75, 0, dist_atr),
                    age_bars=0,
                )
            )
        if direction == Direction.BEARISH and prior_session.low < entry_price:
            distance = _tp_distance(entry_price, prior_session.low, direction)
            dist_atr = distance / atr if atr > 0 else 0.0
            raw_candidates.append(
                TPChoice(
                    price=prior_session.low,
                    source="SESSION_LEVEL",
                    kind="REFERENCE",
                    rr=distance / risk if risk > 0 else 0.0,
                    distance_atr=dist_atr,
                    quality=_candidate_quality("SESSION_LEVEL", 0.75, 0, dist_atr),
                    age_bars=0,
                )
            )

    deduped = _dedup_tp_candidates(raw_candidates, atr)
    filtered: list[TPChoice] = []
    for candidate in deduped:
        view = TPCandidateView(
            price=candidate.price,
            source_type=candidate.source,
            candidate_kind=candidate.kind,
            rr=candidate.rr,
            distance_atr=candidate.distance_atr,
            quality=candidate.quality,
            age_bars=candidate.age_bars,
        )
        add_found(tp_debug, view)

        if candidate.distance_atr > max_distance_atr:
            add_rejected(tp_debug, view, "tp_too_far", "distance_filter")
            continue
        tp_max_age = int(exits_cfg.get("tp_max_age_bars", 250))
        if candidate.age_bars > tp_max_age:
            add_rejected(tp_debug, view, "tp_too_old", "quality_filter")
            continue
        if candidate.rr < min_rr:
            add_rejected(tp_debug, view, "rr_below_floor", "rr_filter")
            continue
        filtered.append(candidate)

    filtered = sorted(
        filtered,
        key=lambda c: (-c.quality, c.age_bars, c.distance_atr, c.price, c.source, c.kind),
    )
    return (filtered, tp_debug)


def plan_exit(
    *,
    entry_price: float,
    direction: Direction,
    context: ContextSnapshot,
    structures: list[DetectedStructure],
    references: PriceReferenceLevels,
    atr: float,
    config: dict[str, Any],
    tp_structures: list[DetectedStructure] | None = None,
    session_levels: "SessionLevels | None" = None,
) -> tuple[ExitPlan, dict[str, Any]]:
    exits_cfg = config["exits"]

    sl_choice = _pick_sl(
        entry_price=entry_price,
        direction=direction,
        context=context,
        structures=structures,
        atr=atr,
        config=config,
    )

    candidates, tp_debug = _collect_tp_candidates(
        entry_price=entry_price,
        direction=direction,
        sl_price=sl_choice.price,
        context=context,
        structures=structures,
        references=references,
        atr=atr,
        config=config,
        tp_structures=tp_structures,
        session_levels=session_levels,
    )

    if not candidates:
        if bool(exits_cfg["rr_fallback_enabled"]):
            raise ExitFailure("rr_fallback_must_remain_disabled", tp_debug)
        raise ExitFailure("rr_fallback_disabled_no_structural_tp", tp_debug)

    selected = candidates[0]
    set_selected(
        tp_debug,
        TPCandidateView(
            price=selected.price,
            source_type=selected.source,
            candidate_kind=selected.kind,
            rr=selected.rr,
            distance_atr=selected.distance_atr,
            quality=selected.quality,
            age_bars=selected.age_bars,
        ),
    )

    plan = ExitPlan(
        stop_loss=sl_choice.price,
        take_profit=selected.price,
        risk_reward=selected.rr,
        sl_source=sl_choice.source,
        tp_source=selected.source,
        breakeven_trigger_r=float(config["exits"]["management"]["breakeven_at_r"]),
        session_close_exit=bool(config["exits"]["management"]["session_close_exit"]),
    )

    ok, code = validate_exit_plan(
        entry_price=entry_price,
        direction=direction,
        plan=plan,
        context=context,
        min_rr=float(exits_cfg["min_rr"]),
        min_rr_neutral=float(exits_cfg["min_rr_neutral"]),
    )
    if not ok:
        raise ExitFailure(code, tp_debug)

    if context.regime == Regime.NEUTRAL and plan.risk_reward < float(exits_cfg["min_rr_neutral"]):
        raise ExitFailure("neutral_rr_below_floor", tp_debug)

    return (plan, tp_debug)
