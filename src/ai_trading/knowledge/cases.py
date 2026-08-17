"""Case cards as educational examples, never as samples.

A case library looks like data and is not. The cases in OpenMobius were
extracted by vision-language models from trading-education videos, and they are
selected by what somebody chose to teach -- which is overwhelmingly what
worked. Counting outcomes across them measures the pedagogy, not the market.

So every case carries ``case_is_educational_example = True``, immutably, and
the type offers no aggregate. There is no ``win_rate()``, no ``success_count``,
and :func:`case_outcome_statistics` exists only to raise with an explanation.
The refusal is the feature: somebody will eventually want that number, and the
right time to explain why it does not exist is before they compute it
themselves.

Extraction provenance is kept because it bounds what the card is worth. A card
with ``review_status='pending'`` and ``extraction_confidence='medium'`` is one
model's reading of a video nobody checked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

__all__ = [
    "TradingCase", "CaseProvenance", "CaseIndex", "CaseUseError",
    "case_outcome_statistics", "ReviewStatus",
]


class CaseUseError(RuntimeError):
    """A case library was used as if it were a sample."""


class ReviewStatus(str, Enum):
    PENDING = "pending"
    HUMAN_REVIEWED = "human_reviewed"
    REJECTED = "rejected"

    @property
    def is_reviewed(self) -> bool:
        return self is ReviewStatus.HUMAN_REVIEWED


@dataclass(frozen=True)
class CaseProvenance:
    """How the card came to exist, which bounds what it is worth."""

    source_project: str = ""
    source_reference: str = ""       # e.g. an upstream video or document id
    extracted_by_model: str = ""
    extraction_confidence: str = ""
    review_status: ReviewStatus = ReviewStatus.PENDING

    @property
    def is_machine_extracted(self) -> bool:
        return bool(self.extracted_by_model)

    @property
    def is_human_reviewed(self) -> bool:
        return self.review_status.is_reviewed

    def to_dict(self) -> dict:
        return {
            "source_project": self.source_project,
            "source_reference": self.source_reference,
            "extracted_by_model": self.extracted_by_model,
            "extraction_confidence": self.extraction_confidence,
            "review_status": self.review_status.value,
            "is_machine_extracted": self.is_machine_extracted,
            "is_human_reviewed": self.is_human_reviewed,
        }


@dataclass(frozen=True)
class TradingCase:
    """One worked example. Educational, always."""

    case_id: str
    source: str
    asset: str
    timeframe: str
    concepts: tuple[str, ...]
    context: str
    observations: str
    analysis_steps: tuple[str, ...]
    lessons: str
    source_time_range: tuple[datetime, datetime] | None = None
    provenance: CaseProvenance = field(default_factory=CaseProvenance)

    #: Immutable. Set in ``__post_init__`` and rejected if anyone passes False.
    case_is_educational_example: bool = True

    def __post_init__(self) -> None:
        if not self.case_id:
            raise CaseUseError("a case needs an id")
        if not self.case_is_educational_example:
            raise CaseUseError(
                f"{self.case_id}: a case card cannot be marked as anything but an "
                "educational example. Cases are selected by what somebody chose to "
                "teach, so treating them as observations would measure the "
                "pedagogy rather than the market."
            )

    @property
    def is_statistically_representative(self) -> bool:
        """Always ``False``. Selection is by teaching value, not by sampling."""
        return False

    @property
    def may_support_a_probability(self) -> bool:
        return False

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id, "source": self.source, "asset": self.asset,
            "timeframe": self.timeframe, "concepts": list(self.concepts),
            "context": self.context, "observations": self.observations,
            "analysis_steps": list(self.analysis_steps), "lessons": self.lessons,
            "source_time_range": (
                [self.source_time_range[0].isoformat(),
                 self.source_time_range[1].isoformat()]
                if self.source_time_range else None),
            "provenance": self.provenance.to_dict(),
            "case_is_educational_example": True,
            "is_statistically_representative": False,
        }


def case_outcome_statistics(cases) -> None:
    """Refuse to aggregate case outcomes. Always raises.

    Present so the refusal is discoverable at the moment somebody reaches for
    it, rather than being a paragraph in a document they did not read.
    """
    count = len(list(cases))
    raise CaseUseError(
        f"refusing to compute outcome statistics over {count} case card(s). "
        "Cases are educational examples selected for teaching value, so the "
        "sample is chosen by outcome. Any rate computed from them measures how "
        "often the pattern was taught, not how often it worked. Test the "
        "concept against real market data through a pre-registered hypothesis "
        "instead."
    )


class CaseIndex:
    """Cases by id, searchable by concept and asset. No aggregates."""

    def __init__(self) -> None:
        self._cases: dict[str, TradingCase] = {}

    def add(self, case: TradingCase) -> TradingCase:
        if case.case_id in self._cases:
            raise CaseUseError(f"{case.case_id} is already indexed")
        self._cases[case.case_id] = case
        return case

    def get(self, case_id: str) -> TradingCase | None:
        return self._cases.get(case_id)

    def all(self) -> list[TradingCase]:
        return sorted(self._cases.values(), key=lambda c: c.case_id)

    def by_concept(self, concept_id: str) -> list[TradingCase]:
        return [c for c in self.all() if concept_id in c.concepts]

    def by_asset(self, asset: str) -> list[TradingCase]:
        return [c for c in self.all() if c.asset == asset]

    def __len__(self) -> int:
        return len(self._cases)

    def summary(self) -> dict:
        reviewed = sum(1 for c in self.all() if c.provenance.is_human_reviewed)
        return {
            "cases": len(self._cases),
            "human_reviewed": reviewed,
            "machine_extracted": sum(1 for c in self.all()
                                     if c.provenance.is_machine_extracted),
            "assets": sorted({c.asset for c in self.all() if c.asset}),
            "all_educational_examples": True,
            "usable_as_sample": False,
        }
