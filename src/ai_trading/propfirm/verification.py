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
    "UNKNOWN", "verified", "user_supplied", "unknown", "not_applicable",
    "official_verified", "STALENESS_WINDOW", "VerificationMethod",
]

T = TypeVar("T")

#: Beyond this, a previously verified rule is treated as stale and re-checked.
STALENESS_WINDOW = timedelta(days=90)


class UnverifiedRuleError(RuntimeError):
    """A compliance decision required a rule that is not verified."""


class VerificationMethod(str, Enum):
    """How a value came to be believed.

    ``OFFICIAL_SOURCE_REVIEW`` records a human reading the firm's own current
    documentation and attesting to the values. It is materially stronger than an
    unsourced statement and materially weaker than a machine fetch of the page:
    nobody re-derives it automatically, so it carries a ``verified_at`` and goes
    stale on the same clock as any other verification.
    """

    OFFICIAL_SOURCE_REVIEW = "official_source_review"
    AUTOMATED_FETCH = "automated_fetch"
    OPERATOR_STATEMENT = "operator_statement"
    NONE = "none"


class VerificationStatus(str, Enum):
    #: A human reviewed the firm's official current documentation and attested
    #: to the value, recording the URL, title and review date.
    OFFICIAL_SOURCE_VERIFIED = "official_source_verified"
    VERIFIED_OFFICIAL = "verified_official"   # machine-fetched from the source
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
                        VerificationStatus.OFFICIAL_SOURCE_VERIFIED,
                        VerificationStatus.NOT_APPLICABLE)

    @property
    def has_value(self) -> bool:
        return self is not VerificationStatus.UNKNOWN

    @property
    def is_applicable(self) -> bool:
        """Whether the rule exists for this program at all.

        NOT_APPLICABLE is a fact, not a gap: a program with no daily loss limit
        is fully specified, and demanding verification of a rule that does not
        exist would block adjudication forever.
        """
        return self is not VerificationStatus.NOT_APPLICABLE


@dataclass(frozen=True)
class SourceRef:
    """Where a rule came from."""

    url: str = ""
    retrieved_at: datetime | None = None
    document_title: str = ""
    note: str = ""
    verified_at: date | None = None
    verification_method: "VerificationMethod" = None  # set in __post_init__

    def __post_init__(self) -> None:
        if self.verification_method is None:
            object.__setattr__(self, "verification_method", VerificationMethod.NONE)

    @property
    def is_stale(self) -> bool:
        """Whether the verification has aged out.

        Uses ``verified_at`` when a human performed the review, since that is
        the date the claim was actually checked.
        """
        if self.verified_at is not None:
            age = date.today() - self.verified_at
            return age > STALENESS_WINDOW
        if self.retrieved_at is None:
            return True
        return datetime.now(timezone.utc) - self.retrieved_at > STALENESS_WINDOW

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at else None,
            "document_title": self.document_title,
            "note": self.note,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "verification_method": self.verification_method.value,
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

    @property
    def is_applicable(self) -> bool:
        return self.status.is_applicable

    def require(self, purpose: str = "compliance") -> T:
        """Return the value, or refuse if it cannot back a compliance claim."""
        if self.status is VerificationStatus.NOT_APPLICABLE:
            raise UnverifiedRuleError(
                f"{self.label or 'rule'} is NOT_APPLICABLE to this program and has "
                "no value to require"
            )
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


def official_verified(
    value: T, *, url: str, title: str, verified_at: date, label: str = "",
) -> RuleValue[T]:
    """A value attested by human review of the firm's official documentation.

    Distinct from :func:`verified`, which means the code fetched the page. Both
    are sufficient for compliance; only this one records that a person, not a
    parser, read the source.
    """
    return RuleValue(
        value, VerificationStatus.OFFICIAL_SOURCE_VERIFIED,
        SourceRef(url=url, document_title=title, verified_at=verified_at,
                  verification_method=VerificationMethod.OFFICIAL_SOURCE_REVIEW),
        label,
    )


def not_applicable(label: str = "", note: str = "") -> RuleValue[Any]:
    """A rule that does not exist for this program.

    A fact rather than a gap, so it does not block adjudication readiness.
    """
    return RuleValue(None, VerificationStatus.NOT_APPLICABLE, SourceRef(note=note), label)


def unknown(label: str = "", note: str = "") -> RuleValue[Any]:
    """A rule nobody has established."""
    return RuleValue(None, VerificationStatus.UNKNOWN, SourceRef(note=note), label)


#: Convenience singleton for readability at call sites.
UNKNOWN = unknown()
