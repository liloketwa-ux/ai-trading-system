"""Timezone-aware, versioned trading-session definitions.

Sessions are defined in a named IANA zone and converted from UTC on demand. The
machine's local timezone is never consulted -- a research result that changes
because it was run on a laptop in a different country is not a result.

Definitions are **versioned**. Exchanges move session times; silently changing a
definition retroactively rewrites every historical session feature computed from
it, so a change means a new version and features record which one they used.

DST is handled by ``zoneinfo`` rather than fixed offsets. London is UTC+0 in
January and UTC+1 in July, so a session defined as 08:00 London is a different
UTC hour depending on the date -- a fixed-offset implementation is wrong for
roughly half the year.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from ..storage.records import utc

__all__ = ["SessionDefinition", "SessionWindow", "ASIA", "LONDON", "NEW_YORK", "CME_EQUITY", "SESSIONS"]


@dataclass(frozen=True)
class SessionWindow:
    """A concrete session occurrence in UTC."""

    name: str
    version: str
    session_date: date
    start: datetime
    end: datetime

    def contains(self, moment: datetime) -> bool:
        return self.start <= utc(moment) < self.end

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


@dataclass(frozen=True)
class SessionDefinition:
    """A named session in a named timezone.

    Attributes:
        name: Session identifier used in feature names.
        timezone: IANA zone, e.g. ``"Europe/London"``.
        start: Local session start.
        end: Local session end. May be before ``start`` for sessions that cross
            midnight, in which case the session ends on the following day.
        version: Definition version. Changing times requires a new version.
    """

    name: str
    timezone: str
    start: time
    end: time
    version: str = "1"

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except Exception as exc:  # noqa: BLE001 - re-raised with context
            raise ValueError(f"unknown timezone {self.timezone!r}") from exc
        if self.start == self.end:
            raise ValueError(f"session {self.name}: start equals end")

    @property
    def crosses_midnight(self) -> bool:
        return self.end < self.start

    @property
    def key(self) -> str:
        return f"{self.name}:v{self.version}"

    def window_for(self, session_date: date) -> SessionWindow:
        """The UTC window for one local session date.

        Localization goes through ``zoneinfo``, so the UTC offset is whatever
        applied on that date -- DST included.
        """
        zone = ZoneInfo(self.timezone)
        start_local = datetime.combine(session_date, self.start, tzinfo=zone)
        end_date = session_date + timedelta(days=1) if self.crosses_midnight else session_date
        end_local = datetime.combine(end_date, self.end, tzinfo=zone)
        return SessionWindow(
            self.name, self.version, session_date,
            start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc),
        )

    def session_date_of(self, moment: datetime) -> date:
        """Which local session date a UTC instant belongs to."""
        local = utc(moment).astimezone(ZoneInfo(self.timezone))
        if self.crosses_midnight and local.time() < self.end:
            return (local - timedelta(days=1)).date()
        return local.date()

    def window_containing(self, moment: datetime) -> SessionWindow | None:
        """The session window containing ``moment``, or ``None`` if outside."""
        window = self.window_for(self.session_date_of(moment))
        return window if window.contains(moment) else None

    def is_open(self, moment: datetime) -> bool:
        return self.window_containing(moment) is not None

    def previous_completed(self, decision_time: datetime) -> SessionWindow | None:
        """The most recent session that had **closed** by ``decision_time``.

        This is the availability rule for previous-session levels: yesterday's
        high is not knowable until yesterday's session has actually ended.
        """
        moment = utc(decision_time)
        candidate = self.session_date_of(moment)
        # Walk back until a window has closed. Two steps suffice, but bound it.
        for _ in range(4):
            window = self.window_for(candidate)
            if window.end <= moment:
                return window
            candidate -= timedelta(days=1)
        return None


#: Conventional FX/crypto session blocks. Times are local to each zone.
ASIA = SessionDefinition("asia", "Asia/Tokyo", time(9, 0), time(15, 0))
LONDON = SessionDefinition("london", "Europe/London", time(8, 0), time(16, 30))
NEW_YORK = SessionDefinition("new_york", "America/New_York", time(9, 30), time(16, 0))
#: CME equity-index futures: opens 17:00 CT and runs to 16:00 CT next day.
CME_EQUITY = SessionDefinition("cme_equity", "America/Chicago", time(17, 0), time(16, 0))

SESSIONS: dict[str, SessionDefinition] = {
    s.name: s for s in (ASIA, LONDON, NEW_YORK, CME_EQUITY)
}
