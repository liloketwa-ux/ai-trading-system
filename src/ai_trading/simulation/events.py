"""Simulation events, each carrying temporal provenance."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ..storage.records import utc

__all__ = ["EventType", "SimEvent", "make_bar_event"]


class EventType(str, Enum):
    BAR = "BAR"
    TRADE = "TRADE"
    ORDER_BOOK = "ORDER_BOOK"
    NEWS = "NEWS"
    FUNDING = "FUNDING"
    OPEN_INTEREST = "OPEN_INTEREST"
    SIGNAL = "SIGNAL"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_PARTIALLY_FILLED = "ORDER_PARTIALLY_FILLED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_REJECTED = "ORDER_REJECTED"
    POSITION_CHANGED = "POSITION_CHANGED"
    SESSION_OPEN = "SESSION_OPEN"
    SESSION_CLOSE = "SESSION_CLOSE"


@dataclass(frozen=True)
class SimEvent:
    """One event on the simulation timeline.

    ``available_at`` is when the simulator may act on it, which for a bar is its
    close -- the same rule the feature engine enforces, restated here so the
    event loop cannot bypass it.
    """

    event_type: EventType
    timestamp: datetime          # when the underlying thing happened
    available_at: datetime       # when the simulator may act on it
    instrument: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    sequence: int = 0            # tie-break for events sharing a timestamp

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", utc(self.timestamp))
        object.__setattr__(self, "available_at", utc(self.available_at))
        if self.available_at < self.timestamp:
            raise ValueError(
                f"{self.event_type}: available_at precedes timestamp -- an event "
                "cannot be actionable before it happens"
            )

    @property
    def sort_key(self) -> tuple:
        """Order by availability, then occurrence, then sequence.

        Availability first: two events that happened at the same instant but
        became knowable at different times must be processed in the order the
        simulator could actually have seen them.
        """
        return (self.available_at, self.timestamp, self.sequence)


def make_bar_event(
    instrument: str, bar_open: datetime, duration, values: dict, *, sequence: int = 0
) -> SimEvent:
    """A bar event available at its close."""
    return SimEvent(
        EventType.BAR, bar_open, utc(bar_open) + duration, instrument, values, sequence
    )
