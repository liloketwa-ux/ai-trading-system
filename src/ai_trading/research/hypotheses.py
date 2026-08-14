"""Hypothesis registry.

A hypothesis is a permanent record of a question that was asked, whether or not
the answer was interesting. Registering before running is what makes the trial
count meaningful: a family whose members are only recorded when they look good
is not a family, it is a highlight reel.

Definitions are immutable. Editing a hypothesis after seeing its result and
re-running it is the single most effective way to manufacture a discovery.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..storage.dataset import code_commit
from ..storage.records import utc

__all__ = ["Hypothesis", "HypothesisRegistry", "STANDARD_FAMILY"]


@dataclass(frozen=True)
class Hypothesis:
    """One registered research question."""

    hypothesis_id: str
    description: str
    feature_set: tuple[str, ...]
    feature_definitions: dict[str, str]
    label_key: str
    horizon_seconds: float
    created_at: datetime
    research_version: str
    dataset_version: str
    code_commit: str
    expected_direction: str | None = None   # "positive" | "negative" | None
    family: str = "ICT"
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.hypothesis_id:
            raise ValueError("hypothesis_id must not be empty")
        if not self.feature_set:
            raise ValueError(f"{self.hypothesis_id}: feature_set must not be empty")
        if self.expected_direction not in (None, "positive", "negative"):
            raise ValueError(f"{self.hypothesis_id}: bad expected_direction")
        object.__setattr__(self, "created_at", utc(self.created_at))

    @property
    def checksum(self) -> str:
        payload = json.dumps(
            {
                "id": self.hypothesis_id,
                "features": sorted(self.feature_set),
                "definitions": self.feature_definitions,
                "label": self.label_key,
                "horizon": self.horizon_seconds,
                "expected_direction": self.expected_direction,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "hypothesis_id": self.hypothesis_id,
            "description": self.description,
            "feature_set": list(self.feature_set),
            "feature_definitions": self.feature_definitions,
            "label_key": self.label_key,
            "horizon_seconds": self.horizon_seconds,
            "created_at": self.created_at.isoformat(),
            "research_version": self.research_version,
            "dataset_version": self.dataset_version,
            "code_commit": self.code_commit,
            "expected_direction": self.expected_direction,
            "family": self.family,
            "notes": self.notes,
            "checksum": self.checksum,
        }


class HypothesisRegistry:
    """Append-only registry of research questions."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self._hypotheses: dict[str, Hypothesis] = {}
        if self.path and self.path.exists():
            self._load()

    def _load(self) -> None:
        for line in self.path.read_text().splitlines():
            if line.strip():
                payload = json.loads(line)
                self._hypotheses[payload["hypothesis_id"]] = _from_dict(payload)

    def _persist(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w") as handle:
            for hypothesis in self._hypotheses.values():
                handle.write(json.dumps(hypothesis.to_dict()) + "\n")

    def register(
        self,
        hypothesis_id: str,
        description: str,
        feature_set: tuple[str, ...],
        *,
        label_key: str,
        horizon_seconds: float,
        research_version: str,
        dataset_version: str,
        feature_definitions: dict[str, str] | None = None,
        expected_direction: str | None = None,
        family: str = "ICT",
        notes: str = "",
    ) -> Hypothesis:
        """Register a hypothesis. Re-registering with different content raises."""
        hypothesis = Hypothesis(
            hypothesis_id=hypothesis_id,
            description=description,
            feature_set=tuple(feature_set),
            feature_definitions=dict(feature_definitions or {}),
            label_key=label_key,
            horizon_seconds=horizon_seconds,
            created_at=datetime.now(timezone.utc),
            research_version=research_version,
            dataset_version=dataset_version,
            code_commit=code_commit(),
            expected_direction=expected_direction,
            family=family,
            notes=notes,
        )
        existing = self._hypotheses.get(hypothesis_id)
        if existing is not None and existing.checksum != hypothesis.checksum:
            raise ValueError(
                f"{hypothesis_id} is already registered with a different definition "
                f"(checksum {existing.checksum} vs {hypothesis.checksum}). "
                "Hypotheses are immutable -- register a new id instead of editing "
                "one whose result you have already seen."
            )
        if existing is None:
            self._hypotheses[hypothesis_id] = hypothesis
            self._persist()
            return hypothesis
        return existing

    def get(self, hypothesis_id: str) -> Hypothesis | None:
        return self._hypotheses.get(hypothesis_id)

    def require(self, hypothesis_id: str) -> Hypothesis:
        hypothesis = self._hypotheses.get(hypothesis_id)
        if hypothesis is None:
            raise KeyError(f"unregistered hypothesis {hypothesis_id!r}")
        return hypothesis

    def all(self) -> list[Hypothesis]:
        return sorted(self._hypotheses.values(), key=lambda h: h.hypothesis_id)

    def family(self, name: str) -> list[Hypothesis]:
        return [h for h in self.all() if h.family == name]

    def family_size(self, name: str = "ICT") -> int:
        """Size of the hypothesis family, for multiple-testing correction."""
        return len(self.family(name))

    def __len__(self) -> int:
        return len(self._hypotheses)


def _from_dict(payload: dict) -> Hypothesis:
    return Hypothesis(
        hypothesis_id=payload["hypothesis_id"],
        description=payload["description"],
        feature_set=tuple(payload["feature_set"]),
        feature_definitions=payload.get("feature_definitions", {}),
        label_key=payload["label_key"],
        horizon_seconds=payload["horizon_seconds"],
        created_at=datetime.fromisoformat(payload["created_at"]),
        research_version=payload["research_version"],
        dataset_version=payload["dataset_version"],
        code_commit=payload["code_commit"],
        expected_direction=payload.get("expected_direction"),
        family=payload.get("family", "ICT"),
        notes=payload.get("notes", ""),
    )


#: The pre-declared family. Declaring it up front is what makes the trial count
#: honest -- the denominator is fixed before any result is seen.
STANDARD_FAMILY = [
    ("ICT-001", "Liquidity sweep + displacement", ("liquidity_sweep", "displacement_atr")),
    ("ICT-002", "Liquidity sweep + FVG", ("liquidity_sweep", "fvg")),
    ("ICT-003", "Liquidity sweep + displacement + FVG",
     ("liquidity_sweep", "displacement_atr", "fvg")),
    ("ICT-004", "Liquidity sweep + displacement + FVG + MSS",
     ("liquidity_sweep", "displacement_atr", "fvg", "mss")),
    ("ICT-005", "ICT-004 + HTF bias",
     ("liquidity_sweep", "displacement_atr", "fvg", "mss", "htf_bias")),
    ("ICT-006", "ICT-004 + New York session",
     ("liquidity_sweep", "displacement_atr", "fvg", "mss", "session")),
]
