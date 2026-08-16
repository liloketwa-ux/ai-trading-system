"""The contract a real futures data provider must satisfy.

Written before any provider exists, deliberately. Writing the interface against
the first vendor's API shape is how a vendor's quirks become the system's
assumptions -- and the quirk that matters most here is that most vendors will
happily serve a continuous front-month series and call it ``NQ``.

So the contract is stated in terms of what research needs:

* bars, per **contract**, never per product
* trades where the provider has them
* contract metadata, including expiry -- without it no roll can be justified
* session metadata, so gaps can be judged instead of counted
* instrument metadata: tick size, multiplier, currency

and the provenance every response must carry: provider, dataset, contract,
timestamp, timezone, schema version, coverage.

:meth:`FuturesDataProvider.fetch_bars` takes a ``contract``, not a symbol, and
there is no parameter for "front month". A provider adapter that can only serve
a stitched series cannot implement this interface without saying so through
:attr:`ProviderManifest.serves_continuous_only`, which the ingestion gate then
refuses.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from typing import Sequence

from .availability import AvailabilityPolicy
from .providers import Bar, CoverageWindow, DataKind, HistoricalRecord

__all__ = [
    "FuturesDataProvider", "ProviderManifest", "InstrumentMetadata",
    "SessionMetadata", "ContractRecord", "ResponseProvenance",
    "ContinuousOnlyProviderError", "ProviderCredentialError",
]


class ContinuousOnlyProviderError(RuntimeError):
    """A provider that can only serve stitched series was used for ingestion."""


class ProviderCredentialError(RuntimeError):
    """Credentials were needed and not supplied through the environment."""


@dataclass(frozen=True)
class InstrumentMetadata:
    """What a contract is worth, in the units the simulator needs.

    Without tick value and multiplier a price series cannot be turned into
    money, and a backtest that guesses them produces P&L in invented units.
    """

    instrument: str
    exchange: str
    currency: str
    tick_size: float
    tick_value: float
    multiplier: float
    description: str = ""

    def __post_init__(self) -> None:
        for name in ("tick_size", "tick_value", "multiplier"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")

    def points_to_currency(self, points: float) -> float:
        return points * self.multiplier

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument, "exchange": self.exchange,
            "currency": self.currency, "tick_size": self.tick_size,
            "tick_value": self.tick_value, "multiplier": self.multiplier,
            "description": self.description,
        }


@dataclass(frozen=True)
class SessionMetadata:
    """When the instrument trades, as the provider states it.

    Taken from the provider rather than assumed, because the missing-bar count
    is computed against it: an assumed session turns a normal close into
    reported data loss, or hides real loss inside an assumed break.
    """

    instrument: str
    timezone_name: str
    #: ``date.weekday()`` values on which a session occurs.
    trading_weekdays: frozenset[int]
    session_open_utc_minute: int
    session_close_utc_minute: int
    daily_break_utc: tuple[int, int] | None = None
    holidays: frozenset[date] = frozenset()
    source_note: str = ""

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument, "timezone": self.timezone_name,
            "trading_weekdays": sorted(self.trading_weekdays),
            "session_open_utc_minute": self.session_open_utc_minute,
            "session_close_utc_minute": self.session_close_utc_minute,
            "daily_break_utc": (list(self.daily_break_utc)
                                if self.daily_break_utc else None),
            "holiday_count": len(self.holidays),
            "source_note": self.source_note,
        }


@dataclass(frozen=True)
class ContractRecord:
    """One deliverable contract as the provider describes it."""

    instrument: str
    contract: str
    expiry: date
    first_trade_date: date | None = None
    last_trade_date: date | None = None
    is_active: bool = False
    provider_symbol: str = ""

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument, "contract": self.contract,
            "expiry": self.expiry.isoformat(),
            "first_trade_date": (self.first_trade_date.isoformat()
                                 if self.first_trade_date else None),
            "last_trade_date": (self.last_trade_date.isoformat()
                                if self.last_trade_date else None),
            "is_active": self.is_active,
            "provider_symbol": self.provider_symbol,
        }


@dataclass(frozen=True)
class ResponseProvenance:
    """Attached to every response. Seven fields, none optional."""

    provider: str
    dataset: str
    contract: str
    timestamp: datetime
    timezone_name: str
    schema_version: str
    coverage: CoverageWindow

    def __post_init__(self) -> None:
        for name in ("provider", "dataset", "contract", "timezone_name",
                     "schema_version"):
            if not getattr(self, name):
                raise ValueError(
                    f"response provenance requires {name}; a response that cannot "
                    "say where it came from cannot be audited later"
                )

    def to_dict(self) -> dict:
        return {
            "provider": self.provider, "dataset": self.dataset,
            "contract": self.contract, "timestamp": self.timestamp.isoformat(),
            "timezone": self.timezone_name,
            "schema_version": self.schema_version,
            "coverage": self.coverage.to_dict(),
        }


@dataclass(frozen=True)
class ProviderManifest:
    """What a provider is and what it can honestly do."""

    provider: str
    dataset: str
    kinds: frozenset[DataKind]
    availability_policy: AvailabilityPolicy
    timezone_name: str = "UTC"
    #: True when the provider can only supply a stitched front-month series.
    #: Such a provider is usable for reference and refused for ingestion.
    serves_continuous_only: bool = False
    #: Names of environment variables holding credentials. Values are never
    #: stored here, never logged, and never committed.
    credential_env_vars: tuple[str, ...] = ()
    documentation_url: str = ""
    known_limitations: tuple[str, ...] = ()

    @property
    def requires_credentials(self) -> bool:
        return bool(self.credential_env_vars)

    def check_credentials(self, environ: dict[str, str]) -> None:
        """Verify credentials are present, without reading their values.

        Only presence is checked and only names are reported. A missing-key
        error that echoes the key's value is a credential leak in a log file.
        """
        missing = [name for name in self.credential_env_vars
                   if not environ.get(name)]
        if missing:
            raise ProviderCredentialError(
                f"{self.provider} needs {', '.join(missing)} in the environment. "
                "Set them in the environment's secret configuration -- never in "
                "source, never in a commit, never in a chat message."
            )

    def require_contract_level(self) -> None:
        """Refuse a continuous-only provider for canonical ingestion."""
        if self.serves_continuous_only:
            raise ContinuousOnlyProviderError(
                f"{self.provider}/{self.dataset} serves only a stitched "
                "front-month series. The canonical research dataset holds "
                "individual contracts; a continuous series may be derived later "
                "from raw contracts plus an explicit roll and adjustment policy, "
                "and cannot be ingested as a substitute for them."
            )

    def to_dict(self) -> dict:
        return {
            "provider": self.provider, "dataset": self.dataset,
            "kinds": sorted(k.value for k in self.kinds),
            "availability_policy": self.availability_policy.to_dict(),
            "timezone": self.timezone_name,
            "serves_continuous_only": self.serves_continuous_only,
            "requires_credentials": self.requires_credentials,
            "credential_env_vars": list(self.credential_env_vars),
            "documentation_url": self.documentation_url,
            "known_limitations": list(self.known_limitations),
        }


class FuturesDataProvider(ABC):
    """The final contract for a real futures provider.

    No implementation ships. Writing one requires a reachable provider and
    credentials supplied through the environment, neither of which exists yet.
    """

    @property
    @abstractmethod
    def manifest(self) -> ProviderManifest: ...

    @abstractmethod
    def instrument_metadata(self, instrument: str) -> InstrumentMetadata:
        """Tick size, tick value, multiplier, currency, exchange."""

    @abstractmethod
    def session_metadata(self, instrument: str) -> SessionMetadata:
        """The provider's own statement of when the instrument trades."""

    @abstractmethod
    def list_contracts(self, instrument: str, *, start: date,
                       end: date) -> Sequence[ContractRecord]:
        """Deliverable contracts overlapping a window, with expiries."""

    @abstractmethod
    def coverage(self, kind: DataKind, instrument: str,
                 contract: str) -> CoverageWindow:
        """What the provider holds, per contract. Declared, not inferred."""

    @abstractmethod
    def fetch_bars(self, *, instrument: str, contract: str, timeframe: str,
                   start: datetime,
                   end: datetime) -> tuple[Sequence[Bar], ResponseProvenance]:
        """Bars for **one contract**. There is no front-month parameter."""

    def fetch_trades(self, *, instrument: str, contract: str, start: datetime,
                     end: datetime
                     ) -> tuple[Sequence[HistoricalRecord], ResponseProvenance]:
        """Tick data, where the provider has it."""
        if DataKind.TRADES not in self.manifest.kinds:
            raise NotImplementedError(
                f"{self.manifest.provider} does not serve trades"
            )
        raise NotImplementedError

    def preflight(self, environ: dict[str, str]) -> None:
        """Everything that must hold before ingestion is attempted."""
        self.manifest.check_credentials(environ)
        self.manifest.require_contract_level()
