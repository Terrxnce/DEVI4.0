"""USD correlation utilities for risk management.

USD appears on one or both sides of most traded FX pairs and many commodity/index
contracts (e.g. XAUUSD, US30). Opening multiple USD-correlated positions in the
same direction amplifies exposure to USD-driven news events beyond what the
standard per-symbol position cap alone captures.

``count_usd_positions`` takes a list of open symbol names and returns how many
involve USD, regardless of direction. The caller supplies the direction-filtered
or unfiltered list depending on which limit it wants to enforce.
"""
from __future__ import annotations

# Symbols that are USD-denominated (commodity/index futures quoted in USD) but
# do NOT represent a direct USD currency position.  We exclude them from the
# correlation count because their USD relationship is structural (quote currency)
# rather than a directional FX bet on the US dollar.
_USD_QUOTE_ONLY_EXCLUSIONS: frozenset[str] = frozenset({
    "XAUUSD", "XAGUSD",         # metals — USD quote, not a USD FX bet
    "US30", "NAS100", "US500",  # indices — USD denominated
    "US30.cash", "US100.cash", "US500.cash",
    "WTI", "USOIL",
})


def is_usd_pair(symbol: str) -> bool:
    """Return True if the symbol represents a direct USD currency position.

    Matches symbols where USD is the base or quote currency (EURUSD, USDJPY,
    USDCAD, USDCHF, AUDUSD, NZDUSD, GBPUSD) while excluding commodity and
    index contracts that happen to be quoted in USD.

    Case-insensitive. Broker suffix suffixes (e.g. "EURUSDm", "EURUSD.pro")
    are handled by stripping non-alpha characters before the first 6 chars.
    """
    clean = symbol.upper().replace(".", "").replace("_", "").replace("-", "")
    if clean in _USD_QUOTE_ONLY_EXCLUSIONS:
        return False
    # Strip trailing broker suffix: take first 6 alphanum chars
    core = "".join(c for c in clean if c.isalpha())[:6]
    return "USD" in core


def count_usd_positions(open_symbols: list[str]) -> int:
    """Count how many symbols in *open_symbols* are USD currency pairs."""
    return sum(1 for s in open_symbols if is_usd_pair(s))


def is_jpy_pair(symbol: str) -> bool:
    """Return True if the symbol involves JPY as base or quote currency.

    Matches any pair where JPY appears in the first 6 alpha characters
    (e.g. USDJPY, EURJPY, GBPJPY, CADJPY, AUDJPY, NZDJPY, CHFJPY).
    Case-insensitive. Broker suffixes are stripped the same way as is_usd_pair.
    """
    clean = symbol.upper().replace(".", "").replace("_", "").replace("-", "")
    core = "".join(c for c in clean if c.isalpha())[:6]
    return "JPY" in core


def count_jpy_positions(open_symbols: list[str]) -> int:
    """Count how many symbols in *open_symbols* are JPY pairs."""
    return sum(1 for s in open_symbols if is_jpy_pair(s))
