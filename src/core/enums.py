from __future__ import annotations

from enum import Enum


class Timeframe(str, Enum):
    M15 = "M15"
    H1 = "H1"


class Direction(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class Session(str, Enum):
    ASIA = "ASIA"
    LONDON = "LONDON"
    NY_AM = "NY_AM"
    NY_PM = "NY_PM"
    CLOSED = "CLOSED"


class HTFAgreement(str, Enum):
    AGREES = "AGREES"
    NEUTRAL = "NEUTRAL"
    CONTRADICTS = "CONTRADICTS"


class Regime(str, Enum):
    TRENDING = "TRENDING"
    NEUTRAL = "NEUTRAL"
    RANGING = "RANGING"
    EXPANDING = "EXPANDING"


class StructureType(str, Enum):
    ORDER_BLOCK = "ORDER_BLOCK"
    FAIR_VALUE_GAP = "FAIR_VALUE_GAP"
    BREAK_OF_STRUCTURE = "BREAK_OF_STRUCTURE"
    LIQUIDITY_SWEEP = "LIQUIDITY_SWEEP"
    REJECTION = "REJECTION"
    ENGULFING = "ENGULFING"


class SetupClass(str, Enum):
    OB_WITH_BOS = "OB_WITH_BOS"
    OB_WITH_FVG = "OB_WITH_FVG"
    OB_WITH_ENGULFING = "OB_WITH_ENGULFING"
    REJECTION_WITH_FVG = "REJECTION_WITH_FVG"
    SWEEP_WITH_OB = "SWEEP_WITH_OB"


class ConfidenceTier(str, Enum):
    A = "A"
    B = "B"
    C = "C"


class InstrumentClass(str, Enum):
    FX = "FX"
    XAUUSD = "XAUUSD"
    INDICES = "INDICES"
    CRYPTO = "CRYPTO"


class Namespace(str, Enum):
    PROD = "prod"
    EVAL = "eval"
    SHADOW = "shadow"


class Mode(str, Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    SHADOW = "shadow"
    LIVE = "live"


class FinalDecision(str, Enum):
    REJECTED_INSUFFICIENT_DATA = "REJECTED_INSUFFICIENT_DATA"
    REJECTED_NO_STRUCTURES = "REJECTED_NO_STRUCTURES"
    REJECTED_CONFLUENCE = "REJECTED_CONFLUENCE"
    REJECTED_EXIT_PLAN = "REJECTED_EXIT_PLAN"
    REJECTED_RISK = "REJECTED_RISK"
    HOLD = "HOLD"
    REJECTED_COMPLIANCE = "REJECTED_COMPLIANCE"
    REJECTED_EXECUTION = "REJECTED_EXECUTION"
    EXECUTE = "EXECUTE"
