"""Pre-registered Phase 7 candidates.

A candidate is frozen before the walk-forward loop runs. Retuning inside the
loop -- adjusting a threshold because window 3 disappointed -- turns
out-of-sample testing into in-sample fitting with extra steps, and the resulting
numbers look like validation while being nothing of the kind.

So a candidate is immutable and content-addressed. Any change to a threshold,
label, execution model or cost assumption produces a different fingerprint and
must be registered as a new candidate with its own research version.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from ..storage.dataset import code_commit
from ..storage.records import utc

__all__ = ["Candidate", "CandidateRegistry", "CandidateLockError"]


class CandidateLockError(RuntimeError):
    """An attempt to change a candidate after registration."""


@dataclass(frozen=True)
class Candidate:
    """A frozen research candidate entering robustness testing."""

    candidate_id: str
    hypothesis_id: str
    research_version: str
    feature_definitions: dict[str, str]
    thresholds: dict[str, float]
    label_key: str
    execution_model_version: str
    cost_model_version: str
    dataset_version: str
    backtest_version: str = "1"
    protocol_version: str = "1"
    random_seed: int = 0
    code_commit: str = field(default_factory=code_commit)
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    instrument_specific: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id required")
        if not self.label_key:
            raise ValueError("candidate needs a fixed label")
        object.__setattr__(self, "registered_at", utc(self.registered_at))

    @property
    def fingerprint(self) -> str:
        """Content hash over everything that defines the experiment."""
        payload = json.dumps(
            {
                "hypothesis_id": self.hypothesis_id,
                "research_version": self.research_version,
                "feature_definitions": self.feature_definitions,
                "thresholds": self.thresholds,
                "label_key": self.label_key,
                "execution_model_version": self.execution_model_version,
                "cost_model_version": self.cost_model_version,
                "dataset_version": self.dataset_version,
                "backtest_version": self.backtest_version,
                "random_seed": self.random_seed,
            },
            sort_keys=True, default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def retuned(self, **changes) -> "Candidate":
        """Produce a NEW candidate reflecting a parameter change.

        Never mutates. A retuned candidate must carry a new id, because the
        original's out-of-sample results do not transfer to it.
        """
        if "candidate_id" not in changes:
            raise CandidateLockError(
                "retuning requires a new candidate_id -- the original's "
                "out-of-sample results do not apply to a changed definition"
            )
        return replace(self, code_commit=code_commit(),
                       registered_at=datetime.now(timezone.utc), **changes)

    def lineage(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "fingerprint": self.fingerprint,
            "hypothesis_id": self.hypothesis_id,
            "research_version": self.research_version,
            "dataset_version": self.dataset_version,
            "feature_versions": self.feature_definitions,
            "label_version": self.label_key,
            "backtest_version": self.backtest_version,
            "execution_model_version": self.execution_model_version,
            "cost_model_version": self.cost_model_version,
            "random_seed": self.random_seed,
            "code_commit": self.code_commit,
            "protocol_version": self.protocol_version,
            "instrument_specific": self.instrument_specific,
        }


class CandidateRegistry:
    """Append-only registry. Fingerprint changes are rejected."""

    def __init__(self) -> None:
        self._candidates: dict[str, Candidate] = {}

    def register(self, candidate: Candidate) -> Candidate:
        existing = self._candidates.get(candidate.candidate_id)
        if existing is not None and existing.fingerprint != candidate.fingerprint:
            raise CandidateLockError(
                f"{candidate.candidate_id} is already registered with fingerprint "
                f"{existing.fingerprint}; the new definition hashes to "
                f"{candidate.fingerprint}. Register a new candidate id instead of "
                "silently retuning one already under test."
            )
        self._candidates[candidate.candidate_id] = candidate
        return candidate

    def get(self, candidate_id: str) -> Candidate | None:
        return self._candidates.get(candidate_id)

    def all(self) -> list[Candidate]:
        return sorted(self._candidates.values(), key=lambda c: c.candidate_id)

    def __len__(self) -> int:
        return len(self._candidates)
