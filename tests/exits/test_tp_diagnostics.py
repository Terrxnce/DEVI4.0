from __future__ import annotations

from src.exits.tp_diagnostics import (
    TP_DEBUG_SCHEMA_VERSION,
    TPCandidateView,
    add_found,
    add_rejected,
    new_tp_debug,
    set_selected,
)


def _candidate() -> TPCandidateView:
    return TPCandidateView(
        price=1.1034,
        source_type="H1_STRUCTURE",
        candidate_kind="STRUCTURE",
        rr=1.56789,
        distance_atr=3.45678,
        quality=0.87654,
        age_bars=4,
    )


def test_new_tp_debug_schema_and_shape() -> None:
    payload = new_tp_debug()

    assert payload["schema_version"] == TP_DEBUG_SCHEMA_VERSION
    assert payload["found"] == []
    assert payload["rejected"] == []
    assert payload["selected"] == {}


def test_add_found_adds_candidate_payload() -> None:
    payload = new_tp_debug()
    candidate = _candidate()

    add_found(payload, candidate)

    assert len(payload["found"]) == 1
    assert payload["found"][0]["source_type"] == "H1_STRUCTURE"
    assert payload["found"][0]["candidate_kind"] == "STRUCTURE"


def test_add_rejected_sets_reason_and_stage() -> None:
    payload = new_tp_debug()
    candidate = _candidate()

    add_rejected(payload, candidate, "rr_below_floor", "rr_filter")

    assert len(payload["rejected"]) == 1
    rejected = payload["rejected"][0]
    assert rejected["rejection_reason"] == "rr_below_floor"
    assert rejected["rejection_stage"] == "rr_filter"


def test_set_selected_overwrites_selected_payload() -> None:
    payload = new_tp_debug()
    first = _candidate()
    second = TPCandidateView(
        price=1.1040,
        source_type="SWING",
        candidate_kind="SWING",
        rr=1.8,
        distance_atr=3.9,
        quality=0.82,
        age_bars=2,
    )

    set_selected(payload, first)
    set_selected(payload, second)

    assert payload["selected"]["source_type"] == "SWING"
    assert payload["selected"]["candidate_kind"] == "SWING"
    assert payload["selected"]["price"] == 1.104
