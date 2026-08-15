"""Rule verification status -- the gate between "we were told" and "we checked".

Prop-firm rules change frequently and the consequences of being wrong are
asymmetric: a ruleset that overstates the drawdown allowance produces a
simulation that passes accounts which would have blown, and the error is
invisible because the equity curve looks fine.

So a rule is not a number. It is a number *plus* how we came to believe it, and
a value that was never checked against the firm's own documentation cannot be
used to assert compliance. :meth:`RuleValue.require` raises rather than return a
plausible figure, which makes the simulator fail closed by construction instead
of by discipline.

Three statuses matter and are routinely conflated:

* ``VERIFIED_OFFICIAL`` -- read from the firm's own current documentation, with
  the URL and retrieval date recorded.
* ``USER_SUPPLIED`` -- someone stated it. Possibly correct, possibly stale,
  never a substitute for checking.
* ``UNKNOWN`` -- not established. The only honest label for a rule nobody has
  confirmed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Generic, TypeVar

__all__ = [
    "VerificationStatus", "SourceRef", "RuleValue", "UnverifiedRuleError",
    "UNKNOWN", "verified", "user_supplied", "unknown", "STALENESS_WINDOW",
]

T = TypeVar("T")

#: Beyond this, a previously verified rule is treated as stale and re-checked.
STALENESS_WINDOW = timedelta(days=90)


class UnverifiedRuleError(RuntimeError):
    """A compliance decision required a rule that is not verified."""


class VerificationStatus(str, Enum):
    VERIFIED_OFFICIAL = "verified_official"
    USER_SUPPLIED = "user_supplied"
    THIRD_PARTY = "third_party"       # explicitly insufficient on its own
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"

    @property
    def sufficient_for_compliance(self) -> bool:
        """Only an official source may back a compliance assertion.

        Third-party articles are excluded deliberately: they are the single
        largest source of stale prop-firm numbers, and they read as
        authoritative.
        """
        return self in (VerificationStatus.VERIFIED_OFFICIAL,
                        VerificationStatus.NOT_APPLICABLE)

    @property
    def has_value(self) -> bool:
        return self is not VerificationStatus.UNKNOWN


@dataclass(frozen=True)
class SourceRef:
    """Where a rule came from."""

    url: str = ""
    retrieved_at: datetime | None = None
    document_title: str = ""
    note: str = ""

    @property
    def is_stale(self) -> bool:
        if self.retrieved_at is None:
            return True
        return datetime.now(timezone.utc) - self.retrieved_at > STALENESS_WINDOW

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at else None,
            "document_title": self.document_title,
            "note": self.note,
            "is_stale": self.is_stale,
        }


@dataclass(frozen=True)
class RuleValue(Generic[T]):
    """A rule value carrying its provenance.

    ``get()`` returns the value or ``None``; ``require()`` raises unless the
    status is sufficient to back a compliance claim.
    """

    value: T | None
    status: VerificationStatus
    source: SourceRef = field(default_factory=SourceRef)
    label: str = ""

    def __post_init__(self) -> None:
        if self.status is VerificationStatus.UNKNOWN and self.value is not None:
            raise ValueError(
                f"{self.label or 'rule'}: UNKNOWN status cannot carry a value -- "
                "storing a guess alongside an unknown label is how guesses become facts"
            )

    def get(self, default: T | None = None) -> T | None:
        return self.value if self.value is not None else default

    def require(self, purpose: str = "compliance") -> T:
        """Return the value, or refuse if it cannot back a compliance claim."""
        if not self.status.sufficient_for_compliance:
            raise UnverifiedRuleError(
                f"{self.label or 'rule'} is {self.status.value} and cannot be used for "
                f"{purpose}. Verify it against the firm's official current "
                "documentation before enabling this capability."
            )
        if self.value is None:
            raise UnverifiedRuleError(f"{self.label or 'rule'} has no value")
        return self.value

    @property
    def is_verified(self) -> bool:
        return self.status.sufficient_for_compliance

    @property
    def is_unknown(self) -> bool:
        return self.status is VerificationStatus.UNKNOWN

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "value": self.value,
            "status": self.status.value,
            "verified": self.is_verified,
            "source": self.source.to_dict(),
        }

    def __repr__(self) -> str:
        if self.is_unknown:
            return f"RuleValue({self.label!r}=UNKNOWN)"
        return f"RuleValue({self.label!r}={self.value!r}, {self.status.value})"


def verified(value: T, url: str, retrieved_at: datetime, *, label: str = "",
             title: str = "") -> RuleValue[T]:
    """A rule read from the firm's official documentation."""
    return RuleValue(value, VerificationStatus.VERIFIED_OFFICIAL,
                     SourceRef(url, retrieved_at, title), label)


def user_supplied(value: T, *, label: str = "", note: str = "") -> RuleValue[T]:
    """A rule someone stated. Not a substitute for checking."""
    return RuleValue(value, VerificationStatus.USER_SUPPLIED,
                     SourceRef(note=note or "supplied by operator; not independently verified"),
                     label)


def unknown(label: str = "", note: str = "") -> RuleValue[Any]:
    """A rule nobody has established."""
    return RuleValue(None, VerificationStatus.UNKNOWN, SourceRef(note=note), label)


#: Convenience singleton for readability at call sites.
UNKNOWN = unknown()
