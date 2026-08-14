"""Research governance: splits, locked holdout, experiment registry (Phase 3)."""

from . import baselines, costs, statistics
from .costs import OPTIMISTIC, PESSIMISTIC, REALISTIC, CostModel
from .evaluate import Conclusion, HypothesisResult, evaluate_hypothesis
from .experiments import Experiment, ExperimentRegistry, ExperimentStatus
from .hypotheses import STANDARD_FAMILY, Hypothesis, HypothesisRegistry
from .labels import FORWARD_RETURNS, R_LABELS, Label, LabelDefinition, LabelKind
from .sampling import Event, SamplingPolicy, apply_sampling
from .splits import (
    HoldoutLedger,
    HoldoutViolation,
    Purpose,
    SplitDefinition,
    SplitRegistry,
)

__all__ = [
    "FORWARD_RETURNS",
    "OPTIMISTIC",
    "PESSIMISTIC",
    "REALISTIC",
    "R_LABELS",
    "STANDARD_FAMILY",
    "Conclusion",
    "CostModel",
    "Event",
    "Experiment",
    "Hypothesis",
    "HypothesisRegistry",
    "HypothesisResult",
    "Label",
    "LabelDefinition",
    "LabelKind",
    "SamplingPolicy",
    "apply_sampling",
    "baselines",
    "costs",
    "evaluate_hypothesis",
    "statistics",
    "ExperimentRegistry",
    "ExperimentStatus",
    "HoldoutLedger",
    "HoldoutViolation",
    "Purpose",
    "SplitDefinition",
    "SplitRegistry",
]
