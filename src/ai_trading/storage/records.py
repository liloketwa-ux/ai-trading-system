"""Append-only observation records with mandatory temporal provenance.

The load-bearing idea: an observation is eligible for a decision at time ``t``
only if it was *available* by ``t``. Event time is not enough. A liquidity
figure describing a pool as it stood on Monday but fetched on Friday is future
information on Monday, and merging it into Monday's feature row is look-ahead
even though its event time is Monday.

Availability is therefore explicit and tri-valued rather than merely present or
absent. A record whose availability cannot be established is marked
``UNKNOWN`` and is **excluded from point-in-time research** until resolved --
never silently treated as usable. Assuming usability is how leakage enters a
dataset that everyone believes is clean.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

__all__ = [
    "Availability",
    "Observation",
    "TemporalIntegrityError",
    "UnknownAvailabilityError",
    "utc",
]


class TemporalIntegrityError(RuntimeError):
    """A temporal invariant was violated."""


class UnknownAvailabilityError(TemporalIntegrityError):
    """Point-in-time research touched a record with unresolved availability."""


class Availability(str, Enum):
    """Whether we can say when a datum became usable."""

    KNOWN = "known"
    UNKNOWN = "unknown_availability"

    @property
    def usable_for_research(self) -> bool:
        return self is Availability.KNOWN


def utc(value: datetime) -> datetime:
    """Coerce to UTC. Naive input is assumed UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class Observation:
    """One immutable observation.

    Never mutated. Enrichment appends a new observation with a later
    ``available_at``; it does not overwrite an earlier one. That is what makes
    point-in-time reconstruction possible at all.

    Attributes:
        key: Instrument or token identifier.
        kind: Observation family -- ``ohlcv``, ``liquidity``, ``holders``,
            ``social``, ``news``, ``wallet``, ...
        event_time: When the underlying thing happened.
        available_at: When a decision could first have used it. ``None`` marks
            the record ``UNKNOWN`` availability.
        ingested_at: When we wrote it down.
        source: Producer identifier, e.g. ``ccxt:binanceusdm``, ``pumpi:pumpfun``.
        value: The payload.
        schema_version: Version of ``value``'s shape.
        dataset_version: Dataset this belongs to, if assigned.
        provenance_id: Stable identity for this observation.
        raw_ref: Pointer to the raw source artefact (tx signature, response id).
        timeframe: Bar timeframe where applicable.
        derived_from: Provenance ids of inputs, for derived observations.
    """

    key: str
    kind: str
    event_time: datetime
    available_at: datetime | None
    ingested_at: datetime
    source: str
    value: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "1"
    dataset_version: str | None = None
    provenance_id: str = ""
    raw_ref: str | None = None
    timeframe: str | None = None
    derived_from: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("key must not be empty")
        if not self.kind:
            raise ValueError("kind must not be empty")
        if not self.source:
            raise ValueError("source must not be empty")

        object.__setattr__(self, "event_time", utc(self.event_time))
        object.__setattr__(self, "ingested_at", utc(self.ingested_at))
        if self.available_at is not None:
            available = utc(self.available_at)
            object.__setattr__(self, "available_at", available)
            if available < self.event_time:
                raise TemporalIntegrityError(
                    f"{self.kind}/{self.key}: available_at {available.isoformat()} precedes "
                    f"event_time {self.event_time.isoformat()} -- a datum cannot be usable "
                    "before it exists"
                )
        if not self.provenance_id:
            object.__setattr__(self, "provenance_id", self.compute_id())

    # -- availability ------------------------------------------------------

    @property
    def availability(self) -> Availability:
        return Availability.KNOWN if self.available_at is not None else Availability.UNKNOWN

    def is_available_at(self, decision_time: datetime) -> bool:
        """Whether this record may be used for a decision at ``decision_time``.

        Unknown availability is never usable -- it returns False rather than
        guessing.
        """
        if self.available_at is None:
            return False
        return self.available_at <= utc(decision_time)

    # -- identity ----------------------------------------------------------

    def compute_id(self) -> str:
        """Content-addressed identity, stable across processes."""
        payload = json.dumps(
            {
                "key": self.key,
                "kind": self.kind,
                "event_time": self.event_time.isoformat(),
                "available_at": self.available_at.isoformat() if self.available_at else None,
                "source": self.source,
                "schema_version": self.schema_version,
                "timeframe": self.timeframe,
                "value": self.value,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    def with_availability(self, available_at: datetime) -> "Observation":
        """Return a *new* record with availability resolved.

        Resolution creates a new record; it never mutates this one.
        """
        from dataclasses import replace

        return replace(self, available_at=utc(available_at), provenance_id="")

    def to_row(self) -> dict[str, Any]:
        return {
            "provenance_id": self.provenance_id,
            "key": self.key,
            "kind": self.kind,
            "event_time": self.event_time,
            "available_at": self.available_at,
            "ingested_at": self.ingested_at,
            "source": self.source,
            "schema_version": self.schema_version,
            "dataset_version": self.dataset_version,
            "raw_ref": self.raw_ref,
            "timeframe": self.timeframe,
            "availability": self.availability.value,
            "derived_from": ",".join(self.derived_from),
            **{f"value_{k}": v for k, v in self.value.items()},
        }
