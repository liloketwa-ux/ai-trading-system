"""Event sampling policy.

Overlapping samples are the quiet way significance gets manufactured. If a setup
fires on twenty consecutive bars and each carries a 4-hour forward label, those
twenty "independent" observations share almost all of their outcome window. The
effective sample size is closer to one, but every test treats it as twenty and
the confidence interval shrinks by a factor of four for free.

So spacing is explicit, recorded on every experiment, and enforced before any
statistic is computed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..storage.records import utc

__all__ = ["SamplingPolicy", "Event", "apply_sampling"]


@dataclass(frozen=True)
class Event:
    """One candidate research observation."""

    instrument: str
    decision_time: datetime
    features: dict
    direction: int = 1
    entry_price: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_time", utc(self.decision_time))


@dataclass(frozen=True)
class SamplingPolicy:
    """How candidate events become an analysable sample.

    Attributes:
        min_spacing: Minimum gap between accepted events on one instrument.
            Should be at least the label horizon, or outcome windows overlap.
        exclusion_windows: Periods to drop entirely (holidays, known outages).
        deduplicate_overlaps: Drop events whose label window overlaps an already
            accepted event's.
        label_horizon: Used to compute overlap when deduplicating.
        max_events_per_instrument: Optional cap.
    """

    min_spacing: timedelta = timedelta(0)
    exclusion_windows: tuple[tuple[datetime, datetime], ...] = ()
    deduplicate_overlaps: bool = True
    label_horizon: timedelta = timedelta(hours=4)
    max_events_per_instrument: int | None = None
    version: str = "1"

    def to_dict(self) -> dict:
        return {
            "min_spacing_s": self.min_spacing.total_seconds(),
            "exclusion_windows": [
                [utc(a).isoformat(), utc(b).isoformat()] for a, b in self.exclusion_windows
            ],
            "deduplicate_overlaps": self.deduplicate_overlaps,
            "label_horizon_s": self.label_horizon.total_seconds(),
            "max_events_per_instrument": self.max_events_per_instrument,
            "version": self.version,
        }

    @property
    def effective_spacing(self) -> timedelta:
        """The spacing actually applied.

        When deduplicating overlaps, the label horizon is the binding
        constraint, since two events closer than one horizon share outcome bars.
        """
        if self.deduplicate_overlaps:
            return max(self.min_spacing, self.label_horizon)
        return self.min_spacing


def apply_sampling(events: list[Event], policy: SamplingPolicy) -> list[Event]:
    """Filter candidates into an analysable sample, oldest first."""
    ordered = sorted(events, key=lambda e: (e.instrument, e.decision_time))
    spacing = policy.effective_spacing

    accepted: list[Event] = []
    last_by_instrument: dict[str, datetime] = {}
    counts: dict[str, int] = {}

    for event in ordered:
        if any(utc(a) <= event.decision_time < utc(b) for a, b in policy.exclusion_windows):
            continue

        previous = last_by_instrument.get(event.instrument)
        if previous is not None and event.decision_time - previous < spacing:
            continue

        if policy.max_events_per_instrument is not None:
            if counts.get(event.instrument, 0) >= policy.max_events_per_instrument:
                continue

        accepted.append(event)
        last_by_instrument[event.instrument] = event.decision_time
        counts[event.instrument] = counts.get(event.instrument, 0) + 1

    return sorted(accepted, key=lambda e: e.decision_time)
