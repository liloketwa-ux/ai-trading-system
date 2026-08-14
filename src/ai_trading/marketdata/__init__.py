"""Normalized market-data layer (Phase 2).

Provider-agnostic types carrying mandatory provenance (AD-2), a tri-state
capability model (AD-3), a CCXT-backed implementation, and a quality gate that
fails loudly rather than let research run on corrupt data.
"""

from .ccxt_adapter import CCXTMarketData, parse_timeframe
from .provider import (
    Capability,
    CapabilityError,
    ExecutionProvider,
    MarketDataError,
    MarketDataProvider,
    StaleDataError,
)
from .quality import (
    QualityGateError,
    QualityIssue,
    QualityReport,
    Severity,
    check_quality,
)
from .types import (
    OHLCV,
    FundingRate,
    OpenInterest,
    OrderBook,
    Provenance,
    Ticker,
    Trade,
    bars_to_frame,
)

__all__ = [
    "OHLCV",
    "CCXTMarketData",
    "Capability",
    "CapabilityError",
    "ExecutionProvider",
    "FundingRate",
    "MarketDataError",
    "MarketDataProvider",
    "OpenInterest",
    "OrderBook",
    "Provenance",
    "QualityGateError",
    "QualityIssue",
    "QualityReport",
    "Severity",
    "StaleDataError",
    "Ticker",
    "Trade",
    "bars_to_frame",
    "check_quality",
    "parse_timeframe",
]
