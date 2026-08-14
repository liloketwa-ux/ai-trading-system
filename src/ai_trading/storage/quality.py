"""Data-quality and availability semantics.

Lives in the storage layer because both storage and features depend on it and
the dependency must point one way: features may import storage, never the
reverse.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["DataQuality", "AvailabilityRule"]


class DataQuality(str, Enum):
    """Why a value is what it is.

    ``MISSING`` and ``ZERO`` are deliberately distinct: no observation at all
    versus an observation of nothing. Collapsing them into ``0.0`` silently
    changes trading meaning -- a strategy cannot tell "the venue reported no
    trades" from "we never received the bar".
    """

    OK = "ok"
    MISSING = "missing"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"
    ZERO = "zero"

    @property
    def usable(self) -> bool:
        """Whether a computation may consume this as a number."""
        return self in (DataQuality.OK, DataQuality.ZERO)

    @property
    def has_value(self) -> bool:
        return self.usable or self is DataQuality.STALE


class AvailabilityRule(str, Enum):
    """How a feature's availability was determined."""

    INPUT_MAX = "input_max"
    BAR_CLOSE = "bar_close"
    SESSION_CLOSE = "session_close"
    EXPLICIT_LATER = "explicit_later"
    INTRABAR = "intrabar"
