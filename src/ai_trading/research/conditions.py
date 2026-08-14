"""Hypothesis conditions: thresholds, categories, covariates, and redundancy.

Phase 5 exposed two defects. Both are fixed here.

**Defect 1: presence is not a condition.** ICT-002 and ICT-003 selected
identical samples because ``displacement_atr`` is always present, so adding it
to a conjunction filtered nothing. "Feature is not null" is not a hypothesis --
it partitions nothing and inflates the apparent family size without adding a
degree of freedom. Continuous features must enter as an explicit threshold, an
interval, or a covariate.

**Defect 2: untracked threshold sweeps.** Trying ``>= 1.0``, ``>= 1.25``,
``>= 1.5`` and reporting the best is three trials presented as one. Every
threshold is registered as a separate child hypothesis, and the parent/child
structure lets multiple-testing correction operate on the true family.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

__all__ = [
    "ConditionType", "Condition", "boolean", "at_least", "at_most", "within",
    "categorical", "presence", "Conjunction", "RedundancyReport",
    "detect_redundant_conditions", "threshold_sweep", "Covariate",
    "CovariateSpec", "build_design_matrix",
]


class ConditionType(str, Enum):
    BOOLEAN = "boolean"
    THRESHOLD_GE = "threshold_ge"
    THRESHOLD_LE = "threshold_le"
    INTERVAL = "interval"
    CATEGORICAL = "categorical"
    PRESENCE = "presence"      # kept only so it can be detected and rejected

    @property
    def is_presence(self) -> bool:
        return self is ConditionType.PRESENCE


@dataclass(frozen=True)
class Condition:
    """One testable condition on a single feature."""

    feature: str
    kind: ConditionType
    value: Any = None
    lower: float | None = None
    upper: float | None = None

    def __post_init__(self) -> None:
        if not self.feature:
            raise ValueError("condition needs a feature name")
        if self.kind in (ConditionType.THRESHOLD_GE, ConditionType.THRESHOLD_LE):
            if self.value is None:
                raise ValueError(f"{self.feature}: threshold condition needs a value")
        if self.kind is ConditionType.INTERVAL:
            if self.lower is None or self.upper is None:
                raise ValueError(f"{self.feature}: interval needs lower and upper")
            if self.lower >= self.upper:
                raise ValueError(f"{self.feature}: interval lower must be < upper")

    def matches(self, features: dict[str, Any]) -> bool:
        observed = features.get(self.feature)
        if observed is None:
            return False
        if self.kind is ConditionType.PRESENCE:
            return True
        if self.kind is ConditionType.BOOLEAN:
            return bool(observed) is bool(self.value)
        if self.kind is ConditionType.CATEGORICAL:
            allowed = self.value if isinstance(self.value, (list, tuple, set)) else (self.value,)
            return observed in allowed
        try:
            numeric = float(observed)
        except (TypeError, ValueError):
            return False
        if self.kind is ConditionType.THRESHOLD_GE:
            return numeric >= float(self.value)
        if self.kind is ConditionType.THRESHOLD_LE:
            return numeric <= float(self.value)
        return float(self.lower) <= numeric <= float(self.upper)

    @property
    def label(self) -> str:
        if self.kind is ConditionType.BOOLEAN:
            return f"{self.feature}={'true' if self.value else 'false'}"
        if self.kind is ConditionType.THRESHOLD_GE:
            return f"{self.feature}>={self.value}"
        if self.kind is ConditionType.THRESHOLD_LE:
            return f"{self.feature}<={self.value}"
        if self.kind is ConditionType.INTERVAL:
            return f"{self.lower}<={self.feature}<={self.upper}"
        if self.kind is ConditionType.CATEGORICAL:
            return f"{self.feature} in {self.value}"
        return f"{self.feature} PRESENT"


def boolean(feature: str, value: bool = True) -> Condition:
    return Condition(feature, ConditionType.BOOLEAN, value=value)


def at_least(feature: str, threshold: float) -> Condition:
    return Condition(feature, ConditionType.THRESHOLD_GE, value=threshold)


def at_most(feature: str, threshold: float) -> Condition:
    return Condition(feature, ConditionType.THRESHOLD_LE, value=threshold)


def within(feature: str, lower: float, upper: float) -> Condition:
    return Condition(feature, ConditionType.INTERVAL, lower=lower, upper=upper)


def categorical(feature: str, values) -> Condition:
    return Condition(feature, ConditionType.CATEGORICAL, value=values)


def presence(feature: str) -> Condition:
    """Deliberately available so redundancy detection has something to catch."""
    return Condition(feature, ConditionType.PRESENCE)


@dataclass(frozen=True)
class Conjunction:
    """An AND of conditions -- the sample-selection rule for a hypothesis."""

    conditions: tuple[Condition, ...]

    def matches(self, features: dict[str, Any]) -> bool:
        return all(c.matches(features) for c in self.conditions)

    def select(self, events: Sequence) -> list:
        return [e for e in events if self.matches(getattr(e, "features", e))]

    @property
    def label(self) -> str:
        return " AND ".join(c.label for c in self.conditions)

    @property
    def arity(self) -> int:
        return len(self.conditions)


# -- redundancy ------------------------------------------------------------


@dataclass(frozen=True)
class RedundancyReport:
    """Which conditions failed to change the selected sample."""

    total: int
    selected: int
    redundant: tuple[str, ...]
    effective_conditions: tuple[str, ...]

    @property
    def has_redundancy(self) -> bool:
        return bool(self.redundant)

    @property
    def effective_arity(self) -> int:
        """Conditions that actually partition. The real degrees of freedom."""
        return len(self.effective_conditions)


def detect_redundant_conditions(
    conjunction: Conjunction, events: Sequence
) -> RedundancyReport:
    """Find conditions whose removal does not change the selected sample.

    Empirical rather than syntactic: a threshold below every observed value is
    just as redundant as a presence check, and only the data can say so.
    """
    selected = conjunction.select(events)
    redundant: list[str] = []
    effective: list[str] = []

    for condition in conjunction.conditions:
        others = Conjunction(tuple(c for c in conjunction.conditions if c is not condition))
        if len(others.select(events)) == len(selected):
            redundant.append(condition.label)
        else:
            effective.append(condition.label)

    return RedundancyReport(
        total=len(events), selected=len(selected),
        redundant=tuple(redundant), effective_conditions=tuple(effective),
    )


# -- threshold sweeps ------------------------------------------------------


def threshold_sweep(
    parent_id: str, base: Conjunction, feature: str, thresholds: Sequence[float],
    *, direction: str = "ge",
) -> list[tuple[str, Conjunction, float]]:
    """Expand a threshold sweep into child hypotheses, one per threshold.

    Each returned child is a separate trial. Sweeping silently and reporting the
    best threshold is three tests presented as one, and the resulting p-value
    means nothing.
    """
    if direction not in ("ge", "le"):
        raise ValueError("direction must be 'ge' or 'le'")
    if not thresholds:
        raise ValueError("threshold sweep needs at least one threshold")

    children = []
    for index, threshold in enumerate(thresholds):
        condition = at_least(feature, threshold) if direction == "ge" else at_most(feature, threshold)
        child_id = f"{parent_id}-{chr(ord('A') + index)}"
        children.append((child_id, Conjunction(base.conditions + (condition,)), threshold))
    return children


# -- covariates ------------------------------------------------------------


@dataclass(frozen=True)
class Covariate:
    """A continuous or categorical feature entering a model as a regressor.

    The alternative to a hard threshold: instead of asking "does the effect
    exist above 1.25?", ask "does the outcome vary with displacement at all?"
    -- which uses every observation rather than discarding most of them.
    """

    feature: str
    kind: str = "continuous"   # "continuous" | "categorical"
    standardize: bool = True

    def __post_init__(self) -> None:
        if self.kind not in ("continuous", "categorical"):
            raise ValueError(f"{self.feature}: kind must be continuous or categorical")


@dataclass(frozen=True)
class CovariateSpec:
    """A covariate-style hypothesis: ``outcome ~ x1 + x2 + ...``."""

    outcome: str
    covariates: tuple[Covariate, ...]
    notes: str = ""

    @property
    def formula(self) -> str:
        return f"{self.outcome} ~ " + " + ".join(c.feature for c in self.covariates)


def build_design_matrix(spec: CovariateSpec, events: Sequence):
    """Assemble a design matrix, one-hot encoding categoricals.

    Deliberately returns the matrix and nothing else. Phase 6 does not fit a
    model; this exists so the covariate path is available without a predictive
    model being smuggled in ahead of the evidence to justify one.
    """
    import numpy as np

    rows, names = [], []
    levels: dict[str, list[str]] = {}
    for covariate in spec.covariates:
        if covariate.kind == "categorical":
            observed = sorted({
                str(getattr(e, "features", e).get(covariate.feature))
                for e in events
                if getattr(e, "features", e).get(covariate.feature) is not None
            })
            # Drop the first level as the reference category.
            levels[covariate.feature] = observed[1:]
            names.extend(f"{covariate.feature}={v}" for v in observed[1:])
        else:
            names.append(covariate.feature)

    for event in events:
        features = getattr(event, "features", event)
        row = []
        for covariate in spec.covariates:
            value = features.get(covariate.feature)
            if covariate.kind == "categorical":
                row.extend(1.0 if str(value) == level else 0.0
                           for level in levels[covariate.feature])
            else:
                row.append(float(value) if value is not None else np.nan)
        rows.append(row)

    matrix = np.asarray(rows, dtype="float64") if rows else np.empty((0, len(names)))

    for index, covariate in enumerate(
        c for c in spec.covariates if c.kind == "continuous" and c.standardize
    ):
        column = names.index(covariate.feature)
        values = matrix[:, column]
        finite = values[np.isfinite(values)]
        if finite.size > 1 and finite.std() > 0:
            matrix[:, column] = (values - finite.mean()) / finite.std()

    return matrix, names
