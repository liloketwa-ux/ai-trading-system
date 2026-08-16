"""Pipeline latency measurement, and the refusal to assume it away.

Research on Solana data routinely assumes an event is actionable the instant it
occurs on chain. It is not. Between the block and a strategy there is indexer
lag, delivery, persistence and processing, and the sum is neither zero nor
constant. A backtest that ignores it earns its returns in a window that did not
exist.

Four stamps, in the only order they can occur:

``event_time``    when it happened on chain
``observed_at``   when our indexer surfaced it
``persisted_at``  when we durably stored it
``processed_at``  when a strategy could act on it

The distribution that matters is the tail. P50 latency describes a pipeline on
a quiet afternoon; P99 describes it when a mint goes viral, which is exactly
when a strategy would want to act. So percentiles are reported to P99 and max,
and the summary refuses to exist below a minimum sample count rather than
computing a P99 from eleven observations.

Until real measurements are taken, :class:`LatencyProfile.UNVERIFIED` is what
callers get, and :meth:`require_measured` refuses. Network access to the Solana
indexers is blocked in this environment, so that is the current state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from statistics import median
from typing import Sequence

__all__ = [
    "LatencyStage", "LatencyObservation", "LatencyProfile", "LatencyStatus",
    "LatencyInstrument", "UnmeasuredLatencyError", "MIN_SAMPLES",
]

#: Below this, percentile estimates describe the sample rather than the pipeline.
MIN_SAMPLES = 100


class UnmeasuredLatencyError(RuntimeError):
    """A latency figure was required before any had been measured."""


class LatencyStatus(str, Enum):
    UNVERIFIED = "unverified"
    INSUFFICIENT_SAMPLES = "insufficient_samples"
    MEASURED = "measured"

    @property
    def is_usable(self) -> bool:
        return self is LatencyStatus.MEASURED


class LatencyStage(str, Enum):
    """Which leg of the pipeline a measurement covers."""

    INDEXING = "indexing"          # event_time  -> observed_at
    PERSISTENCE = "persistence"    # observed_at -> persisted_at
    PROCESSING = "processing"      # persisted_at-> processed_at
    END_TO_END = "end_to_end"      # event_time  -> processed_at


@dataclass(frozen=True)
class LatencyObservation:
    """One event's journey through the pipeline."""

    event_time: datetime
    observed_at: datetime
    persisted_at: datetime
    processed_at: datetime
    source: str = ""

    def __post_init__(self) -> None:
        ordered = [self.event_time, self.observed_at,
                   self.persisted_at, self.processed_at]
        names = ["event_time", "observed_at", "persisted_at", "processed_at"]
        for (earlier, later), (first, second) in zip(
                zip(ordered, ordered[1:]), zip(names, names[1:])):
            if later < earlier:
                raise ValueError(
                    f"{second} precedes {first}: a stage cannot complete before the "
                    "one it depends on"
                )

    def stage(self, stage: LatencyStage) -> timedelta:
        if stage is LatencyStage.INDEXING:
            return self.observed_at - self.event_time
        if stage is LatencyStage.PERSISTENCE:
            return self.persisted_at - self.observed_at
        if stage is LatencyStage.PROCESSING:
            return self.processed_at - self.persisted_at
        return self.processed_at - self.event_time


def _percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile. No interpolation, no invented values."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-fraction * len(ordered) // 1))))
    return ordered[rank - 1]


@dataclass(frozen=True)
class LatencyProfile:
    """Measured latency for one stage, or an explicit refusal to claim one."""

    stage: LatencyStage
    status: LatencyStatus
    samples: int
    p50_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None
    max_ms: float | None = None
    note: str = ""

    @classmethod
    def unverified(cls, stage: LatencyStage, note: str) -> "LatencyProfile":
        return cls(stage, LatencyStatus.UNVERIFIED, 0, note=note)

    @property
    def is_measured(self) -> bool:
        return self.status.is_usable

    def require_measured(self) -> "LatencyProfile":
        if not self.is_measured:
            raise UnmeasuredLatencyError(
                f"{self.stage.value} latency is {self.status.value} "
                f"({self.samples} sample(s)); research must not assume zero "
                "indexing latency. " + (self.note or "")
            )
        return self

    def to_dict(self) -> dict:
        return {
            "stage": self.stage.value, "status": self.status.value,
            "samples": self.samples, "p50_ms": self.p50_ms, "p95_ms": self.p95_ms,
            "p99_ms": self.p99_ms, "max_ms": self.max_ms,
            "is_measured": self.is_measured, "note": self.note,
        }


class LatencyInstrument:
    """Collects observations and reports percentiles per stage."""

    def __init__(self, source: str, *, min_samples: int = MIN_SAMPLES) -> None:
        self.source = source
        self.min_samples = min_samples
        self._observations: list[LatencyObservation] = []

    def record(self, observation: LatencyObservation) -> LatencyObservation:
        self._observations.append(observation)
        return observation

    def record_event(self, *, event_time: datetime, observed_at: datetime,
                     persisted_at: datetime,
                     processed_at: datetime) -> LatencyObservation:
        return self.record(LatencyObservation(
            event_time, observed_at, persisted_at, processed_at, self.source))

    def __len__(self) -> int:
        return len(self._observations)

    def profile(self, stage: LatencyStage = LatencyStage.END_TO_END) -> LatencyProfile:
        if not self._observations:
            return LatencyProfile.unverified(
                stage,
                f"no observations recorded for {self.source}; the pipeline has never "
                "been measured",
            )
        values = [o.stage(stage).total_seconds() * 1000.0 for o in self._observations]
        if len(values) < self.min_samples:
            return LatencyProfile(
                stage, LatencyStatus.INSUFFICIENT_SAMPLES, len(values),
                note=(f"{len(values)} sample(s), {self.min_samples} required. A P99 "
                      "computed from this many observations describes the sample, "
                      "not the pipeline."),
            )
        return LatencyProfile(
            stage, LatencyStatus.MEASURED, len(values),
            p50_ms=float(median(values)),
            p95_ms=float(_percentile(values, 0.95)),
            p99_ms=float(_percentile(values, 0.99)),
            max_ms=float(max(values)),
            note=f"measured from {len(values):,} observations of {self.source}",
        )

    def report(self) -> dict:
        return {
            "source": self.source,
            "observations": len(self._observations),
            "min_samples": self.min_samples,
            "stages": {s.value: self.profile(s).to_dict() for s in LatencyStage},
        }
