"""How far an adapter has actually been proven.

"We have a Databento adapter" can mean anything from a file with the right
class name to a component that has served validated history into a study. The
gap between those is where most integration confidence is lost, so the states
are named and ordered, and promotion requires evidence rather than assertion.

* ``SOURCE_PRESENT`` -- code exists. Says nothing about whether it runs.
* ``UNIT_TESTED`` -- exercised against fixtures. Proves the parsing, not the feed.
* ``MACHINE_RETRIEVED`` -- this code pulled real bytes from the real endpoint.
* ``RUNTIME_VERIFIED`` -- retrieved data matched an independent reference.
* ``HISTORICALLY_VALIDATED`` -- a full requested range came back with coverage
  and continuity checked, not just a successful sample call.
* ``RESEARCH_APPROVED`` -- its data passed the quality gate.

The last promotion is the only one that cannot be granted by inspection:
:meth:`SourceLedger.promote` refuses ``RESEARCH_APPROVED`` without a passing
:class:`DatasetQualityReport` attached. That refusal is the point of the ladder.

Promotion is also strictly one step at a time. Jumping from ``SOURCE_PRESENT``
to ``RESEARCH_APPROVED`` because the data "looks fine" is exactly the move the
ladder exists to make visible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from .quality import DatasetQualityReport

__all__ = ["SourceStatus", "SourcePromotionError", "SourceRecord", "SourceLedger"]


class SourcePromotionError(RuntimeError):
    """A source was promoted without the evidence the level requires."""


class SourceStatus(str, Enum):
    SOURCE_PRESENT = "source_present"
    UNIT_TESTED = "unit_tested"
    MACHINE_RETRIEVED = "machine_retrieved"
    RUNTIME_VERIFIED = "runtime_verified"
    HISTORICALLY_VALIDATED = "historically_validated"
    RESEARCH_APPROVED = "research_approved"

    @property
    def rank(self) -> int:
        return _RANK[self]

    @property
    def has_touched_real_data(self) -> bool:
        """Whether real bytes from the real source have ever been seen."""
        return self.rank >= SourceStatus.MACHINE_RETRIEVED.rank

    @property
    def may_enter_research(self) -> bool:
        return self is SourceStatus.RESEARCH_APPROVED

    def next_level(self) -> "SourceStatus | None":
        ordered = sorted(SourceStatus, key=lambda s: s.rank)
        index = ordered.index(self)
        return ordered[index + 1] if index + 1 < len(ordered) else None


_RANK = {
    SourceStatus.SOURCE_PRESENT: 0,
    SourceStatus.UNIT_TESTED: 1,
    SourceStatus.MACHINE_RETRIEVED: 2,
    SourceStatus.RUNTIME_VERIFIED: 3,
    SourceStatus.HISTORICALLY_VALIDATED: 4,
    SourceStatus.RESEARCH_APPROVED: 5,
}


@dataclass(frozen=True)
class PromotionEvent:
    """One rung climbed, with the evidence offered."""

    to_status: SourceStatus
    at: datetime
    evidence: str

    def to_dict(self) -> dict:
        return {"to_status": self.to_status.value, "at": self.at.isoformat(),
                "evidence": self.evidence}


@dataclass
class SourceRecord:
    """One adapter's standing, with the history of how it got there."""

    source_name: str
    status: SourceStatus = SourceStatus.SOURCE_PRESENT
    history: list[PromotionEvent] = field(default_factory=list)
    quality_reports: list[DatasetQualityReport] = field(default_factory=list)
    blocked_reason: str = ""

    @property
    def may_enter_research(self) -> bool:
        return self.status.may_enter_research

    @property
    def is_blocked(self) -> bool:
        return bool(self.blocked_reason)

    def to_dict(self) -> dict:
        return {
            "source_name": self.source_name,
            "status": self.status.value,
            "may_enter_research": self.may_enter_research,
            "blocked_reason": self.blocked_reason,
            "history": [e.to_dict() for e in self.history],
            "quality_reports": [r.summary() for r in self.quality_reports],
        }


class SourceLedger:
    """Tracks every data source's standing. Promotions require evidence."""

    def __init__(self) -> None:
        self._records: dict[str, SourceRecord] = {}

    def register(self, source_name: str, *, blocked_reason: str = "") -> SourceRecord:
        if source_name in self._records:
            return self._records[source_name]
        record = SourceRecord(source_name, blocked_reason=blocked_reason)
        self._records[source_name] = record
        return record

    def get(self, source_name: str) -> SourceRecord | None:
        return self._records.get(source_name)

    def promote(self, source_name: str, to_status: SourceStatus, *,
                evidence: str,
                quality_report: DatasetQualityReport | None = None) -> SourceRecord:
        """Advance a source one rung, refusing unsupported claims."""
        record = self._records.get(source_name)
        if record is None:
            raise SourcePromotionError(
                f"{source_name} is not registered; register it before promoting it"
            )
        if not evidence:
            raise SourcePromotionError(
                f"promoting {source_name} to {to_status.value} requires evidence -- an "
                "unexplained promotion is indistinguishable from an assumption"
            )
        if to_status.rank <= record.status.rank:
            raise SourcePromotionError(
                f"{source_name} is already {record.status.value}; promotion to "
                f"{to_status.value} is not forward progress"
            )
        expected = record.status.next_level()
        if to_status is not expected:
            raise SourcePromotionError(
                f"{source_name} is {record.status.value} and the next rung is "
                f"{expected.value if expected else 'none'}, not {to_status.value}. "
                "Levels are climbed one at a time so that skipped evidence is visible."
            )
        if to_status is SourceStatus.RESEARCH_APPROVED:
            if quality_report is None:
                raise SourcePromotionError(
                    f"{source_name} cannot become RESEARCH_APPROVED without a quality "
                    "report; that promotion is the one claim that cannot be made by "
                    "inspection"
                )
            if not quality_report.is_research_eligible:
                raise SourcePromotionError(
                    f"{source_name} cannot become RESEARCH_APPROVED: its quality report "
                    f"is {quality_report.quality_status.value} with "
                    f"{len(quality_report.fatal_findings)} fatal finding(s) -- "
                    + "; ".join(f.check for f in quality_report.fatal_findings)
                )
            record.quality_reports.append(quality_report)

        record.status = to_status
        record.history.append(PromotionEvent(
            to_status, datetime.now(timezone.utc), evidence))
        return record

    def block(self, source_name: str, reason: str) -> SourceRecord:
        """Record that a source cannot currently be reached or used."""
        record = self.register(source_name)
        record.blocked_reason = reason
        return record

    def research_approved(self) -> list[SourceRecord]:
        return [r for r in self._records.values() if r.may_enter_research]

    def all(self) -> list[SourceRecord]:
        return sorted(self._records.values(), key=lambda r: r.source_name)

    def report(self) -> dict:
        return {
            "sources": [r.to_dict() for r in self.all()],
            "research_approved": [r.source_name for r in self.research_approved()],
            "count": len(self._records),
        }

    def __len__(self) -> int:
        return len(self._records)
