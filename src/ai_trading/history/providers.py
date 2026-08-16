"""The historical data acquisition contract.

Distinct from :mod:`ai_trading.marketdata.provider`, which models a *live*
venue connection. A historical provider answers a different question -- "what
was true, and when did we know it" -- and the extra field in that sentence is
the whole reason this module exists separately.

Every record a provider emits carries five things, and a provider that cannot
supply them does not get to pretend:

``source``           which feed produced it, verbatim
``event_time``       when the underlying thing happened
``available_at``     when a decision could first have used it
``retrieved_at``     when this code pulled it
``schema_version``   which layout the row was written in

``available_at`` is the one that gets fudged, so it is never derived silently:
it comes from the provider's :class:`~ai_trading.history.availability.AvailabilityPolicy`,
which forces the assumption into the open and labels its quality.

Capabilities are declared, not discovered by exception. A provider that has no
order-book history says so, and the caller finds out before running a study
that depends on one rather than after.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Sequence

from .availability import AvailabilityPolicy, AvailabilityQuality, utc

__all__ = [
    "DataKind", "HistoricalRecord", "Bar", "CoverageWindow",
    "HistoricalDataProvider", "ProviderCapabilityError", "ProviderDescriptor",
    "SCHEMA_VERSION",
]

#: Bumped whenever a record layout changes. Stored on every row so a dataset
#: written under an old layout is identifiable rather than silently reinterpreted.
SCHEMA_VERSION = "1.0.0"


class ProviderCapabilityError(RuntimeError):
    """A provider was asked for a data kind it does not serve."""


class DataKind(str, Enum):
    """What a provider can supply."""

    BARS = "bars"
    TRADES = "trades"
    ORDER_BOOKS = "order_books"
    FUNDING = "funding"
    OPEN_INTEREST = "open_interest"
    NEWS = "news"
    MACRO_EVENTS = "macro_events"

    @property
    def is_market_microstructure(self) -> bool:
        """Kinds whose research value collapses without observed availability.

        Order books and trades are consumed by strategies that react in
        milliseconds. Running those on assumed availability measures the
        assumption, not the market.
        """
        return self in (DataKind.TRADES, DataKind.ORDER_BOOKS)


@dataclass(frozen=True)
class CoverageWindow:
    """What a provider actually holds for one kind and instrument.

    Reported rather than inferred from whatever happened to be returned. A
    query that comes back with three months of a five-year request has either
    hit a coverage limit or lost data, and only the provider knows which.
    """

    kind: DataKind
    instrument: str
    start: date | None
    end: date | None
    timeframes: tuple[str, ...] = ()
    note: str = ""

    @property
    def is_empty(self) -> bool:
        return self.start is None or self.end is None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "instrument": self.instrument,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "timeframes": list(self.timeframes),
            "note": self.note,
        }


@dataclass(frozen=True)
class HistoricalRecord:
    """Base for every historical datum. Provenance is not optional."""

    source: str
    event_time: datetime
    available_at: datetime
    retrieved_at: datetime
    schema_version: str = SCHEMA_VERSION
    availability_quality: AvailabilityQuality = AvailabilityQuality.UNVERIFIED

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("a historical record must name its source")
        object.__setattr__(self, "event_time", utc(self.event_time))
        object.__setattr__(self, "available_at", utc(self.available_at))
        object.__setattr__(self, "retrieved_at", utc(self.retrieved_at))
        if self.available_at < self.event_time:
            raise ValueError(
                f"available_at {self.available_at.isoformat()} precedes event_time "
                f"{self.event_time.isoformat()} -- a datum cannot be usable before the "
                "thing it describes has happened"
            )

    def is_available_at(self, decision_time: datetime) -> bool:
        return self.available_at <= utc(decision_time)

    def provenance(self) -> dict:
        return {
            "source": self.source,
            "event_time": self.event_time.isoformat(),
            "available_at": self.available_at.isoformat(),
            "retrieved_at": self.retrieved_at.isoformat(),
            "schema_version": self.schema_version,
            "availability_quality": self.availability_quality.value,
        }


@dataclass(frozen=True)
class Bar(HistoricalRecord):
    """One OHLCV bar for one contract.

    ``instrument`` is the product (``NQ``); ``contract`` is the specific
    deliverable (``NQZ25``). They are separate fields because conflating them
    is how a continuous series gets built by accident.
    """

    instrument: str = ""
    contract: str = ""
    timeframe: str = ""
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.instrument or not self.contract or not self.timeframe:
            raise ValueError(
                "a bar needs instrument, contract and timeframe -- a bar without its "
                "contract cannot be kept out of a continuous series"
            )

    @property
    def has_impossible_ohlc(self) -> bool:
        """Whether the four prices contradict each other."""
        if self.high < self.low:
            return True
        if not (self.low <= self.open <= self.high):
            return True
        return not (self.low <= self.close <= self.high)

    def to_dict(self) -> dict:
        payload = self.provenance()
        payload.update({
            "instrument": self.instrument, "contract": self.contract,
            "timeframe": self.timeframe, "open": self.open, "high": self.high,
            "low": self.low, "close": self.close, "volume": self.volume,
        })
        return payload


@dataclass(frozen=True)
class ProviderDescriptor:
    """Everything a data-quality report needs to name its source."""

    name: str
    kinds: frozenset[DataKind]
    availability_policy: AvailabilityPolicy
    timezone: str = "UTC"
    known_limitations: tuple[str, ...] = ()
    documentation_url: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kinds": sorted(k.value for k in self.kinds),
            "availability_policy": self.availability_policy.to_dict(),
            "timezone": self.timezone,
            "known_limitations": list(self.known_limitations),
            "documentation_url": self.documentation_url,
        }


class HistoricalDataProvider(ABC):
    """A source of historical market data.

    Subclasses declare what they serve and implement only those kinds. The
    default implementations raise :class:`ProviderCapabilityError` rather than
    returning empty lists, because an empty list is indistinguishable from "no
    data in that window" and quietly turns a missing capability into a research
    result of zero.
    """

    @property
    @abstractmethod
    def descriptor(self) -> ProviderDescriptor: ...

    @property
    def name(self) -> str:
        return self.descriptor.name

    @property
    def availability_policy(self) -> AvailabilityPolicy:
        return self.descriptor.availability_policy

    def supports(self, kind: DataKind) -> bool:
        return kind in self.descriptor.kinds

    def require(self, kind: DataKind) -> None:
        if not self.supports(kind):
            raise ProviderCapabilityError(
                f"{self.name} does not serve {kind.value}. Declared kinds: "
                f"{', '.join(sorted(k.value for k in self.descriptor.kinds)) or 'none'}"
            )

    @abstractmethod
    def coverage(self, kind: DataKind, instrument: str) -> CoverageWindow:
        """What this provider actually holds. Never inferred from a query."""

    def fetch_bars(self, instrument: str, contract: str, timeframe: str,
                   start: datetime, end: datetime) -> Sequence[Bar]:
        self.require(DataKind.BARS)
        raise NotImplementedError

    def fetch_trades(self, instrument: str, contract: str,
                     start: datetime, end: datetime) -> Sequence[HistoricalRecord]:
        self.require(DataKind.TRADES)
        raise NotImplementedError

    def fetch_order_books(self, instrument: str, contract: str,
                          start: datetime, end: datetime) -> Sequence[HistoricalRecord]:
        self.require(DataKind.ORDER_BOOKS)
        raise NotImplementedError

    def fetch_funding(self, instrument: str,
                      start: datetime, end: datetime) -> Sequence[HistoricalRecord]:
        self.require(DataKind.FUNDING)
        raise NotImplementedError

    def fetch_open_interest(self, instrument: str,
                            start: datetime, end: datetime) -> Sequence[HistoricalRecord]:
        self.require(DataKind.OPEN_INTEREST)
        raise NotImplementedError

    def fetch_news(self, start: datetime, end: datetime,
                   instrument: str | None = None) -> Sequence[HistoricalRecord]:
        self.require(DataKind.NEWS)
        raise NotImplementedError

    def fetch_macro_events(self, start: datetime,
                           end: datetime) -> Sequence[HistoricalRecord]:
        self.require(DataKind.MACRO_EVENTS)
        raise NotImplementedError

    def capability_report(self) -> dict[str, bool]:
        return {kind.value: self.supports(kind) for kind in DataKind}
