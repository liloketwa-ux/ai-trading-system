"""Normalized market-data types with mandatory provenance (AD-2).

Every datum records where it came from, when the underlying event happened,
when we fetched it, and -- the load-bearing one -- when a decision could first
legitimately have used it.

``available_at`` is not decoration. Look-ahead is not only a bar-indexing
problem: a liquidity figure fetched today describing a token as it was last
week is future information if a backtest reads it at last week's timestamp.
For an OHLCV bar, ``available_at`` is the bar's **close**, never its open --
the open is knowable only after the bar completes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd

__all__ = [
    "Provenance",
    "OHLCV",
    "Ticker",
    "OrderBook",
    "Trade",
    "FundingRate",
    "OpenInterest",
    "bars_to_frame",
]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class Provenance:
    """Where a datum came from and when it became usable."""

    source: str            # "ccxt:binanceusdm", "pumpi:pumpfun", "dexscreener"
    event_time: datetime   # when the underlying event happened
    retrieved_at: datetime # when we fetched it
    available_at: datetime # earliest time a decision could have used it
    parser_version: str = "1"
    emulated: bool = False  # derived by the venue/library, not natively reported

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("source must not be empty")
        object.__setattr__(self, "event_time", _utc(self.event_time))
        object.__setattr__(self, "retrieved_at", _utc(self.retrieved_at))
        object.__setattr__(self, "available_at", _utc(self.available_at))
        if self.available_at < self.event_time:
            raise ValueError(
                f"available_at ({self.available_at}) precedes event_time "
                f"({self.event_time}) -- a datum cannot be usable before it exists"
            )

    @property
    def latency(self) -> timedelta:
        """Delay between the event and our retrieval of it."""
        return self.retrieved_at - self.event_time


@dataclass(frozen=True)
class OHLCV:
    """One completed candle. ``timestamp`` is the bar OPEN."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    provenance: Provenance

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _utc(self.timestamp))
        if self.high < self.low:
            raise ValueError(f"high {self.high} below low {self.low}")
        if not (self.low <= self.open <= self.high):
            raise ValueError(f"open {self.open} outside [{self.low}, {self.high}]")
        if not (self.low <= self.close <= self.high):
            raise ValueError(f"close {self.close} outside [{self.low}, {self.high}]")
        if self.volume < 0:
            raise ValueError(f"volume must be >= 0, got {self.volume}")


@dataclass(frozen=True)
class Ticker:
    symbol: str
    last: float
    bid: float | None
    ask: float | None
    provenance: Provenance

    @property
    def spread(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid

    @property
    def spread_bps(self) -> float | None:
        spread = self.spread
        if spread is None or self.last <= 0:
            return None
        return spread / self.last * 10_000.0


@dataclass(frozen=True)
class OrderBook:
    symbol: str
    bids: list[tuple[float, float]]  # (price, size), best first
    asks: list[tuple[float, float]]
    provenance: Provenance

    @property
    def best_bid(self) -> float | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0][0] if self.asks else None

    @property
    def mid(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2.0

    def depth(self, side: str) -> float:
        levels = self.bids if side == "bid" else self.asks
        return sum(size for _, size in levels)


@dataclass(frozen=True)
class Trade:
    symbol: str
    timestamp: datetime
    price: float
    amount: float
    side: str  # "buy" | "sell"
    provenance: Provenance
    trade_id: str | None = None


@dataclass(frozen=True)
class FundingRate:
    symbol: str
    rate: float
    funding_time: datetime
    provenance: Provenance


@dataclass(frozen=True)
class OpenInterest:
    symbol: str
    open_interest: float
    provenance: Provenance
    open_interest_value: float | None = None


def bars_to_frame(bars: list[OHLCV]) -> pd.DataFrame:
    """Convert bars to the OHLCV frame the backtester consumes.

    Indexed by bar open and sorted ascending. Carries an ``available_at``
    column so downstream code can assert causality rather than assume it.
    """
    if not bars:
        return pd.DataFrame(
            columns=["open", "high", "low", "close", "volume", "available_at"]
        ).rename_axis("timestamp")

    frame = pd.DataFrame(
        {
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
            "available_at": [b.provenance.available_at for b in bars],
        },
        index=pd.DatetimeIndex([b.timestamp for b in bars], name="timestamp"),
    )
    return frame.sort_index()
