"""Point-in-time replay: what a decision at time T could actually see.

The filter is one comparison, ``available_at <= T``, and putting it in one place
is the entire defence. Look-ahead does not usually arrive as a bug in the
comparison; it arrives as a code path that never made it, because someone
iterated the raw list instead of asking the replay for a view.

So :class:`PointInTimeReplay` owns the bars and exposes no attribute that
returns all of them. Every accessor takes a decision time. The cursor is
monotonic as well: :meth:`advance` refuses to move backwards, because a replay
that can rewind can also re-decide with knowledge it did not have, and the
resulting equity curve looks like skill.

:meth:`assert_no_leakage` is the test-facing half. Given a decision time it
verifies that nothing in the visible set became available later -- which is what
an injected future observation trips.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Iterator, Sequence

from .availability import utc
from .providers import Bar

__all__ = ["PointInTimeReplay", "LeakageError", "LeakageReport"]


class LeakageError(RuntimeError):
    """Data that was not yet available appeared in a decision's view."""


@dataclass(frozen=True)
class LeakageReport:
    """What a leakage check found."""

    decision_time: datetime
    visible_rows: int
    leaked_rows: int
    examples: tuple[str, ...] = ()

    @property
    def is_clean(self) -> bool:
        return self.leaked_rows == 0

    def to_dict(self) -> dict:
        return {
            "decision_time": self.decision_time.isoformat(),
            "visible_rows": self.visible_rows,
            "leaked_rows": self.leaked_rows,
            "is_clean": self.is_clean,
            "examples": list(self.examples),
        }


class PointInTimeReplay:
    """A view over historical bars that only ever yields what was available.

    Holds bars sorted by ``available_at`` rather than ``event_time``. That is
    the ordering a live system experiences, and sorting by event time is how a
    late-arriving correction gets replayed as though it had been on time.
    """

    def __init__(self, bars: Iterable[Bar]) -> None:
        self._bars: list[Bar] = sorted(bars, key=lambda b: (b.available_at,
                                                           b.event_time))
        self._cursor: datetime | None = None

    def __len__(self) -> int:
        return len(self._bars)

    @property
    def cursor(self) -> datetime | None:
        return self._cursor

    # -- views ------------------------------------------------------------
    def visible_at(self, decision_time: datetime) -> list[Bar]:
        """Bars a decision at ``decision_time`` could have used."""
        moment = utc(decision_time)
        return [b for b in self._bars if b.available_at <= moment]

    def latest_at(self, decision_time: datetime, *, contract: str | None = None,
                  timeframe: str | None = None) -> Bar | None:
        """Most recently available bar, optionally filtered.

        Ordered by ``available_at``: the newest *knowable* bar, not the newest
        bar. Those differ exactly when data arrives late, which is when the
        distinction matters.
        """
        moment = utc(decision_time)
        candidates = [
            b for b in self._bars
            if b.available_at <= moment
            and (contract is None or b.contract == contract)
            and (timeframe is None or b.timeframe == timeframe)
        ]
        return candidates[-1] if candidates else None

    def advance(self, to: datetime) -> list[Bar]:
        """Move the cursor forward, returning newly available bars."""
        moment = utc(to)
        if self._cursor is not None and moment < self._cursor:
            raise LeakageError(
                f"cannot rewind the replay cursor from {self._cursor.isoformat()} to "
                f"{moment.isoformat()} -- a replay that moves backwards can re-decide "
                "with knowledge it did not have at the time"
            )
        previous, self._cursor = self._cursor, moment
        return [
            b for b in self._bars
            if b.available_at <= moment
            and (previous is None or b.available_at > previous)
        ]

    def steps(self, times: Sequence[datetime]) -> Iterator[tuple[datetime, list[Bar]]]:
        """Walk a schedule of decision times, yielding what arrived at each."""
        for moment in times:
            yield (utc(moment), self.advance(moment))

    # -- verification -----------------------------------------------------
    def check_leakage(self, decision_time: datetime) -> LeakageReport:
        moment = utc(decision_time)
        visible = self.visible_at(moment)
        leaked = [b for b in visible if b.available_at > moment]
        return LeakageReport(
            moment, len(visible), len(leaked),
            tuple(f"{b.contract} {b.event_time.isoformat()} "
                  f"available_at={b.available_at.isoformat()}" for b in leaked[:5]),
        )

    def assert_no_leakage(self, decision_time: datetime) -> LeakageReport:
        report = self.check_leakage(decision_time)
        if not report.is_clean:
            raise LeakageError(
                f"{report.leaked_rows} row(s) visible at {report.decision_time.isoformat()} "
                f"become available later: {'; '.join(report.examples)}"
            )
        return report

    def horizon(self) -> tuple[datetime, datetime] | None:
        """Availability span of the replay, or ``None`` when empty."""
        if not self._bars:
            return None
        return (self._bars[0].available_at, self._bars[-1].available_at)
