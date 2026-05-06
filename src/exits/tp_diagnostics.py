from __future__ import annotations

from dataclasses import dataclass
from typing import Any


TP_DEBUG_SCHEMA_VERSION = "2"


@dataclass(frozen=True)
class TPCandidateView:
    price: float
    source_type: str
    candidate_kind: str
    rr: float
    distance_atr: float
    quality: float
    age_bars: int



def candidate_payload(candidate: TPCandidateView) -> dict[str, Any]:
    return {
        "price": candidate.price,
        "source_type": candidate.source_type,
        "candidate_kind": candidate.candidate_kind,
        "rr": round(candidate.rr, 4),
        "distance_atr": round(candidate.distance_atr, 4),
        "quality": round(candidate.quality, 4),
        "age_bars": int(candidate.age_bars),
    }



def new_tp_debug() -> dict[str, Any]:
    return {
        "schema_version": TP_DEBUG_SCHEMA_VERSION,
        "found": [],
        "rejected": [],
        "selected": {},
    }



def add_found(tp_debug: dict[str, Any], candidate: TPCandidateView) -> None:
    tp_debug["found"].append(candidate_payload(candidate))



def add_rejected(
    tp_debug: dict[str, Any],
    candidate: TPCandidateView,
    rejection_reason: str,
    rejection_stage: str,
) -> None:
    payload = candidate_payload(candidate)
    payload["rejection_reason"] = rejection_reason
    payload["rejection_stage"] = rejection_stage
    tp_debug["rejected"].append(payload)



def set_selected(tp_debug: dict[str, Any], candidate: TPCandidateView) -> None:
    tp_debug["selected"] = candidate_payload(candidate)
