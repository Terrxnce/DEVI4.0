"""Tests for src/risk/usd_correlation.py

Coverage:
- is_usd_pair: major USD FX pairs → True
- is_usd_pair: non-USD cross pairs → False
- is_usd_pair: commodity/index symbols excluded from USD count → False
- is_usd_pair: broker suffix variants (EURUSDm, EURUSD.pro) handled correctly
- count_usd_positions: empty list → 0
- count_usd_positions: mixed list counts only USD pairs
- count_usd_positions: all non-USD list → 0
- count_usd_positions: all USD list → len(list)
"""
from __future__ import annotations

import pytest

from src.risk.usd_correlation import count_jpy_positions, count_usd_positions, is_jpy_pair, is_usd_pair


# ---------------------------------------------------------------------------
# is_usd_pair — USD FX pairs (should return True)
# ---------------------------------------------------------------------------

class TestIsUsdPairPositive:
    @pytest.mark.parametrize("symbol", [
        "EURUSD",
        "GBPUSD",
        "AUDUSD",
        "NZDUSD",
        "USDJPY",
        "USDCAD",
        "USDCHF",
        "USDMXN",
        "USDSGD",
        "USDHKD",
    ])
    def test_usd_fx_pairs_return_true(self, symbol: str) -> None:
        assert is_usd_pair(symbol) is True, f"Expected {symbol} to be a USD pair"

    @pytest.mark.parametrize("symbol", [
        "eurusd",
        "Eurusd",
        "EURUSD",
        "gbpusd",
    ])
    def test_case_insensitive(self, symbol: str) -> None:
        assert is_usd_pair(symbol) is True


# ---------------------------------------------------------------------------
# is_usd_pair — non-USD crosses (should return False)
# ---------------------------------------------------------------------------

class TestIsUsdPairNegative:
    @pytest.mark.parametrize("symbol", [
        "EURGBP",
        "EURJPY",
        "GBPJPY",
        "AUDCAD",
        "AUDNZD",
        "NZDCAD",
        "EURCHF",
        "GBPCHF",
        "CADJPY",
        "CHFJPY",
        "AUDCHF",
        "NZDCHF",
        "EURAUD",
        "EURNZD",
        "GBPAUD",
        "GBPNZD",
        "GBPCAD",
        "EURCAD",
    ])
    def test_non_usd_crosses_return_false(self, symbol: str) -> None:
        assert is_usd_pair(symbol) is False, f"Expected {symbol} to NOT be a USD pair"


# ---------------------------------------------------------------------------
# is_usd_pair — excluded commodity/index symbols (should return False)
# ---------------------------------------------------------------------------

class TestIsUsdPairExclusions:
    @pytest.mark.parametrize("symbol", [
        "XAUUSD",
        "XAGUSD",
        "US30",
        "NAS100",
        "US500",
        "US30.cash",
        "US100.cash",
        "US500.cash",
        "WTI",
        "USOIL",
    ])
    def test_commodity_and_index_excluded(self, symbol: str) -> None:
        assert is_usd_pair(symbol) is False, f"Expected {symbol} to be excluded from USD FX count"


# ---------------------------------------------------------------------------
# is_usd_pair — broker suffix handling
# ---------------------------------------------------------------------------

class TestIsUsdPairBrokerSuffixes:
    @pytest.mark.parametrize("symbol", [
        "EURUSDm",
        "EURUSD.pro",
        "GBPUSD_raw",
        "AUDUSDm",
    ])
    def test_broker_suffix_usd_pairs_detected(self, symbol: str) -> None:
        assert is_usd_pair(symbol) is True, f"Expected {symbol} (broker suffix) to be a USD pair"

    @pytest.mark.parametrize("symbol", [
        "EURGBPm",
        "EURJPY.pro",
    ])
    def test_broker_suffix_non_usd_not_detected(self, symbol: str) -> None:
        assert is_usd_pair(symbol) is False, f"Expected {symbol} (broker suffix) to NOT be a USD pair"


# ---------------------------------------------------------------------------
# count_usd_positions
# ---------------------------------------------------------------------------

