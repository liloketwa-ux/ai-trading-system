"""Dataset grades: five gates, climbed in order, each meaning one thing.

Phase 9 had a single `QualityStatus`, which conflated questions that fail
independently. A dataset can be structurally perfect and still be forbidden
from supporting a market claim -- synthetic data is exactly that, and so is a
real dataset whose provenance was never established. One flag cannot carry both
facts.

So the ladder:

``SOURCE_VALID``          the source is identified, legitimate and named.
``DATA_QUALITY_VALID``    the rows survive the quality gate.
``POINT_IN_TIME_VALID``   availability semantics are coherent and replayable.
``RESEARCH_GRADE``        all of the above; research may run on it.
``MARKET_CLAIM_ALLOWED``  the data is real, so conclusions may describe a market.

The last rung is the one that matters and the one that is not implied by the
others. ``RESEARCH_GRADE`` synthetic data is a legitimate, useful thing: it
calibrates the machinery. It cannot tell you anything about NQ, and the ladder
says so structurally rather than relying on a reader remembering.

Each rung records *why* it was or was not granted, because "not research grade"
is not actionable and "point-in-time invalid: 3 rows available before their
event time" is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from .datasets import DataOrigin
from .quality import DatasetQualityReport

__all__ = [
    "DatasetGrade", "GradeAssessment", "GradeResult", "assess_grades",
    "GradeError",
]


class GradeError(RuntimeError):
    """A capability was used at a grade that does not permit it."""


class DatasetGrade(str, Enum):
    """Ordered gates. Each requires every gate below it."""

    SOURCE_VALID = "source_valid"
    DATA_QUALITY_VALID = "data_quality_valid"
    POINT_IN_TIME_VALID = "point_in_time_valid"
    RESEARCH_GRADE = "research_grade"
    MARKET_CLAIM_ALLOWED = "market_claim_allowed"

    @property
    def rank(self) -> int:
        return _RANK[self]

    @property
    def permits_research(self) -> bool:
        return self.rank >= DatasetGrade.RESEARCH_GRADE.rank

    @property
    def permits_market_claims(self) -> bool:
        return self is DatasetGrade.MARKET_CLAIM_ALLOWED


_RANK = {
    DatasetGrade.SOURCE_VALID: 0,
    DatasetGrade.DATA_QUALITY_VALID: 1,
    DatasetGrade.POINT_IN_TIME_VALID: 2,
    DatasetGrade.RESEARCH_GRADE: 3,
    DatasetGrade.MARKET_CLAIM_ALLOWED: 4,
}


@dataclass(frozen=True)
class GradeAssessment:
    """One rung: granted or not, and why."""

    grade: DatasetGrade
    granted: bool
    reason: str

    def to_dict(self) -> dict:
        return {"grade": self.grade.value, "granted": self.granted,
                "reason": self.reason}


@dataclass(frozen=True)
class GradeResult:
    """The full ladder for one dataset."""

    assessments: tuple[GradeAssessment, ...]
    highest: DatasetGrade | None
    assessed_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))

    def granted(self, grade: DatasetGrade) -> bool:
        """Whether a specific rung was granted.

        Rungs are cumulative: a dataset that failed quality cannot be
        point-in-time valid even if its timestamps are individually fine,
        because the rows underneath are not trustworthy.
        """
        for assessment in self.assessments:
            if assessment.grade is grade:
                return assessment.granted
        return False

    @property
    def permits_research(self) -> bool:
        return self.granted(DatasetGrade.RESEARCH_GRADE)

    @property
    def permits_market_claims(self) -> bool:
        return self.granted(DatasetGrade.MARKET_CLAIM_ALLOWED)

    @property
    def blocking_reason(self) -> str:
        """Why the ladder stopped where it did."""
        for assessment in self.assessments:
            if not assessment.granted:
                return f"{assessment.grade.value}: {assessment.reason}"
        return ""

    def require(self, grade: DatasetGrade, purpose: str = "this operation") -> None:
        if not self.granted(grade):
            raise GradeError(
                f"{purpose} requires {grade.value}, which was not granted. "
                + (self.blocking_reason or "no reason recorded")
            )

    def to_dict(self) -> dict:
        return {
            "highest": self.highest.value if self.highest else None,
            "permits_research": self.permits_research,
            "permits_market_claims": self.permits_market_claims,
            "blocking_reason": self.blocking_reason,
            "assessments": [a.to_dict() for a in self.assessments],
            "assessed_at": self.assessed_at.isoformat(),
        }


def assess_grades(*, source_name: str, origin: DataOrigin,
                  quality_report: DatasetQualityReport,
                  point_in_time_clean: bool,
                  point_in_time_note: str = "",
                  source_is_identified: bool = True,
                  source_note: str = "") -> GradeResult:
    """Walk the ladder, stopping at the first rung that fails.

    Stopping rather than continuing is deliberate. Granting
    ``POINT_IN_TIME_VALID`` on a dataset whose rows failed the quality gate
    would be technically defensible and practically misleading -- the
    timestamps of untrustworthy rows are not a useful assurance.
    """
    assessments: list[GradeAssessment] = []

    source_ok = bool(source_name) and source_is_identified
    assessments.append(GradeAssessment(
        DatasetGrade.SOURCE_VALID, source_ok,
        source_note or (f"source identified as {source_name!r}" if source_ok
                        else "source is not identified; an unnamed origin cannot "
                             "be assessed for legitimacy")))

    quality_ok = source_ok and quality_report.is_research_eligible
    if not source_ok:
        quality_reason = "not assessed: source is not valid"
    elif quality_report.is_research_eligible:
        quality_reason = (f"quality gate returned "
                          f"{quality_report.quality_status.value}")
    else:
        failing = ", ".join(f.check for f in quality_report.fatal_findings)
        quality_reason = (f"quality gate returned "
                          f"{quality_report.quality_status.value}: {failing}")
    assessments.append(GradeAssessment(DatasetGrade.DATA_QUALITY_VALID,
                                       quality_ok, quality_reason))

    pit_ok = quality_ok and point_in_time_clean
    if not quality_ok:
        pit_reason = "not assessed: data quality is not valid"
    elif point_in_time_clean:
        pit_reason = point_in_time_note or "replay showed no availability leakage"
    else:
        pit_reason = point_in_time_note or "availability semantics are incoherent"
    assessments.append(GradeAssessment(DatasetGrade.POINT_IN_TIME_VALID,
                                       pit_ok, pit_reason))

    research_ok = pit_ok
    assessments.append(GradeAssessment(
        DatasetGrade.RESEARCH_GRADE, research_ok,
        "source, quality and point-in-time gates all passed" if research_ok
        else "blocked by a lower gate"))

    market_ok = research_ok and origin.may_support_market_claims
    if not research_ok:
        market_reason = "not assessed: dataset is not research grade"
    elif origin.may_support_market_claims:
        market_reason = "origin is real market data"
    else:
        market_reason = (
            f"origin is {origin.value}: the dataset is research grade and still "
            "cannot describe a market. Results computed on it describe the "
            "generator.")
    assessments.append(GradeAssessment(DatasetGrade.MARKET_CLAIM_ALLOWED,
                                       market_ok, market_reason))

    granted = [a.grade for a in assessments if a.granted]
    highest = max(granted, key=lambda g: g.rank) if granted else None
    return GradeResult(tuple(assessments), highest)
