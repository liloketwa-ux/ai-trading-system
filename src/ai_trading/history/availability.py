"""When a historical datum could first have been used.

Backtests are wrong in one direction by default. A bar stamped 09:31:00 was not
usable at 09:31:00 -- it was usable once the venue published it and the
recipient received it, which is later by an amount nobody writes down. Treating
the exchange timestamp as the availability timestamp buys the strategy a free
look at the immediate future of every bar, and the resulting equity curve is
smooth, plausible and fictional.

The honest position is that most historical files cannot answer the question.
A CSV of OHLCV bars records when the *market* did something. It almost never
records when the *file* knew about it. So availability gets its own quality
label, and ``UNVERIFIED`` is the default rather than the exception:

* ``OBSERVED`` -- the source recorded an arrival or publication timestamp and
  we kept it. The only case with real precision.
* ``DERIVED`` -- computed from a documented, source-specific publication rule
  (a bar is published at its close plus a stated delay). Honest, but only as
  good as the rule.
* ``ASSUMED_BAR_CLOSE`` -- the conventional fallback: available at the close of
  the bar it belongs to. Defensible for bar data, still an assumption, and it
  ignores dissemination latency entirely.
* ``UNVERIFIED`` -- nothing about arrival is known. Research may run; claims
  about latency-sensitive behaviour may not.

The distinction is not bookkeeping. ``ASSUMED_BAR_CLOSE`` on hourly bars is
nearly harmless and on one-second bars is nearly worthless, and only the label
lets a reader tell which situation they are in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

__all__ = [
    "AvailabilityQuality", "AvailabilityPolicy", "AvailabilityError",
    "bar_close_availability", "utc",
]


class AvailabilityError(RuntimeError):
    """An availability claim could not be honestly made."""


def utc(value: datetime) -> datetime:
    """Normalise to UTC, refusing naive datetimes.

    A naive timestamp in market data is an unexploded bug: it means somebody's
    local timezone silently entered the dataset.
    """
    if value.tzinfo is None:
        raise AvailabilityError(
            f"naive datetime {value.isoformat()} -- market data timestamps must carry "
            "a timezone, or the ingesting machine's locale becomes part of the dataset"
        )
    return value.astimezone(timezone.utc)


class AvailabilityQuality(str, Enum):
    """How much the recorded ``available_at`` is actually worth."""

    OBSERVED = "observed"
    DERIVED = "derived"
    ASSUMED_BAR_CLOSE = "assumed_bar_close"
    UNVERIFIED = "unverified"

    @property
    def is_measured(self) -> bool:
        """Whether arrival was actually seen rather than reasoned about."""
        return self is AvailabilityQuality.OBSERVED

    @property
    def supports_latency_research(self) -> bool:
        """Whether conclusions about reaction speed may be drawn.

        Only an observed arrival time can support them. Everything else encodes
        an assumption whose error is exactly the quantity being measured.
        """
        return self is AvailabilityQuality.OBSERVED

    @property
    def is_usable_for_research(self) -> bool:
        """Whether the datum may enter point-in-time research at all.

        ``UNVERIFIED`` is permitted here and refused for latency claims. Bar
        research on a 15-minute timeframe does not collapse because
        dissemination latency is unmeasured; a claim about reacting within
        seconds does.
        """
        return True


@dataclass(frozen=True)
class AvailabilityPolicy:
    """How one source's ``available_at`` is established.

    Attached to a provider rather than to individual records, because it is a
    property of the feed and pretending otherwise invites per-row fudging.
    """

    quality: AvailabilityQuality
    #: Delay added to the reference time when ``quality`` is ``DERIVED``.
    publication_delay: timedelta = timedelta(0)
    #: Why this policy is defensible. Required for anything but UNVERIFIED, so
    #: that an assumption always ships with its justification.
    justification: str = ""

    def __post_init__(self) -> None:
        if self.quality is not AvailabilityQuality.UNVERIFIED and not self.justification:
            raise AvailabilityError(
                f"an availability policy of {self.quality.value} needs a justification -- "
                "an unexplained assumption about arrival time is indistinguishable from "
                "a guess, and it silently sets how much future the strategy can see"
            )
        if self.publication_delay < timedelta(0):
            raise AvailabilityError(
                "publication_delay cannot be negative: data does not arrive before it "
                "exists"
            )
        if (self.quality is AvailabilityQuality.DERIVED
                and self.publication_delay == timedelta(0)):
            raise AvailabilityError(
                "a DERIVED policy with zero delay is just ASSUMED_BAR_CLOSE wearing a "
                "better label; use that instead or state the real delay"
            )

    def available_at(self, *, event_time: datetime, bar_close: datetime | None = None,
                     observed_at: datetime | None = None) -> datetime:
        """Compute the availability timestamp for one record.

        ``observed_at`` wins whenever the source supplied it, regardless of
        policy: a real measurement always beats a rule about measurements.
        """
        if observed_at is not None:
            return utc(observed_at)
        if self.quality is AvailabilityQuality.OBSERVED:
            raise AvailabilityError(
                "policy claims OBSERVED availability but the record carries no "
                "observed_at -- the claim cannot be honoured for this row"
            )
        reference = utc(bar_close if bar_close is not None else event_time)
        if self.quality is AvailabilityQuality.DERIVED:
            return reference + self.publication_delay
        return reference

    def to_dict(self) -> dict:
        return {
            "quality": self.quality.value,
            "publication_delay_seconds": self.publication_delay.total_seconds(),
            "justification": self.justification,
            "supports_latency_research": self.quality.supports_latency_research,
        }


def bar_close_availability(justification: str) -> AvailabilityPolicy:
    """The conventional bar-data fallback, stated as an assumption."""
    return AvailabilityPolicy(AvailabilityQuality.ASSUMED_BAR_CLOSE,
                              justification=justification)