class TestCountUsdPositions:
    def test_empty_list_returns_zero(self) -> None:
        assert count_usd_positions([]) == 0

    def test_all_usd_pairs(self) -> None:
        symbols = ["EURUSD", "GBPUSD", "AUDUSD", "USDJPY"]
        assert count_usd_positions(symbols) == 4

    def test_no_usd_pairs(self) -> None:
        symbols = ["EURGBP", "EURJPY", "AUDCAD"]
        assert count_usd_positions(symbols) == 0

    def test_mixed_list(self) -> None:
        symbols = ["EURUSD", "EURGBP", "GBPUSD", "AUDCAD", "NZDUSD"]
        # EURUSD, GBPUSD, NZDUSD are USD pairs; EURGBP, AUDCAD are not
        assert count_usd_positions(symbols) == 3

    def test_excludes_gold_and_indices(self) -> None:
        symbols = ["XAUUSD", "US30", "NAS100", "EURUSD"]
        # Only EURUSD counts; XAUUSD, US30, NAS100 are excluded
        assert count_usd_positions(symbols) == 1

    def test_single_usd_pair(self) -> None:
        assert count_usd_positions(["USDJPY"]) == 1

    def test_single_non_usd_pair(self) -> None:
        assert count_usd_positions(["EURGBP"]) == 0

    def test_duplicates_counted_individually(self) -> None:
        """Same symbol twice (unlikely in practice) counts twice."""
        assert count_usd_positions(["EURUSD", "EURUSD"]) == 2

    def test_broker_suffixes_counted(self) -> None:
        symbols = ["EURUSDm", "GBPUSDm", "EURGBPm"]
        # EURUSDm and GBPUSDm are USD; EURGBPm is not
        assert count_usd_positions(symbols) == 2


# ---------------------------------------------------------------------------
# is_jpy_pair
# ---------------------------------------------------------------------------

class TestIsJpyPair:
    @pytest.mark.parametrize("symbol", [
        "USDJPY",
        "EURJPY",
        "GBPJPY",
        "CADJPY",
        "AUDJPY",
        "NZDJPY",
        "CHFJPY",
    ])
    def test_jpy_pairs_return_true(self, symbol: str) -> None:
        assert is_jpy_pair(symbol) is True, f"Expected {symbol} to be a JPY pair"

    @pytest.mark.parametrize("symbol", [
        "usdjpy",
        "Eurjpy",
        "GBPJPY",
    ])
    def test_case_insensitive(self, symbol: str) -> None:
        assert is_jpy_pair(symbol) is True

    @pytest.mark.parametrize("symbol", [
        "EURUSD",
        "GBPUSD",
        "AUDUSD",
        "EURGBP",
        "AUDCAD",
        "XAUUSD",
        "US30",
    ])
    def test_non_jpy_pairs_return_false(self, symbol: str) -> None:
        assert is_jpy_pair(symbol) is False, f"Expected {symbol} to NOT be a JPY pair"

    @pytest.mark.parametrize("symbol", [
        "USDJPYm",
        "EURJPY.pro",
        "GBPJPY_raw",
    ])
    def test_broker_suffix_jpy_pairs_detected(self, symbol: str) -> None:
        assert is_jpy_pair(symbol) is True, f"Expected {symbol} (broker suffix) to be a JPY pair"


# ---------------------------------------------------------------------------
# count_jpy_positions
# ---------------------------------------------------------------------------

class TestCountJpyPositions:
    def test_empty_list_returns_zero(self) -> None:
        assert count_jpy_positions([]) == 0

    def test_all_jpy_pairs(self) -> None:
        symbols = ["USDJPY", "EURJPY", "GBPJPY", "CADJPY"]
        assert count_jpy_positions(symbols) == 4

    def test_no_jpy_pairs(self) -> None:
        symbols = ["EURUSD", "GBPUSD", "EURGBP", "AUDCAD"]
        assert count_jpy_positions(symbols) == 0

    def test_mixed_list(self) -> None:
        # CADJPY and USDJPY are JPY; EURUSD, GBPUSD, XAUUSD are not
        symbols = ["CADJPY", "EURUSD", "USDJPY", "GBPUSD", "XAUUSD"]
        assert count_jpy_positions(symbols) == 2

    def test_real_world_asia_session(self) -> None:
        # The exact scenario that triggered this fix: CADJPY + USDJPY both short
        symbols = ["CADJPY", "USDJPY"]
        assert count_jpy_positions(symbols) == 2

    def test_single_jpy_pair(self) -> None:
        assert count_jpy_positions(["USDJPY"]) == 1

    def test_single_non_jpy_pair(self) -> None:
        assert count_jpy_positions(["EURUSD"]) == 0
