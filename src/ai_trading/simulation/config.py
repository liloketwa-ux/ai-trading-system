"""Backtest configuration and reproducibility identity.

A backtest that cannot be re-run is an anecdote. The config carries everything
needed to reproduce a result, and its ``run_id`` is a hash of exactly those
fields -- so two runs with the same id are the same experiment, and any
difference in assumptions produces a different id rather than a silent
divergence.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..storage.dataset import code_commit
from .execution import ExecutionConfig

__all__ = ["BacktestConfig"]


@dataclass(frozen=True)
class BacktestConfig:
    """Complete, reproducible backtest specification."""

    dataset_version: str
    strategy_version: str
    hypothesis_id: str
    feature_versions: dict[str, str] = field(default_factory=dict)
    parameters: dict = field(default_factory=dict)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    cost_model_version: str = "1"
    starting_balance: float = 100_000.0
    random_seed: int = 0
    code_commit: str = field(default_factory=code_commit)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    notes: str = ""

    def __post_init__(self) -> None:
        if self.starting_balance <= 0:
            raise ValueError("starting_balance must be > 0")

    @property
    def run_id(self) -> str:
        """Deterministic identity over everything that affects the result.

        ``created_at`` is excluded: re-running the same experiment tomorrow is
        the same experiment.
        """
        payload = json.dumps(
            {
                "dataset_version": self.dataset_version,
                "strategy_version": self.strategy_version,
                "hypothesis_id": self.hypothesis_id,
                "feature_versions": self.feature_versions,
                "parameters": self.parameters,
                "execution": self.execution.to_dict(),
                "cost_model_version": self.cost_model_version,
                "starting_balance": self.starting_balance,
                "random_seed": self.random_seed,
                "code_commit": self.code_commit,
            },
            sort_keys=True, default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "dataset_version": self.dataset_version,
            "strategy_version": self.strategy_version,
            "hypothesis_id": self.hypothesis_id,
            "feature_versions": self.feature_versions,
            "parameters": self.parameters,
            "execution": self.execution.to_dict(),
            "cost_model_version": self.cost_model_version,
            "starting_balance": self.starting_balance,
            "random_seed": self.random_seed,
            "code_commit": self.code_commit,
            "created_at": self.created_at.isoformat(),
            "notes": self.notes,
        }
