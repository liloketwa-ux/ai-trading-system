"""Monitoring events and their log.

Events are the monitoring system's output: a timestamped, severity-tagged
record of something worth a human's attention. They are deliberately plain
data — routing them to Slack, PagerDuty, or a dashboard is a separate concern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

import pandas as pd

__all__ = ["Severity", "Event", "EventLog"]


class Severity(IntEnum):
    """Ordered so severities can be compared and filtered numerically."""

    INFO = 10
    WARNING = 20
    CRITICAL = 30

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class Event:
    """A single monitoring observation."""

    kind: str
    severity: Severity
    message: str
    timestamp: pd.Timestamp | None = None
    details: dict[str, float] = field(default_factory=dict)

    def __str__(self) -> str:
        stamp = f"{self.timestamp} " if self.timestamp is not None else ""
        return f"[{self.severity}] {stamp}{self.kind}: {self.message}"


class EventLog:
    """An append-only log of monitoring events.

    Keeps the full history in memory; callers that need durability should
    forward events to their own sink as they are emitted.
    """

    def __init__(self) -> None:
        self._events: list[Event] = []

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self):
        return iter(self._events)

    def emit(
        self,
        kind: str,
        severity: Severity,
        message: str,
        *,
        timestamp: pd.Timestamp | None = None,
        **details: float,
    ) -> Event:
        """Record an event and return it."""
        event = Event(kind, severity, message, timestamp, details)
        self._events.append(event)
        return event

    @property
    def events(self) -> list[Event]:
        return list(self._events)

    def filter(
        self,
        *,
        min_severity: Severity = Severity.INFO,
        kind: str | None = None,
    ) -> list[Event]:
        """Events at or above ``min_severity``, optionally of one kind."""
        return [
            e
            for e in self._events
            if e.severity >= min_severity and (kind is None or e.kind == kind)
        ]

    @property
    def worst_severity(self) -> Severity | None:
        """Highest severity recorded, or ``None`` if the log is empty."""
        return max((e.severity for e in self._events), default=None)

    def to_frame(self) -> pd.DataFrame:
        """Events as a frame, for dashboards and export."""
        return pd.DataFrame(
            [
                {
                    "timestamp": e.timestamp,
                    "kind": e.kind,
                    "severity": str(e.severity),
                    "message": e.message,
                    **e.details,
                }
                for e in self._events
            ]
        )

    def clear(self) -> None:
        self._events.clear()
