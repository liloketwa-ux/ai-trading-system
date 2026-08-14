"""Experiment registry.

An experiment that cannot be re-run is an anecdote. Every run records the exact
inputs needed to reproduce it -- dataset version, code commit, seed, parameters,
execution assumptions -- and the registry counts trials automatically, because
the trial count is what a deflated Sharpe or a reality check needs and nobody
remembers it honestly after the fact.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from ..storage.dataset import code_commit
from ..storage.records import utc

__all__ = ["ExperimentStatus", "Experiment", "ExperimentRegistry"]


class ExperimentStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


@dataclass(frozen=True)
class Experiment:
    """One reproducible research run."""

    experiment_id: str
    strategy_version: str
    feature_version: str
    dataset_version: str
    parameters: dict[str, Any]
    seed: int
    code_commit: str
    created_at: datetime
    training_period: tuple[datetime, datetime] | None = None
    validation_period: tuple[datetime, datetime] | None = None
    holdout_period: tuple[datetime, datetime] | None = None
    execution_assumptions: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    status: ExperimentStatus = ExperimentStatus.CREATED
    notes: str = ""
    touched_holdout: bool = False

    def to_dict(self) -> dict:
        def window(w):
            return [w[0].isoformat(), w[1].isoformat()] if w else None

        return {
            "experiment_id": self.experiment_id,
            "strategy_version": self.strategy_version,
            "feature_version": self.feature_version,
            "dataset_version": self.dataset_version,
            "parameters": self.parameters,
            "seed": self.seed,
            "code_commit": self.code_commit,
            "created_at": self.created_at.isoformat(),
            "training_period": window(self.training_period),
            "validation_period": window(self.validation_period),
            "holdout_period": window(self.holdout_period),
            "execution_assumptions": self.execution_assumptions,
            "metrics": self.metrics,
            "status": self.status.value,
            "notes": self.notes,
            "touched_holdout": self.touched_holdout,
        }

    @property
    def reproduction_key(self) -> tuple:
        """Everything that must match for a re-run to be the same experiment."""
        return (
            self.strategy_version,
            self.feature_version,
            self.dataset_version,
            json.dumps(self.parameters, sort_keys=True, default=str),
            self.seed,
            self.code_commit,
        )


class ExperimentRegistry:
    """Persistent, append-oriented registry of research runs."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._experiments: dict[str, Experiment] = {}
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        for line in self.path.read_text().splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            self._experiments[payload["experiment_id"]] = _from_dict(payload)

    def _persist(self) -> None:
        with self.path.open("w") as handle:
            for experiment in self._experiments.values():
                handle.write(json.dumps(experiment.to_dict(), default=str) + "\n")

    # -- lifecycle ---------------------------------------------------------

    def create(
        self,
        *,
        strategy_version: str,
        feature_version: str,
        dataset_version: str,
        parameters: dict[str, Any],
        seed: int,
        execution_assumptions: dict[str, Any] | None = None,
        training_period=None,
        validation_period=None,
        notes: str = "",
    ) -> Experiment:
        experiment = Experiment(
            experiment_id=uuid.uuid4().hex[:16],
            strategy_version=strategy_version,
            feature_version=feature_version,
            dataset_version=dataset_version,
            parameters=dict(parameters),
            seed=seed,
            code_commit=code_commit(),
            created_at=datetime.now(timezone.utc),
            training_period=training_period,
            validation_period=validation_period,
            execution_assumptions=dict(execution_assumptions or {}),
            notes=notes,
        )
        self._experiments[experiment.experiment_id] = experiment
        self._persist()
        return experiment

    def complete(
        self, experiment_id: str, metrics: dict[str, float], *, notes: str = ""
    ) -> Experiment:
        return self._update(
            experiment_id, status=ExperimentStatus.COMPLETED, metrics=dict(metrics),
            notes=notes or self._experiments[experiment_id].notes,
        )

    def fail(self, experiment_id: str, reason: str) -> Experiment:
        return self._update(experiment_id, status=ExperimentStatus.FAILED, notes=reason)

    def mark_holdout(self, experiment_id: str, period) -> Experiment:
        return self._update(experiment_id, touched_holdout=True, holdout_period=period)

    def _update(self, experiment_id: str, **changes) -> Experiment:
        current = self._experiments.get(experiment_id)
        if current is None:
            raise KeyError(f"unknown experiment {experiment_id}")
        updated = replace(current, **changes)
        self._experiments[experiment_id] = updated
        self._persist()
        return updated

    # -- queries -----------------------------------------------------------

    def get(self, experiment_id: str) -> Experiment | None:
        return self._experiments.get(experiment_id)

    def all(self) -> list[Experiment]:
        return list(self._experiments.values())

    def trial_count(
        self, *, strategy_version: str | None = None, completed_only: bool = True
    ) -> int:
        """Trials run, for multiple-testing correction.

        Tuning over fifty configurations makes the best one look excellent by
        construction; a deflated Sharpe needs this number, and counting it
        automatically is the only way it stays honest.
        """
        return sum(
            1
            for e in self._experiments.values()
            if (strategy_version is None or e.strategy_version == strategy_version)
            and (not completed_only or e.status is ExperimentStatus.COMPLETED)
        )

    def holdout_evaluations(self) -> list[Experiment]:
        return [e for e in self._experiments.values() if e.touched_holdout]

    def find_duplicate(self, experiment: Experiment) -> Experiment | None:
        """An earlier run with an identical reproduction key, if any."""
        for other in self._experiments.values():
            if other.experiment_id != experiment.experiment_id and (
                other.reproduction_key == experiment.reproduction_key
            ):
                return other
        return None


def _from_dict(payload: dict) -> Experiment:
    def window(w):
        return (datetime.fromisoformat(w[0]), datetime.fromisoformat(w[1])) if w else None

    return Experiment(
        experiment_id=payload["experiment_id"],
        strategy_version=payload["strategy_version"],
        feature_version=payload["feature_version"],
        dataset_version=payload["dataset_version"],
        parameters=payload["parameters"],
        seed=payload["seed"],
        code_commit=payload["code_commit"],
        created_at=utc(datetime.fromisoformat(payload["created_at"])),
        training_period=window(payload.get("training_period")),
        validation_period=window(payload.get("validation_period")),
        holdout_period=window(payload.get("holdout_period")),
        execution_assumptions=payload.get("execution_assumptions", {}),
        metrics=payload.get("metrics", {}),
        status=ExperimentStatus(payload.get("status", "created")),
        notes=payload.get("notes", ""),
        touched_holdout=payload.get("touched_holdout", False),
    )
