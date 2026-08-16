"""Contract-aware ingestion: individual futures contracts, never stitched.

A futures product is not a price series. ``NQ`` is a family of separately
traded, separately expiring instruments, and the file that claims to be "NQ
1-minute since 2021" is a construction -- somebody chose a roll date and an
adjustment method, and the choice is baked into every bar. Research run on that
file inherits assumptions it cannot see and cannot vary.

So ingestion keeps contracts apart. :class:`ContractBook` stores bars under
``(instrument, contract, timeframe)`` and has no method that returns a joined
series. Building one requires the Phase 7 :class:`RollPolicy`, which fails
closed until both a roll method and an adjustment method are declared -- that
rule is unchanged here and is enforced by delegation rather than reimplemented.

The metadata each contract carries -- ``expiry``, ``first_seen``, ``last_seen``,
``roll_indicator`` -- exists so that a roll can later be justified from observed
data rather than asserted. ``roll_indicator`` is deliberately an observation
("volume in the next contract overtook this one on this date"), not a decision.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Iterable, Sequence

from ..validation.rolls import ContinuityError, RollPolicy
from .providers import Bar

__all__ = [
    "RollIndicator", "ContractMetadata", "ContractBook", "ContinuousSeriesRefused",
]


class ContinuousSeriesRefused(ContinuityError):
    """A joined series was requested without a policy that permits one."""


class RollIndicator(str, Enum):
    """Observed evidence that the market's attention moved to the next contract.

    An indicator is not a roll. It is a fact about volume or open interest that
    a roll policy may choose to act on, recorded separately so the policy can be
    changed without re-deriving the evidence.
    """

    NONE = "none"
    VOLUME_CROSSOVER = "volume_crossover"
    OPEN_INTEREST_CROSSOVER = "open_interest_crossover"
    EXPIRY_REACHED = "expiry_reached"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ContractMetadata:
    """What is known about one deliverable contract.

    ``first_seen`` and ``last_seen`` are properties of *this dataset*, not of
    the contract's real trading life. A contract listed in 2024 that our file
    only covers from 2025 has a ``first_seen`` of 2025, and conflating the two
    would overstate coverage.
    """

    instrument: str
    contract: str
    expiry: date | None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    roll_indicator: RollIndicator = RollIndicator.UNKNOWN
    roll_indicator_date: date | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if not self.instrument or not self.contract:
            raise ValueError("contract metadata needs both instrument and contract")
        if (self.first_seen is not None and self.last_seen is not None
                and self.last_seen < self.first_seen):
            raise ValueError(
                f"{self.contract}: last_seen precedes first_seen"
            )
        if (self.roll_indicator is not RollIndicator.NONE
                and self.roll_indicator is not RollIndicator.UNKNOWN
                and self.roll_indicator_date is None):
            raise ValueError(
                f"{self.contract}: roll_indicator {self.roll_indicator.value} claims an "
                "observed crossover but records no date for it"
            )

    @property
    def has_observed_roll_evidence(self) -> bool:
        return self.roll_indicator not in (RollIndicator.NONE, RollIndicator.UNKNOWN)

    @property
    def expiry_known(self) -> bool:
        return self.expiry is not None

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument,
            "contract": self.contract,
            "expiry": self.expiry.isoformat() if self.expiry else None,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "roll_indicator": self.roll_indicator.value,
            "roll_indicator_date": (self.roll_indicator_date.isoformat()
                                    if self.roll_indicator_date else None),
            "has_observed_roll_evidence": self.has_observed_roll_evidence,
            "note": self.note,
        }


class ContractBook:
    """Bars stored per contract, with no path to a stitched series.

    The omission is the design. There is no ``as_continuous()`` that quietly
    concatenates -- :meth:`continuous_series` exists solely to refuse, and to
    say what would have to be declared first.
    """

    def __init__(self, instrument: str) -> None:
        if not instrument:
            raise ValueError("a contract book needs an instrument")
        self.instrument = instrument
        self._bars: dict[tuple[str, str], list[Bar]] = defaultdict(list)
        self._metadata: dict[str, ContractMetadata] = {}

    # -- ingestion --------------------------------------------------------
    def add_bars(self, bars: Iterable[Bar]) -> int:
        """Ingest bars, refusing anything belonging to another instrument."""
        count = 0
        for bar in bars:
            if bar.instrument != self.instrument:
                raise ValueError(
                    f"bar for {bar.instrument} cannot enter a {self.instrument} book -- "
                    "mixing instruments is how a spread becomes an outright by accident"
                )
            self._bars[(bar.contract, bar.timeframe)].append(bar)
            count += 1
        for key in self._bars:
            self._bars[key].sort(key=lambda b: b.event_time)
        self._refresh_observed_window()
        return count

    def register_contract(self, metadata: ContractMetadata) -> ContractMetadata:
        if metadata.instrument != self.instrument:
            raise ValueError(
                f"{metadata.contract} belongs to {metadata.instrument}, not "
                f"{self.instrument}"
            )
        existing = self._metadata.get(metadata.contract)
        if existing is not None:
            from dataclasses import replace
            metadata = replace(metadata,
                               first_seen=existing.first_seen or metadata.first_seen,
                               last_seen=existing.last_seen or metadata.last_seen)
        self._metadata[metadata.contract] = metadata
        self._refresh_observed_window()
        return self._metadata[metadata.contract]

    def _refresh_observed_window(self) -> None:
        """Recompute first_seen/last_seen from the bars actually held."""
        from dataclasses import replace

        seen: dict[str, tuple[datetime, datetime]] = {}
        for (contract, _timeframe), bars in self._bars.items():
            if not bars:
                continue
            low, high = bars[0].event_time, bars[-1].event_time
            if contract in seen:
                previous_low, previous_high = seen[contract]
                low, high = min(low, previous_low), max(high, previous_high)
            seen[contract] = (low, high)

        for contract, (low, high) in seen.items():
            metadata = self._metadata.get(contract)
            if metadata is None:
                self._metadata[contract] = ContractMetadata(
                    self.instrument, contract, expiry=None,
                    first_seen=low, last_seen=high,
                    note="auto-registered from ingested bars; expiry unknown",
                )
            else:
                self._metadata[contract] = replace(metadata, first_seen=low,
                                                   last_seen=high)

    # -- queries ----------------------------------------------------------
    @property
    def contracts(self) -> list[str]:
        return sorted(self._metadata)

    @property
    def timeframes(self) -> list[str]:
        return sorted({timeframe for _c, timeframe in self._bars})

    def metadata(self, contract: str) -> ContractMetadata | None:
        return self._metadata.get(contract)

    def bars(self, contract: str, timeframe: str) -> list[Bar]:
        return list(self._bars.get((contract, timeframe), ()))

    def bar_count(self, contract: str | None = None,
                  timeframe: str | None = None) -> int:
        return sum(
            len(bars) for (c, t), bars in self._bars.items()
            if (contract is None or c == contract)
            and (timeframe is None or t == timeframe)
        )

    def __len__(self) -> int:
        return self.bar_count()

    # -- the refusal ------------------------------------------------------
    def continuous_series(self, policy: RollPolicy) -> Sequence[Bar]:
        """Refuse to build a joined series without a declared, adjusted roll.

        Delegates the decision to :meth:`RollPolicy.assert_continuous_claim`, so
        the Phase 7 rule stays in one place. Even when a policy does permit
        continuity, this method still refuses: constructing the adjusted series
        is unimplemented, and returning raw concatenated bars under a method
        named ``continuous_series`` would be worse than refusing.
        """
        policy.assert_continuous_claim()
        raise ContinuousSeriesRefused(
            f"{self.instrument}: roll policy {policy.method.value}/"
            f"{policy.adjustment.value} would permit a continuous claim, but no "
            "adjustment implementation exists. Research runs per contract; the "
            f"{len(self.contracts)} contract(s) in this book are not stitched."
        )

    def coverage_report(self) -> dict:
        return {
            "instrument": self.instrument,
            "contracts": [self._metadata[c].to_dict() for c in self.contracts],
            "timeframes": self.timeframes,
            "bars_by_contract": {
                contract: {
                    timeframe: len(self._bars[(contract, timeframe)])
                    for _c, timeframe in sorted(self._bars)
                    if _c == contract
                }
                for contract in self.contracts
            },
            "total_bars": self.bar_count(),
            "is_continuous": False,
            "continuity_note": (
                "individual contracts only; no roll policy applied and no adjustment "
                "implemented"
            ),
        }
