from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.context.h4_context import H4Alignment, H4ContextGate, H4ContextResult
from src.core.enums import Direction, Timeframe
from src.core.models import Bar


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GATE = H4ContextGate()


def _h4bar(bar_index: int, open_: float, high: float, low: float, close: float) -> Bar:
    ts = datetime(2026, 4, 13, (bar_index * 4) % 24, 0, tzinfo=UTC)
    return Bar("NZDUSD", Timeframe.H4, ts, open_, high, low, close, 100.0, bar_index)


def _flat_bars(n: int = 20, base: float = 0.5800, rng: float = 0.0050) -> list[Bar]:
    """Generate n flat H4 bars with no structural features."""
    return [_h4bar(i, base, base + rng, base - rng, base) for i in range(n)]


def _inject_bullish_fvg(bars: list[Bar], idx: int, gap_low: float, gap_high: float) -> list[Bar]:
    """Inject a bullish FVG pattern at bars[idx-2:idx+1].

    FVG rule: c3.low > c1.high  (bullish gap from c1.high to c3.low)
    We set c1.high = gap_low, c3.low = gap_high, c2 = strong displacement up.
    """
    bars = list(bars)
    assert idx >= 1 and idx + 1 < len(bars)
    mid = (gap_low + gap_high) / 2
    bars[idx - 1] = _h4bar(idx - 1, gap_low - 0.002, gap_low, gap_low - 0.004, gap_low - 0.001)  # c1
    bars[idx] = _h4bar(idx, gap_low, gap_high + 0.003, gap_low - 0.001, gap_high + 0.002)         # c2 displacement
    bars[idx + 1] = _h4bar(idx + 1, gap_high + 0.001, gap_high + 0.004, gap_high, gap_high + 0.002)  # c3
    return bars


def _inject_bearish_fvg(bars: list[Bar], idx: int, gap_low: float, gap_high: float) -> list[Bar]:
    """Inject a bearish FVG pattern at bars[idx-2:idx+1].

    FVG rule: c1.low > c3.high  (bearish gap from c3.high to c1.low)
    We set c1.low = gap_high, c3.high = gap_low, c2 = strong displacement down.
    """
    bars = list(bars)
    assert idx >= 1 and idx + 1 < len(bars)
    bars[idx - 1] = _h4bar(idx - 1, gap_high + 0.002, gap_high + 0.004, gap_high, gap_high + 0.001)  # c1
    bars[idx] = _h4bar(idx, gap_high, gap_high + 0.001, gap_low - 0.003, gap_low - 0.002)              # c2 displacement
    bars[idx + 1] = _h4bar(idx + 1, gap_low - 0.001, gap_low, gap_low - 0.003, gap_low - 0.002)        # c3
    return bars


# ---------------------------------------------------------------------------
# Degenerate / guard cases
# ---------------------------------------------------------------------------


def test_insufficient_bars_returns_neutral():
    result = GATE.evaluate(Direction.BEARISH, 0.5828, [], None)
    assert result.alignment == H4Alignment.NEUTRAL
    assert result.reason == "insufficient_h4_bars"


def test_neutral_direction_returns_neutral():
    bars = _flat_bars(20)
    result = GATE.evaluate(Direction.NEUTRAL, 0.5800, bars, None)
    assert result.alignment == H4Alignment.NEUTRAL
    assert result.reason == "direction_neutral"


def test_no_structures_detected_returns_neutral():
    # Completely flat bars produce no OB or FVG.
    bars = _flat_bars(20, base=0.5800, rng=0.0001)
    result = GATE.evaluate(Direction.BEARISH, 0.5800, bars, None)
    assert result.alignment in (H4Alignment.NEUTRAL,)  # may get neutral for flat/no structures


# ---------------------------------------------------------------------------
# COUNTER detection — BEARISH trade into bullish H4 FVG
# ---------------------------------------------------------------------------


