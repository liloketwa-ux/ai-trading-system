"""Provider interfaces and tri-state capability detection (AD-3).

CCXT's ``exchange.has`` is not a boolean map. It returns ``True``, ``False``,
``None`` (key absent), or the string ``'emulated'`` — meaning the library
synthesizes the result from other endpoints rather than the venue reporting it
natively. Verified: ``bybit.has['fetchFundingRate'] == 'emulated'`` and
``kraken.has['fetchOpenInterest'] is None``.

A truthiness check gets both wrong. ``'emulated'`` is truthy and would be taken
for native support, silently mixing derived and native data in one dataset;
``None`` is falsy but produces a venue-specific exception deep in a research run
rather than a clear refusal at the boundary.

So capability is an explicit enum, checked before every call, and unsupported
calls raise :class:`CapabilityError` rather than reaching the venue.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum

from .types import OHLCV, FundingRate, OpenInterest, OrderBook, Ticker, Trade

__all__ = [
    "Capability",
    "CapabilityError",
    "MarketDataError",
    "StaleDataError",
    "MarketDataProvider",
    "ExecutionProvider",
]


class MarketDataError(RuntimeError):
    """Base for market-data failures."""


class CapabilityError(MarketDataError):
    """A provider was asked for something the venue does not support."""


class StaleDataError(MarketDataError):
    """Returned data is older than the caller's freshness requirement."""


class Capability(str, Enum):
    """Support level for one provider method."""

    SUPPORTED = "supported"
    EMULATED = "emulated"      # derived by the library, not natively reported
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"        # venue metadata does not mention it

    @classmethod
    def from_ccxt(cls, value: object) -> "Capability":
        """Map a raw ``exchange.has`` entry onto the enum.

        Order matters: the ``'emulated'`` string must be tested before general
        truthiness, or it collapses into SUPPORTED.
        """
        if isinstance(value, str) and value.lower() == "emulated":
            return cls.EMULATED
        if value is True:
            return cls.SUPPORTED
        if value is False:
            return cls.UNSUPPORTED
        return cls.UNKNOWN

    @property
    def usable(self) -> bool:
        """Whether a call may be attempted at all."""
        return self in (Capability.SUPPORTED, Capability.EMULATED)

    @property
    def is_native(self) -> bool:
        """Whether results come from the venue rather than being derived."""
        return self is Capability.SUPPORTED


class MarketDataProvider(ABC):
    """Read-only market data, normalized and provenance-carrying.

    Mirrors the ``MarketDataProvider`` TypeScript interface in the brief; it is
    a Python ABC per AD-1. Implementations must declare capabilities honestly —
    :meth:`capability` is what callers branch on, and a provider that overstates
    support turns a clean refusal into a runtime failure mid-research.
    """

    name: str = "abstract"

    @abstractmethod
    def capability(self, method: str) -> Capability:
        """Support level for a method name such as ``"fetchOHLCV"``."""

    def require(self, method: str) -> Capability:
        """Assert a method is usable, or raise :class:`CapabilityError`."""
        capability = self.capability(method)
        if not capability.usable:
            raise CapabilityError(
                f"{self.name} does not support {method} (capability={capability.value})"
            )
        return capability

    def supports(self, method: str) -> bool:
        return self.capability(method).usable

    @abstractmethod
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[OHLCV]:
        """Completed candles, oldest first."""

    @abstractmethod
    def fetch_ticker(self, symbol: str) -> Ticker: ...

    @abstractmethod
    def fetch_order_book(self, symbol: str, limit: int | None = None) -> OrderBook: ...

    @abstractmethod
    def fetch_trades(
        self, symbol: str, since: datetime | None = None, limit: int | None = None
    ) -> list[Trade]: ...

    @abstractmethod
    def fetch_funding_rate(self, symbol: str) -> FundingRate: ...

    @abstractmethod
    def fetch_open_interest(self, symbol: str) -> OpenInterest: ...

    def capability_report(self) -> dict[str, Capability]:
        """Support level for every method, for logging and observability."""
        return {
            method: self.capability(method)
            for method in (
                "fetchOHLCV",
                "fetchTicker",
                "fetchOrderBook",
                "fetchTrades",
                "fetchFundingRate",
                "fetchOpenInterest",
            )
        }


class ExecutionProvider(ABC):
    """Order placement and account state.

    Deliberately separate from :class:`MarketDataProvider`: a paper broker
    implements this contract while reading market data from elsewhere, and no
    live implementation ships (AD-9).
    """

    name: str = "abstract"

    @abstractmethod
    def capability(self, method: str) -> Capability: ...

    @abstractmethod
    def create_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        order_type: str = "market",
        price: float | None = None,
        *,
        client_order_id: str | None = None,
    ) -> object: ...

    @abstractmethod
    def cancel_order(self, order_id: str, symbol: str | None = None) -> object: ...

    @abstractmethod
    def fetch_order(self, order_id: str, symbol: str | None = None) -> object: ...

    @abstractmethod
    def fetch_positions(self, symbols: list[str] | None = None) -> list[object]: ...
