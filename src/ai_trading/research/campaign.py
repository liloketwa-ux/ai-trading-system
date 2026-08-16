"""Pre-declared research campaigns, and the gate in front of ICT.

The purpose of the first campaign on a new dataset is to test the *pipeline*,
not to find a strategy. Those two goals conflict: searching for the best result
on freshly ingested data is how a data bug becomes a discovery. So a campaign
is declared before it runs -- fixed sample, features, labels, costs, execution
model and validation protocol -- and the declaration is hashed. Changing any of
it produces a different ``campaign_id``, which makes a quietly widened search
visible in the record instead of invisible in the results.

:class:`ICTGate` enforces the Phase 5 commitment. The pre-registered ICT
hypotheses may be evaluated only once a real dataset has passed the quality
gate, and their definitions may not be edited because real data produced a
disappointing answer. The gate checks the dataset's origin, not its name: a
synthetic dataset cannot open it however it is labelled.

A bad result is a valid result. Nothing here is designed to help a hypothesis
pass.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Sequence

from ..history.datasets import DataOrigin, ResearchDataset

__all__ = [
    "CampaignPurpose", "CampaignDeclaration", "CampaignStatus", "CampaignResult",
    "ICTGate", "ICTGateError", "BASELINE_SUITE",
]

#: The baselines every campaign runs before any hypothesis is allowed a verdict.
#: Order is fixed so results are comparable across campaigns.
BASELINE_SUITE = ("random", "hold_matched_random", "momentum", "mean_reversion")


class ICTGateError(RuntimeError):
    """ICT evaluation was attempted before its precondition was met."""


class CampaignPurpose(str, Enum):
    """What a campaign is for. Determines what may be concluded from it.

    ``PIPELINE_VALIDATION`` runs cannot produce strategy findings at all. Naming
    the purpose up front stops a plumbing test being reinterpreted as evidence
    after the fact, which is the most common way in-sample results escape.
    """

    PIPELINE_VALIDATION = "pipeline_validation"
    BASELINE_ESTABLISHMENT = "baseline_establishment"
    HYPOTHESIS_EVALUATION = "hypothesis_evaluation"

    @property
    def may_claim_edge(self) -> bool:
        return self is CampaignPurpose.HYPOTHESIS_EVALUATION


class CampaignStatus(str, Enum):
    DECLARED = "declared"
    RUNNING = "running"
    COMPLETE = "complete"
    ABANDONED = "abandoned"


@dataclass(frozen=True)
class CampaignDeclaration:
    """Everything fixed before the campaign runs.

    Frozen and hashed. The point is not ceremony: an honest multiple-testing
    correction needs to know how many things were tried, and that number is
    only trustworthy if it was written down first.
    """

    name: str
    purpose: CampaignPurpose
    dataset_id: str
    instrument: str
    contract: str
    timeframes: tuple[str, ...]
    features: tuple[str, ...]
    labels: tuple[str, ...]
    hypotheses: tuple[str, ...]
    baselines: tuple[str, ...] = BASELINE_SUITE
    cost_model: str = ""
    execution_model: str = ""
    validation_protocol: str = ""
    seed: int = 0
    declared_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))
    note: str = ""

    def __post_init__(self) -> None:
        if not self.cost_model or not self.execution_model:
            raise ValueError(
                f"campaign {self.name!r} must fix its cost and execution models "
                "before running; choosing them afterwards means choosing them to "
                "suit the result"
            )
        if not self.validation_protocol:
            raise ValueError(
                f"campaign {self.name!r} must fix its validation protocol before "
                "running"
            )
        if self.purpose.may_claim_edge and not self.hypotheses:
            raise ValueError(
                f"campaign {self.name!r} claims to evaluate hypotheses but declares "
                "none"
            )

    @property
    def campaign_id(self) -> str:
        """Content hash of the declaration. Any change makes a new campaign."""
        payload = {
            "name": self.name, "purpose": self.purpose.value,
            "dataset_id": self.dataset_id, "instrument": self.instrument,
            "contract": self.contract, "timeframes": list(self.timeframes),
            "features": sorted(self.features), "labels": sorted(self.labels),
            "hypotheses": sorted(self.hypotheses),
            "baselines": list(self.baselines), "cost_model": self.cost_model,
            "execution_model": self.execution_model,
            "validation_protocol": self.validation_protocol, "seed": self.seed,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
        return f"{self.name}-{digest}"

    @property
    def test_count(self) -> int:
        """Number of declared tests, for multiple-testing correction."""
        return max(1, len(self.hypotheses) * max(1, len(self.timeframes)))

    def to_dict(self) -> dict:
        return {
            "campaign_id": self.campaign_id, "name": self.name,
            "purpose": self.purpose.value, "dataset_id": self.dataset_id,
            "instrument": self.instrument, "contract": self.contract,
            "timeframes": list(self.timeframes), "features": list(self.features),
            "labels": list(self.labels), "hypotheses": list(self.hypotheses),
            "baselines": list(self.baselines), "cost_model": self.cost_model,
            "execution_model": self.execution_model,
            "validation_protocol": self.validation_protocol, "seed": self.seed,
            "test_count": self.test_count,
            "declared_at": self.declared_at.isoformat(), "note": self.note,
        }


@dataclass
class CampaignResult:
    """Outcome of a declared campaign, tied to the declaration that produced it."""

    declaration: CampaignDeclaration
    status: CampaignStatus = CampaignStatus.DECLARED
    baseline_results: dict[str, dict] = field(default_factory=dict)
    hypothesis_results: dict[str, dict] = field(default_factory=dict)
    pipeline_checks: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def campaign_id(self) -> str:
        return self.declaration.campaign_id

    @property
    def baselines_complete(self) -> bool:
        return all(name in self.baseline_results
                   for name in self.declaration.baselines)

    def may_report_edge(self) -> tuple[bool, str]:
        """Whether this campaign is entitled to claim an edge.

        Three independent conditions, all required. Baselines first: a
        hypothesis that has not been compared against random selection has not
        been compared against anything.
        """
        if not self.declaration.purpose.may_claim_edge:
            return (False, f"campaign purpose is {self.declaration.purpose.value}; "
                           "it cannot produce strategy findings")
        if not self.baselines_complete:
            missing = [n for n in self.declaration.baselines
                       if n not in self.baseline_results]
            return (False, f"baselines not run: {', '.join(missing)}")
        if self.status is not CampaignStatus.COMPLETE:
            return (False, f"campaign status is {self.status.value}")
        return (True, "declared hypotheses evaluated against completed baselines")

    def to_dict(self) -> dict:
        allowed, reason = self.may_report_edge()
        return {
            "campaign_id": self.campaign_id,
            "declaration": self.declaration.to_dict(),
            "status": self.status.value,
            "baselines_complete": self.baselines_complete,
            "baseline_results": self.baseline_results,
            "hypothesis_results": self.hypothesis_results,
            "pipeline_checks": self.pipeline_checks,
            "may_report_edge": allowed,
            "may_report_edge_reason": reason,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class ICTGate:
    """The precondition on evaluating the pre-registered ICT hypotheses.

    Phase 5 registered the hypotheses and deliberately stopped short of turning
    them into rules. The commitment made then was that they would be evaluated
    on real data, once, with their definitions unchanged. This gate is that
    commitment expressed as code rather than intent.
    """

    dataset: ResearchDataset | None = None
    reason_blocked: str = ""

    @property
    def is_open(self) -> bool:
        if self.dataset is None:
            return False
        if not self.dataset.quality_report.is_research_eligible:
            return False
        return self.dataset.origin is DataOrigin.REAL_MARKET

    def status(self) -> str:
        if self.dataset is None:
            return ("CLOSED: no research dataset has been admitted. "
                    + (self.reason_blocked or "No real market data has been ingested."))
        if self.dataset.origin is not DataOrigin.REAL_MARKET:
            return (f"CLOSED: dataset {self.dataset.dataset_id} has origin "
                    f"{self.dataset.origin.value}. ICT hypotheses are evaluated on real "
                    "market data or not at all.")
        if not self.dataset.quality_report.is_research_eligible:
            return (f"CLOSED: dataset {self.dataset.dataset_id} did not pass the "
                    f"quality gate ({self.dataset.quality_report.quality_status.value}).")
        return f"OPEN: dataset {self.dataset.dataset_id} is real and research-eligible."

    def require_open(self) -> ResearchDataset:
        """Return the admitting dataset, or refuse."""
        if not self.is_open:
            raise ICTGateError(
                "the pre-registered ICT hypotheses may not be evaluated yet. "
                + self.status()
                + " Evaluating them on synthetic or unvalidated data would spend a "
                "one-shot pre-registration on a result about nothing."
            )
        assert self.dataset is not None
        return self.dataset

    def verify_definitions_unchanged(
        self, registered: Sequence[str], current: Sequence[str]
    ) -> None:
        """Refuse an evaluation whose hypothesis set has drifted.

        Guards the other half of the Phase 5 commitment: definitions are not
        edited because real data produced a poor result. A changed set is not
        forbidden -- it is simply a *different*, un-pre-registered study, and it
        must be declared as one.
        """
        if sorted(registered) != sorted(current):
            added = sorted(set(current) - set(registered))
            removed = sorted(set(registered) - set(current))
            raise ICTGateError(
                "the hypothesis set differs from the Phase 5 pre-registration "
                f"(added: {added or 'none'}; removed: {removed or 'none'}). "
                "Pre-registered hypotheses are evaluated as written. A different set "
                "is a new study and must be declared as one, with its own "
                "multiple-testing budget."
            )
