"""Prop-firm rules as versioned, time-dependent data.

Phase 8 modelled a rule as a value plus its provenance. That is still not
enough to replay history, because a rule is also a *fact about a period*.
Topstep's Combine parameters in March are not necessarily Topstep's Combine
parameters in August, and adjudicating a March evaluation against August's
numbers produces a confident, wrong answer with no visible symptom.

So a rule here is a :class:`RuleSnapshot`: one field, one value, one validity
interval, one source. Looking rules up requires an ``as_of`` date, and there is
no overload that omits it. The absence of a default is the feature -- a default
would silently mean "today", which is precisely the bug.

Verification is five ordered levels and they are **not collapsed**:

* ``RUNTIME_VERIFIED`` -- observed from the firm's own platform or account API.
  The rule was seen operating, not read about.
* ``MACHINE_VERIFIED`` -- this code fetched the firm's documentation and parsed
  the value.
* ``SOURCE_VERIFIED`` -- a person read the firm's official documentation and
  attested to the value.
* ``USER_SUPPLIED`` -- someone stated it.
* ``UNKNOWN`` -- never established.

The top three all suffice to back a compliance claim, and the distinction still
matters afterwards: they differ in what re-checks them. A ``SOURCE_VERIFIED``
value goes stale silently when a human stops looking; a ``MACHINE_VERIFIED``
one can be re-derived on a schedule; a ``RUNTIME_VERIFIED`` one is contradicted
by the platform itself the moment it changes. Flattening them into "verified"
throws away the only information that says which failures are detectable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Iterable, Iterator

__all__ = [
    "RuleVerificationLevel", "RuleSnapshot", "RuleSnapshotStore",
    "RuleSnapshotError", "OverlappingSnapshotError", "NoRulesetError",
    "ResolvedRuleset", "get_ruleset",
]


class RuleSnapshotError(RuntimeError):
    """Base class for snapshot lookup failures."""


class OverlappingSnapshotError(RuleSnapshotError):
    """Two snapshots claim the same field on the same date."""


class NoRulesetError(RuleSnapshotError):
    """No rules were in force for the requested address and date."""


class RuleVerificationLevel(str, Enum):
    """How a value came to be believed, ordered weakest to strongest.

    Ordering exists so a caller can ask for "at least SOURCE_VERIFIED" without
    hard-coding which levels qualify. It does **not** license collapsing the
    levels: :attr:`rank` compares them, and nothing converts one into another.
    """

    UNKNOWN = "unknown"
    USER_SUPPLIED = "user_supplied"
    SOURCE_VERIFIED = "source_verified"
    MACHINE_VERIFIED = "machine_verified"
    RUNTIME_VERIFIED = "runtime_verified"

    @property
    def rank(self) -> int:
        return _LEVEL_RANK[self]

    @property
    def sufficient_for_compliance(self) -> bool:
        return self.rank >= RuleVerificationLevel.SOURCE_VERIFIED.rank

    @property
    def is_reverifiable(self) -> bool:
        """Whether staleness in this value is detectable without a human.

        ``SOURCE_VERIFIED`` is sufficient for compliance and still not
        re-verifiable: nothing re-reads the page, so the value rots silently.
        """
        return self.rank >= RuleVerificationLevel.MACHINE_VERIFIED.rank

    def __ge__(self, other: object) -> bool:      # type: ignore[override]
        if isinstance(other, RuleVerificationLevel):
            return self.rank >= other.rank
        return NotImplemented

    def __gt__(self, other: object) -> bool:      # type: ignore[override]
        if isinstance(other, RuleVerificationLevel):
            return self.rank > other.rank
        return NotImplemented

    def __le__(self, other: object) -> bool:      # type: ignore[override]
        if isinstance(other, RuleVerificationLevel):
            return self.rank <= other.rank
        return NotImplemented

    def __lt__(self, other: object) -> bool:      # type: ignore[override]
        if isinstance(other, RuleVerificationLevel):
            return self.rank < other.rank
        return NotImplemented


_LEVEL_RANK = {
    RuleVerificationLevel.UNKNOWN: 0,
    RuleVerificationLevel.USER_SUPPLIED: 1,
    RuleVerificationLevel.SOURCE_VERIFIED: 2,
    RuleVerificationLevel.MACHINE_VERIFIED: 3,
    RuleVerificationLevel.RUNTIME_VERIFIED: 4,
}


@dataclass(frozen=True)
class RuleSnapshot:
    """One rule field, valid over one interval, with its provenance.

    ``effective_to`` is exclusive and ``None`` means "still in force". The
    half-open interval is what makes two consecutive versions describe every
    instant exactly once, with no gap on the changeover day and no day that
    matches both.
    """

    firm_id: str
    program_id: str
    account_size: int
    field_name: str
    value: object
    effective_from: date
    effective_to: date | None
    source_url: str
    source_title: str
    retrieved_at: datetime | None
    verified_at: date | None
    verification_method: str
    verification_status: RuleVerificationLevel
    ruleset_version: str
    #: ``False`` when the rule does not exist for this program. A fact, not a
    #: gap: it carries no value and does not block readiness.
    applicable: bool = True
    note: str = ""

    def __post_init__(self) -> None:
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError(
                f"{self.field_name}: effective_to {self.effective_to} does not follow "
                f"effective_from {self.effective_from} -- an interval that ends before "
                "it starts matches no date and would silently drop the rule"
            )
        if self.verification_status is RuleVerificationLevel.UNKNOWN and self.value is not None:
            raise ValueError(
                f"{self.field_name}: an UNKNOWN snapshot cannot carry a value -- "
                "storing a guess beside an unknown label is how guesses become facts"
            )
        if not self.applicable and self.value is not None:
            raise ValueError(
                f"{self.field_name}: a rule marked not applicable cannot carry a value"
            )

    # -- temporal ---------------------------------------------------------
    def covers(self, as_of: date) -> bool:
        if as_of < self.effective_from:
            return False
        return self.effective_to is None or as_of < self.effective_to

    def overlaps(self, other: "RuleSnapshot") -> bool:
        """Whether two snapshots of the same field claim a shared date."""
        if self.address != other.address or self.field_name != other.field_name:
            return False
        left_end = self.effective_to or date.max
        right_end = other.effective_to or date.max
        return self.effective_from < right_end and other.effective_from < left_end

    @property
    def address(self) -> tuple[str, str, int]:
        return (self.firm_id, self.program_id, self.account_size)

    @property
    def is_open_ended(self) -> bool:
        return self.effective_to is None

    # -- verification -----------------------------------------------------
    @property
    def is_verified(self) -> bool:
        return (not self.applicable) or self.verification_status.sufficient_for_compliance

    def require(self, purpose: str = "compliance") -> object:
        if not self.applicable:
            raise RuleSnapshotError(
                f"{self.field_name} is not applicable to "
                f"{self.firm_id}/{self.program_id} and has no value to require"
            )
        if not self.verification_status.sufficient_for_compliance:
            raise RuleSnapshotError(
                f"{self.field_name} is {self.verification_status.value} and cannot be "
                f"used for {purpose}"
            )
        return self.value

    def to_dict(self) -> dict:
        return {
            "firm_id": self.firm_id,
            "program_id": self.program_id,
            "account_size": self.account_size,
            "field_name": self.field_name,
            "value": self.value.value if isinstance(self.value, Enum) else self.value,
            "effective_from": self.effective_from.isoformat(),
            "effective_to": self.effective_to.isoformat() if self.effective_to else None,
            "source_url": self.source_url,
            "source_title": self.source_title,
            "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at else None,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "verification_method": self.verification_method,
            "verification_status": self.verification_status.value,
            "ruleset_version": self.ruleset_version,
            "applicable": self.applicable,
            "note": self.note,
        }


@dataclass(frozen=True)
class ResolvedRuleset:
    """The rules in force at one instant, for one account.

    Carries ``as_of`` so a result can never be mistaken for "current". Every
    downstream report that prints a ruleset prints the date it was resolved for.
    """

    firm_id: str
    program_id: str
    account_size: int
    as_of: date
    snapshots: dict[str, RuleSnapshot]

    def __getitem__(self, field_name: str) -> RuleSnapshot:
        return self.snapshots[field_name]

    def __contains__(self, field_name: object) -> bool:
        return field_name in self.snapshots

    def __iter__(self) -> Iterator[str]:
        return iter(self.snapshots)

    def __len__(self) -> int:
        return len(self.snapshots)

    def get(self, field_name: str) -> RuleSnapshot | None:
        return self.snapshots.get(field_name)

    def value(self, field_name: str, default: object = None) -> object:
        snapshot = self.snapshots.get(field_name)
        return default if snapshot is None else snapshot.value

    def require(self, field_name: str, purpose: str = "compliance") -> object:
        snapshot = self.snapshots.get(field_name)
        if snapshot is None:
            raise RuleSnapshotError(
                f"{field_name} has no snapshot in force on {self.as_of.isoformat()} for "
                f"{self.firm_id}/{self.program_id}/{self.account_size}"
            )
        return snapshot.require(purpose)

    @property
    def ruleset_versions(self) -> list[str]:
        """Versions contributing to this resolution.

        Usually one. More than one means fields changed on different dates,
        which is normal and worth surfacing rather than hiding behind a single
        version label.
        """
        return sorted({s.ruleset_version for s in self.snapshots.values()})

    @property
    def unverified_fields(self) -> list[str]:
        return sorted(name for name, s in self.snapshots.items() if not s.is_verified)

    @property
    def fully_verified(self) -> bool:
        return not self.unverified_fields

    def at_least(self, level: RuleVerificationLevel) -> list[str]:
        """Fields meeting or exceeding a verification level."""
        return sorted(name for name, s in self.snapshots.items()
                      if s.applicable and s.verification_status >= level)

    def to_dict(self) -> dict:
        return {
            "firm_id": self.firm_id,
            "program_id": self.program_id,
            "account_size": self.account_size,
            "as_of": self.as_of.isoformat(),
            "ruleset_versions": self.ruleset_versions,
            "fully_verified": self.fully_verified,
            "unverified_fields": self.unverified_fields,
            "snapshots": {n: s.to_dict() for n, s in sorted(self.snapshots.items())},
        }


class RuleSnapshotStore:
    """Append-only collection of rule snapshots, queried by date.

    Overlaps are rejected at write time rather than resolved at read time. A
    store that silently picks one of two conflicting snapshots answers every
    query and is wrong on an unknown subset of them; one that refuses the write
    fails once, loudly, where the mistake was made.
    """

    def __init__(self, snapshots: Iterable[RuleSnapshot] = ()) -> None:
        self._snapshots: list[RuleSnapshot] = []
        for snapshot in snapshots:
            self.add(snapshot)

    def add(self, snapshot: RuleSnapshot) -> RuleSnapshot:
        for existing in self._snapshots:
            if existing.overlaps(snapshot):
                raise OverlappingSnapshotError(
                    f"{snapshot.field_name} for {snapshot.firm_id}/"
                    f"{snapshot.program_id}/{snapshot.account_size} already has a "
                    f"snapshot covering "
                    f"[{existing.effective_from}, {existing.effective_to or 'open'}); "
                    f"the new one covers "
                    f"[{snapshot.effective_from}, {snapshot.effective_to or 'open'}). "
                    "Close the earlier interval before opening a new one."
                )
        self._snapshots.append(snapshot)
        return snapshot

    def extend(self, snapshots: Iterable[RuleSnapshot]) -> None:
        for snapshot in snapshots:
            self.add(snapshot)

    def supersede(self, snapshot: RuleSnapshot, *, on: date) -> RuleSnapshot:
        """Close the open interval for a field and append its replacement.

        The ordinary way a firm rule changes. Closing the old interval at the
        same date the new one opens keeps the timeline gapless.
        """
        from dataclasses import replace

        for index, existing in enumerate(self._snapshots):
            if (existing.address == snapshot.address
                    and existing.field_name == snapshot.field_name
                    and existing.is_open_ended):
                if on <= existing.effective_from:
                    raise ValueError(
                        f"cannot supersede {snapshot.field_name} on {on}: the current "
                        f"snapshot only takes effect on {existing.effective_from}"
                    )
                self._snapshots[index] = replace(existing, effective_to=on)
                break
        return self.add(replace(snapshot, effective_from=on))

    # -- queries ----------------------------------------------------------
    def all(self) -> list[RuleSnapshot]:
        return sorted(self._snapshots,
                      key=lambda s: (s.firm_id, s.program_id, s.account_size,
                                     s.field_name, s.effective_from))

    def history(self, firm_id: str, program_id: str, account_size: int,
                field_name: str) -> list[RuleSnapshot]:
        """Every version of one field, oldest first."""
        return sorted(
            (s for s in self._snapshots
             if s.address == (firm_id, program_id, account_size)
             and s.field_name == field_name),
            key=lambda s: s.effective_from,
        )

    def get_ruleset(self, firm_id: str, program_id: str, account_size: int,
                    as_of: date) -> ResolvedRuleset:
        """The rules applicable on ``as_of``.

        ``as_of`` is mandatory. There is deliberately no default, because the
        only plausible default is "today", and quietly applying today's rules to
        a historical replay is the exact failure this class exists to prevent.
        """
        if as_of is None:
            raise TypeError(
                "get_ruleset requires an explicit as_of date -- defaulting to today "
                "would silently replay history against current rules"
            )
        address = (firm_id, program_id, account_size)
        resolved = {
            s.field_name: s for s in self._snapshots
            if s.address == address and s.covers(as_of)
        }
        if not resolved:
            raise NoRulesetError(
                f"no rules in force for {firm_id}/{program_id}/{account_size} on "
                f"{as_of.isoformat()}. The registry has "
                f"{len([s for s in self._snapshots if s.address == address])} snapshot(s) "
                "for this account at other dates; none covers this one. Current rules "
                "are not substituted."
            )
        return ResolvedRuleset(firm_id, program_id, account_size, as_of, resolved)

    def coverage(self, firm_id: str, program_id: str,
                 account_size: int) -> tuple[date | None, date | None]:
        """Earliest start and latest close for one account's snapshots.

        A ``None`` end means at least one field is still open-ended.
        """
        relevant = [s for s in self._snapshots
                    if s.address == (firm_id, program_id, account_size)]
        if not relevant:
            return (None, None)
        start = min(s.effective_from for s in relevant)
        if any(s.is_open_ended for s in relevant):
            return (start, None)
        return (start, max(s.effective_to for s in relevant))     # type: ignore[type-var]

    def __len__(self) -> int:
        return len(self._snapshots)


def get_ruleset(store: RuleSnapshotStore, firm_id: str, program_id: str,
                account_size: int, as_of: date) -> ResolvedRuleset:
    """Module-level form of :meth:`RuleSnapshotStore.get_ruleset`."""
    return store.get_ruleset(firm_id, program_id, account_size, as_of)
