from __future__ import annotations

from src.core.enums import Direction
from src.core.models import (
    ContextSnapshot,
    ConfluenceResult,
    DetectedStructure,
    ExitPlan,
    RiskVerdict,
    TradeIntent,
)
from src.execution.paper_adapter import PaperExecutionAdapter, PaperFillResult


def test_paper_adapter_never_calls_broker() -> None:
    """Paper adapter is simulation-only; no broker methods are invoked."""
    adapter = PaperExecutionAdapter()
    
    # Verify adapter has no broker connection attributes
    assert not hasattr(adapter, '_broker')
    assert not hasattr(adapter, '_mt5')
    assert not hasattr(adapter, '_api_client')
    assert not hasattr(adapter, '_connection')
    
    # Verify ticket counter is internal/synthetic
    assert hasattr(adapter, '_ticket_counter')
    assert isinstance(adapter._ticket_counter, int)


def test_paper_adapter_simulates_slippage_without_execution() -> None:
    """Paper adapter calculates synthetic slippage without any real execution."""
    adapter = PaperExecutionAdapter()
    
    # Create minimal intent for testing
    from datetime import datetime, UTC
    from src.core.enums import ConfidenceTier, HTFAgreement, Regime, Session, SetupClass, StructureType, Timeframe
    
    trigger = DetectedStructure(
        structure_type=StructureType.ORDER_BLOCK,
        direction=Direction.BULLISH,
        price_high=1.1010,
        price_low=1.0990,
        quality=0.9,
        age_bars=5,
        atr_relative_size=2.0,
        timeframe=Timeframe.M15,
        bar_index=100,
        bar_time=datetime.now(UTC),
    )
    
    context = ContextSnapshot(
        symbol="EURUSD",
        bar_time=datetime.now(UTC),
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
    )
    
    confluence = ConfluenceResult(
        setup_class=SetupClass.OB_WITH_BOS,
        direction=Direction.BULLISH,
        primary_trigger=trigger,
        structural_confirmations=[],
        structural_labels=["test"],
        minor_confluences=[],
        hard_rejects=[],
        soft_penalties=[],
        structural_count=1,
        minor_count=0,
        quality_penalty=0.0,
        effective_quality=0.9,
        confluence_pass=True,
        confidence_tier=ConfidenceTier.A,
        tier_reason="test",
    )
    
    risk_verdict = RiskVerdict(approved=True, lot_size=0.1, actual_risk_pct=0.4, intended_risk_pct=0.4, reason="test")
    
    intent = TradeIntent(
        trade_id="test-002",
        symbol="EURUSD",
        direction=Direction.BULLISH,
        setup_class=SetupClass.OB_WITH_BOS,
        confidence_tier=ConfidenceTier.A,
        session=Session.LONDON,
        entry_price=1.1000,
        exit_plan=ExitPlan(
            stop_loss=1.0980,
            take_profit=1.1040,
            risk_reward=2.0,
            sl_source="OB",
            tp_source="BOS",
            breakeven_trigger_r=1.0,
            session_close_exit=True,
        ),
        risk_verdict=risk_verdict,
        confluence=confluence,
        context=context,
        config_hash="abc123",
        bar_time=datetime.now(UTC),
    )
    
    result = adapter.execute(intent, spread_at_decision=0.0002, spread_at_fill=0.0003)
    
    # Slippage is calculated mathematically, not from real fill
    expected_slippage = 0.0003  # full spread_at_fill (BUY fills at ask = bid + spread)
    assert abs(result.slippage - expected_slippage) < 1e-9
    assert abs(result.actual_fill - (1.1000 + expected_slippage)) < 1e-9
    assert result.order_status == "FILLED"


def test_paper_adapter_bearish_direction_calculations() -> None:
    """Paper adapter correctly calculates bearish fills synthetically."""
    from datetime import datetime, UTC
    from src.core.enums import ConfidenceTier, HTFAgreement, Regime, Session, SetupClass, StructureType, Timeframe
    
    adapter = PaperExecutionAdapter()
    
    trigger = DetectedStructure(
        structure_type=StructureType.ORDER_BLOCK,
        direction=Direction.BEARISH,
        price_high=1.1020,
        price_low=1.1000,
        quality=0.9,
        age_bars=5,
        atr_relative_size=2.0,
        timeframe=Timeframe.M15,
        bar_index=100,
        bar_time=datetime.now(UTC),
    )
    
    context = ContextSnapshot(
        symbol="EURUSD",
        bar_time=datetime.now(UTC),
        session=Session.LONDON,
        micro_window=False,
        trend_m15=Direction.BEARISH,
        trend_h1=Direction.BEARISH,
        htf_agreement=HTFAgreement.AGREES,
        regime=Regime.TRENDING,
        atr_current=0.001,
        atr_percentile=0.5,
        spread_atr_ratio=0.1,
        stale_entry=False,
        news_blocked=False,
        nearby_structures=[],
    )
    
    confluence = ConfluenceResult(
        setup_class=SetupClass.OB_WITH_BOS,
        direction=Direction.BEARISH,
        primary_trigger=trigger,
        structural_confirmations=[],
        structural_labels=["test"],
        minor_confluences=[],
        hard_rejects=[],
        soft_penalties=[],
        structural_count=1,
        minor_count=0,
        quality_penalty=0.0,
        effective_quality=0.9,
        confluence_pass=True,
        confidence_tier=ConfidenceTier.A,
        tier_reason="test",
    )
    
    risk_verdict = RiskVerdict(approved=True, lot_size=0.1, actual_risk_pct=0.4, intended_risk_pct=0.4, reason="test")
    
    intent = TradeIntent(
        trade_id="test-003",
        symbol="EURUSD",
        direction=Direction.BEARISH,
        setup_class=SetupClass.OB_WITH_BOS,
        confidence_tier=ConfidenceTier.A,
        session=Session.LONDON,
        entry_price=1.1000,
        exit_plan=ExitPlan(
            stop_loss=1.1020,
            take_profit=1.0960,
            risk_reward=2.0,
            sl_source="OB",
            tp_source="BOS",
            breakeven_trigger_r=1.0,
            session_close_exit=True,
        ),
        risk_verdict=risk_verdict,
        confluence=confluence,
        context=context,
        config_hash="abc123",
        bar_time=datetime.now(UTC),
    )
    
    result = adapter.execute(intent, spread_at_decision=0.0002)
    
    assert result.side == "SELL"
    assert result.actual_fill < intent.entry_price  # Fill below entry for bearish
    assert result.order_status == "FILLED"


def test_no_broker_imports_in_execution_module() -> None:
    """Verify execution module has no broker/MT5/real trading imports."""
    import src.execution.gate as gate_module
    import src.execution.paper_adapter as paper_module
    
    import inspect
    gate_source = inspect.getsource(gate_module)
    paper_source = inspect.getsource(paper_module)
    
    # Check for actual broker API imports (not field names)
    forbidden_patterns = [
        "import mt5",
        "from mt5",
        "import metatrader5",
        "from metatrader5",
        "order_send(",
        "order_place(",
        "mt5.",
    ]
    
    for pattern in forbidden_patterns:
        assert pattern not in gate_source.lower(), f"Found forbidden pattern in gate: {pattern}"
        assert pattern not in paper_source.lower(), f"Found forbidden pattern in paper_adapter: {pattern}"