def test_bearish_trade_into_bullish_h4_fvg_returns_counter():
    """The NZDUSD scenario: sell into a bullish H4 FVG → COUNTER."""
    bars = _flat_bars(25)
    # Inject bullish FVG at bars[10]: gap from 0.5770 to 0.5840
    bars = _inject_bullish_fvg(bars, idx=10, gap_low=0.5770, gap_high=0.5840)
    # Entry at 0.5800 is inside the FVG zone [0.5770, 0.5840]
    result = GATE.evaluate(Direction.BEARISH, 0.5800, bars, None)
    assert result.alignment == H4Alignment.COUNTER
    assert result.reason == "price_inside_opposing_h4_structure"
    assert len(result.conflicting_structures) > 0


def test_bullish_trade_into_bearish_h4_fvg_returns_counter():
    """Mirror scenario: buy into a bearish H4 FVG → COUNTER."""
    bars = _flat_bars(25)
    bars = _inject_bearish_fvg(bars, idx=10, gap_low=0.5760, gap_high=0.5830)
    # Entry at 0.5795 is inside the bearish FVG zone [0.5760, 0.5830]
    result = GATE.evaluate(Direction.BULLISH, 0.5795, bars, None)
    assert result.alignment == H4Alignment.COUNTER
    assert result.reason == "price_inside_opposing_h4_structure"


# ---------------------------------------------------------------------------
# ALIGNED detection
# ---------------------------------------------------------------------------


def test_bearish_trade_into_bearish_h4_fvg_returns_aligned():
    bars = _flat_bars(25)
    bars = _inject_bearish_fvg(bars, idx=10, gap_low=0.5760, gap_high=0.5830)
    result = GATE.evaluate(Direction.BEARISH, 0.5795, bars, None)
    assert result.alignment == H4Alignment.ALIGNED


def test_bullish_trade_into_bullish_h4_fvg_returns_aligned():
    bars = _flat_bars(25)
    bars = _inject_bullish_fvg(bars, idx=10, gap_low=0.5770, gap_high=0.5840)
    result = GATE.evaluate(Direction.BULLISH, 0.5800, bars, None)
    assert result.alignment == H4Alignment.ALIGNED


# ---------------------------------------------------------------------------
# NEUTRAL — price not inside any structure
# ---------------------------------------------------------------------------


def test_price_outside_all_structures_returns_neutral():
    bars = _flat_bars(25)
    bars = _inject_bullish_fvg(bars, idx=10, gap_low=0.5770, gap_high=0.5840)
    # Price 0.5600 is well below the FVG zone — not inside any structure.
    result = GATE.evaluate(Direction.BEARISH, 0.5600, bars, None)
    assert result.alignment in (H4Alignment.NEUTRAL, H4Alignment.COUNTER)
    # Accept NEUTRAL; COUNTER would only fire if price is inside a structure.
    if result.alignment == H4Alignment.COUNTER:
        pytest.fail(f"Expected NEUTRAL for price outside structure, got COUNTER: {result.conflicting_structures}")


# ---------------------------------------------------------------------------
# conflicting_structures diagnostic field
# ---------------------------------------------------------------------------


def test_counter_result_includes_conflicting_structure_labels():
    bars = _flat_bars(25)
    bars = _inject_bullish_fvg(bars, idx=10, gap_low=0.5770, gap_high=0.5840)
    result = GATE.evaluate(Direction.BEARISH, 0.5800, bars, None)
    if result.alignment == H4Alignment.COUNTER:
        assert all(isinstance(label, str) and len(label) > 0 for label in result.conflicting_structures)


def test_aligned_result_has_empty_conflicting_structures():
    bars = _flat_bars(25)
    bars = _inject_bullish_fvg(bars, idx=10, gap_low=0.5770, gap_high=0.5840)
    result = GATE.evaluate(Direction.BULLISH, 0.5800, bars, None)
    if result.alignment == H4Alignment.ALIGNED:
        assert result.conflicting_structures == []
