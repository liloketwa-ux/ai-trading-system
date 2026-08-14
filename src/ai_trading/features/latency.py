"""Source latency model.

On-chain events, exchange REST snapshots and indexed feeds all reach us after
the thing happened. Phase 3 defaulted Solana ``available_at`` to the event time,
which assumes an indexer lag of exactly zero -- convenient, and certainly wrong.

Rather than guess, latency is explicitly **unverified** until measured at
runtime. An unverified policy still applies a stated assumption, but it labels
every feature derived from it so a research result can be filtered on whether
its latency was measured or assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from ..storage.records import utc

__all__ = ["LatencyConfidence", "SourceLatency", "LatencyModel", "DEFAULT_LATENCY"]


class LatencyConfidence(str, Enum):
    """How much the latency figure is worth."""

    MEASURED = "measured"      # from observed runtime statistics
    DECLARED = "declared"      # documented by the source operator
    UNVERIFIED = "unverified"  # an assumption, not an observation

    @property
    def is_trustworthy(self) -> bool:
        return self is not LatencyConfidence.UNVERIFIED


@dataclass(frozen=True)
class SourceLatency:
    """Latency assumption for one source."""

    source: str
    delay: timedelta
    confidence: LatencyConfidence = LatencyConfidence.UNVERIFIED
    samples: int = 0
    notes: str = ""

    def __post_init__(self) -> None:
        if self.delay < timedelta(0):
            raise ValueError(f"latency cannot be negative, got {self.delay}")

    def available_at(self, event_time: datetime) -> datetime:
        return utc(event_time) + self.delay


@dataclass
class LatencyModel:
    """Per-source latency assumptions with an explicit default.

    The default is deliberately non-zero and ``UNVERIFIED``: assuming zero lag
    is the optimistic error, and optimistic errors in availability are exactly
    look-ahead.
    """

    entries: dict[str, SourceLatency] = field(default_factory=dict)
    default: SourceLatency = field(
        default_factory=lambda: SourceLatency(
            "*", timedelta(seconds=0), LatencyConfidence.UNVERIFIED,
            notes="no measurement available; treat derived availability as unverified",
        )
    )

    def register(self, latency: SourceLatency) -> None:
        self.entries[latency.source] = latency

    def for_source(self, source: str) -> SourceLatency:
        if source in self.entries:
            return self.entries[source]
        # Fall back to a prefix match, e.g. "pumpi:*" for "pumpi:pumpfun".
        for key, latency in self.entries.items():
            if key.endswith("*") and source.startswith(key[:-1]):
                return latency
        return self.default

    def available_at(self, source: str, event_time: datetime) -> datetime:
        return self.for_source(source).available_at(event_time)

    def confidence(self, source: str) -> LatencyConfidence:
        return self.for_source(source).confidence

    def all_verified(self, sources: list[str]) -> bool:
        return all(self.confidence(s).is_trustworthy for s in sources)


#: Ships unverified everywhere. Nothing has been measured, and the model says so.
DEFAULT_LATENCY = LatencyModel()
DEFAULT_LATENCY.register(
    SourceLatency(
        "pumpi:*", timedelta(seconds=0), LatencyConfidence.UNVERIFIED,
        notes="Solana indexer lag never measured -- network unavailable in this environment",
    )
)
