"""Tests for H4 macro bias in contradiction evaluation.

H4 alone contradicting the setup → soft penalty: "h4_macro_contradiction"
H4 + H1 both contradicting the setup → hard reject: "h4_h1_double_contradiction"
H4 NEUTRAL → no effect on either hard rejects or soft penalties
H4 aligned with the setup → no penalty
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from src.core.enums import Direction, HTFAgreement, Regime, Session
from src.core.models import ContextSnapshot
from src.decision.contradictions import evaluate_hard_rejects, evaluate_soft_penalties


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(
    *,
    trend_m15: Direction = Direction.BULLISH,
    trend_h1: Direction = Direction.BULLISH,
    trend_h4: Direction = Direction.NEUTRAL,
    htf_agreement: HTFAgreement = HTFAgreement.AGREES,
    regime: Regime = Regime.TRENDING,
) -> ContextSnapshot:
    return ContextSnapshot(
        symbol="EURUSD",
        bar_time=datetime(2026, 5, 16, 10, 0, tzinfo=UTC),
        session=Session.LONDON,
        micro_window=False,
        trend_m15=trend_m15,
        trend_h1=trend_h1,
        htf_agreement=htf_agreement,
        regime=regime,
        atr_current=0.0012,
        atr_percentile=0.5,
        spread_atr_ratio=0.1,
        stale_entry=False,
        news_blocked=False,
        nearby_structures=[],
        trend_h4=trend_h4,
    )


# ---------------------------------------------------------------------------
# Hard rejects — H4 + H1 double contradiction
# ---------------------------------------------------------------------------


def test_h4_h1_double_contradiction_is_hard_reject() -> None:
    """Both H4 and H1 BEARISH, setup is BULLISH → hard reject."""
    ctx = _make_context(trend_h4=Direction.BEARISH, trend_h1=Direction.BEARISH)
    rejects = evaluate_hard_rejects(ctx, Direction.BULLISH, block_ranging_regime=True)
    assert "h4_h1_double_contradiction" in rejects


def test_h4_h1_double_contradiction_sell_setup() -> None:
    """Both H4 and H1 BULLISH, setup is BEARISH → hard reject."""
    ctx = _make_context(
        trend_m15=Direction.BEARISH,
        trend_h1=Direction.BULLISH,
        trend_h4=Direction.BULLISH,
        htf_agreement=HTFAgreement.CONTRADICTS,
    )
    rejects = evaluate_hard_rejects(ctx, Direction.BEARISH, block_ranging_regime=True)
    assert "h4_h1_double_contradiction" in rejects


def test_h4_contradiction_alone_does_not_hard_reject() -> None:
    """H4 contradicts but H1 agrees → NO hard reject from double-contradiction rule."""
    ctx = _make_context(
        trend_h1=Direction.BULLISH,
        trend_h4=Direction.BEARISH,
        htf_agreement=HTFAgreement.AGREES,
    )
    rejects = evaluate_hard_rejects(ctx, Direction.BULLISH, block_ranging_regime=False)
    assert "h4_h1_double_contradiction" not in rejects


def test_h4_neutral_no_hard_reject() -> None:
    """H4 NEUTRAL → never triggers double-contradiction hard reject."""
    ctx = _make_context(
        trend_h1=Direction.BEARISH,
        trend_h4=Direction.NEUTRAL,
        htf_agreement=HTFAgreement.CONTRADICTS,
    )
    rejects = evaluate_hard_rejects(ctx, Direction.BULLISH, block_ranging_regime=False)
    assert "h4_h1_double_contradiction" not in rejects


def test_h4_aligned_with_setup_no_hard_reject() -> None:
    """H4 agrees with setup → no double-contradiction hard reject."""
    ctx = _make_context(
        trend_h1=Direction.BULLISH,
        trend_h4=Direction.BULLISH,
    )
    rejects = evaluate_hard_rejects(ctx, Direction.BULLISH, block_ranging_regime=False)
    assert "h4_h1_double_contradiction" not in rejects


# ---------------------------------------------------------------------------
# Soft penalties — H4 single contradiction
# ---------------------------------------------------------------------------


def test_h4_contradiction_produces_soft_penalty() -> None:
    """H4 BEARISH with BULLISH setup → soft penalty."""
    ctx = _make_context(
        trend_m15=Direction.BULLISH,
        trend_h4=Direction.BEARISH,
    )
    penalties = evaluate_soft_penalties(ctx, Direction.BULLISH)
    assert "h4_macro_contradiction" in penalties


def test_h4_aligned_no_soft_penalty() -> None:
    """H4 agrees with setup → no h4 penalty."""
    ctx = _make_context(
        trend_m15=Direction.BULLISH,
        trend_h4=Direction.BULLISH,
    )
    penalties = evaluate_soft_penalties(ctx, Direction.BULLISH)
    assert "h4_macro_contradiction" not in penalties


def test_h4_neutral_no_soft_penalty() -> None:
    """H4 NEUTRAL → no h4 penalty regardless of setup direction."""
    ctx = _make_context(trend_h4=Direction.NEUTRAL)
    penalties = evaluate_soft_penalties(ctx, Direction.BULLISH)
    assert "h4_macro_contradiction" not in penalties


def test_h4_penalty_present_alongside_m15_mismatch() -> None:
    """Both H4 and M15 going against setup → both penalties recorded."""
    ctx = _make_context(
        trend_m15=Direction.BEARISH,
        trend_h4=Direction.BEARISH,
    )
    penalties = evaluate_soft_penalties(ctx, Direction.BULLISH)
    assert "h4_macro_contradiction" in penalties
    assert "m15_trend_mismatch" in penalties


# ---------------------------------------------------------------------------
# Default trend_h4 = NEUTRAL when not supplied
# ---------------------------------------------------------------------------


def test_context_snapshot_defaults_trend_h4_to_neutral() -> None:
    """Existing code that doesn't pass trend_h4 gets NEUTRAL by default."""
    from src.core.enums import Regime, Session
    ctx = ContextSnapshot(
        symbol="EURUSD",
        bar_time=datetime(2026, 5, 16, 10, 0, tzinfo=UTC),
        session=Session.LONDON,
        micro_window=False,
        trend_m15=Direction.BULLISH,
        trend_h1=Direction.BULLISH,
        htf_agreement=HTFAgreement.AGREES,
        regime=Regime.TRENDING,
        atr_current=0.001,
        atr_percentile=0.5,
        spread_atr_ratio=0.1,
        stale_entry=False,
        news_blocked=False,
        nearby_structures=[],
        # trend_h4 intentionally omitted
    )
    assert ctx.trend_h4 == Direction.NEUTRAL
    # And no unexpected hard rejects or penalties
    rejects = evaluate_hard_rejects(ctx, Direction.BULLISH, block_ranging_regime=False)
    assert "h4_h1_double_contradiction" not in rejects
    penalties = evaluate_soft_penalties(ctx, Direction.BULLISH)
    assert "h4_macro_contradiction" not in penalties
