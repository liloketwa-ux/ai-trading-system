"""Point-in-time feature snapshots.

A feature is not a number in a DataFrame cell. It is a value plus the instant
it became knowable, and eligibility is decided by that instant -- never by row
order. Row order is an artefact of how a frame was assembled; a join, a resample
or a merge can reorder it without changing what was actually knowable when.

**Derived features inherit the latest input's availability.** A feature computed
from three inputs is knowable only once the last of them is, so its
``available_at`` is the maximum over its inputs. Declaring anything earlier
would let a decision consume a value it could not yet have computed, which is
look-ahead wearing a derived-feature costume.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Sequence

from .quality import AvailabilityRule, DataQuality
from .records import Observation, TemporalIntegrityError, utc

__all__ = ["FeatureSnapshot", "derive_feature"]


@dataclass(frozen=True)
class FeatureSnapshot:
    """One feature value with the provenance needed to use it safely."""

    name: str
    value: Any
    event_time: datetime
    available_at: datetime
    source: str
    feature_version: str = "1"
    inputs: tuple[str, ...] = ()
    instrument: str = ""
    timeframe: str | None = None
    dataset_version: str | None = None
    provenance_id: str = ""
    derived_from: tuple[str, ...] = ()
    data_quality: DataQuality = DataQuality.OK
    availability_rule: AvailabilityRule = AvailabilityRule.INPUT_MAX

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("feature name must not be empty")
        object.__setattr__(self, "event_time", utc(self.event_time))
        object.__setattr__(self, "available_at", utc(self.available_at))
        if self.available_at < self.event_time:
            raise TemporalIntegrityError(
                f"feature {self.name}: available_at precedes event_time"
            )

        if not self.provenance_id:
            object.__setattr__(self, "provenance_id", self._compute_id())

    def _compute_id(self) -> str:
        import hashlib

        payload = "|".join([
            self.name, str(self.feature_version), self.instrument,
            str(self.timeframe), self.event_time.isoformat(),
            self.available_at.isoformat(), str(self.value),
        ])
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    @property
    def key(self) -> str:
        """Versioned identity, e.g. ``atr:v1``."""
        return f"{self.name}:v{self.feature_version}"

    @property
    def usable(self) -> bool:
        """Whether the value may be consumed as a number."""
        return self.data_quality.usable

    def is_eligible_at(self, decision_time: datetime) -> bool:
        """Eligible only if available AND of usable quality.

        Availability alone is not enough: a value that arrived on time but is
        MISSING must not be silently treated as a number.
        """
        return self.available_at <= utc(decision_time) and self.data_quality.has_value

    def to_observation(self, key: str, ingested_at: datetime) -> Observation:
        return Observation(
            key=key,
            kind=f"feature:{self.name}",
            event_time=self.event_time,
            available_at=self.available_at,
            ingested_at=ingested_at,
            source=self.source,
            timeframe=self.timeframe,
            dataset_version=self.dataset_version,
            value={
                "value": self.value,
                "feature_version": self.feature_version,
                "data_quality": self.data_quality.value,
                "availability_rule": self.availability_rule.value,
            },
            derived_from=self.inputs,
        )


def derive_feature(
    name: str,
    inputs: Sequence[Observation | FeatureSnapshot],
    compute: Callable[[list[Any]], Any],
    *,
    source: str = "derived",
    feature_version: str = "1",
    available_at: datetime | None = None,
) -> FeatureSnapshot:
    """Compute a feature from inputs, propagating availability correctly.

    Availability is the **maximum** over the inputs': the result is knowable
    only once every input is. An explicit ``available_at`` may be supplied but
    is rejected if it is earlier than that bound.

    Raises:
        TemporalIntegrityError: if an input has unresolved availability, or if
            an explicit ``available_at`` would precede the inputs'.
    """
    if not inputs:
        raise ValueError("cannot derive a feature from no inputs")

    availabilities: list[datetime] = []
    event_times: list[datetime] = []
    ids: list[str] = []
    values: list[Any] = []

    for item in inputs:
        if isinstance(item, Observation):
            if item.available_at is None:
                raise TemporalIntegrityError(
                    f"feature {name}: input {item.kind}/{item.key} has UNKNOWN_AVAILABILITY "
                    "and cannot be used in point-in-time research"
                )
            availabilities.append(item.available_at)
            event_times.append(item.event_time)
            ids.append(item.provenance_id)
            values.append(item.value)
        else:
            availabilities.append(item.available_at)
            event_times.append(item.event_time)
            ids.append(f"feature:{item.name}")
            values.append(item.value)

    bound = max(availabilities)
    if available_at is not None:
        explicit = utc(available_at)
        if explicit < bound:
            raise TemporalIntegrityError(
                f"feature {name}: declared available_at {explicit.isoformat()} precedes its "
                f"inputs' availability {bound.isoformat()} -- a derived value cannot be "
                "knowable before the data it is computed from"
            )
        bound = explicit

    return FeatureSnapshot(
        name=name,
        value=compute(values),
        event_time=max(event_times),
        available_at=bound,
        source=source,
        feature_version=feature_version,
        inputs=tuple(ids),
    )
