"""Rolling walk-forward windows with purging and embargo.

Two contaminations that plain rolling windows do not prevent.

**Label overlap.** A training observation at time ``t`` with a 4-hour forward
label is still resolving at ``t + 4h``. If the test period begins at ``t + 1h``,
that training label was computed from bars inside the test period -- the model
was trained on the answer. Purging removes any training observation whose label
window reaches into the test period.

**Serial correlation across the boundary.** Even after purging, the last
training observation and the first test observation sit seconds apart in a
market with strong autocorrelation, so the test set is not independent. The
embargo inserts an explicit gap and is recorded on every window.

The eligibility rule is a single inequality::

    train_time + label_horizon + embargo <= test_start

Anything failing it is dropped, and the counts are reported so the cost of the
policy is visible rather than silent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Sequence

from ..storage.records import utc

__all__ = ["Window", "PurgeReport", "WalkForwardConfig", "generate_windows",
           "purge_and_embargo", "is_contaminated"]


@dataclass(frozen=True)
class Window:
    """One walk-forward fold with full lineage."""

    index: int
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    test_start: datetime
    test_end: datetime
    label_horizon: timedelta
    embargo: timedelta
    dataset_version: str = ""
    hypothesis_version: str = ""
    feature_versions: dict[str, str] = field(default_factory=dict)
    execution_model_version: str = ""
    cost_model_version: str = ""

    def __post_init__(self) -> None:
        for name in ("train_start", "train_end", "validation_start",
                     "validation_end", "test_start", "test_end"):
            object.__setattr__(self, name, utc(getattr(self, name)))
        if not self.train_start < self.train_end:
            raise ValueError(f"window {self.index}: empty train range")
        if self.validation_end < self.validation_start:
            raise ValueError(f"window {self.index}: inverted validation range")
        if not self.test_start < self.test_end:
            raise ValueError(f"window {self.index}: empty test range")
        # A zero-length validation range is legitimate: it means a train -> test
        # design with no separate selection split. In that case purge and embargo
        # carry the ENTIRE separation burden, since nothing else stands between
        # the last training bar and the first test bar.
        if self.validation_start < self.train_end:
            raise ValueError(f"window {self.index}: validation overlaps train")
        if self.test_start < self.validation_end:
            raise ValueError(f"window {self.index}: test overlaps validation")

    @property
    def purge_cutoff(self) -> datetime:
        """Latest training time whose label cannot reach the test period."""
        return self.test_start - self.label_horizon - self.embargo

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "train": [self.train_start.isoformat(), self.train_end.isoformat()],
            "validation": [self.validation_start.isoformat(), self.validation_end.isoformat()],
            "test": [self.test_start.isoformat(), self.test_end.isoformat()],
            "label_horizon_s": self.label_horizon.total_seconds(),
            "embargo_s": self.embargo.total_seconds(),
            "purge_cutoff": self.purge_cutoff.isoformat(),
            "dataset_version": self.dataset_version,
            "hypothesis_version": self.hypothesis_version,
            "feature_versions": self.feature_versions,
            "execution_model_version": self.execution_model_version,
            "cost_model_version": self.cost_model_version,
        }


@dataclass(frozen=True)
class PurgeReport:
    """What the purge and embargo removed, so the cost is visible."""

    submitted: int
    kept: int
    purged_label_overlap: int
    purged_embargo: int
    purged_out_of_range: int

    @property
    def removed(self) -> int:
        return self.submitted - self.kept

    @property
    def removed_fraction(self) -> float:
        return self.removed / self.submitted if self.submitted else 0.0


@dataclass(frozen=True)
class WalkForwardConfig:
    """Rolling window geometry."""

    train: timedelta
    validation: timedelta
    test: timedelta
    step: timedelta
    label_horizon: timedelta
    embargo: timedelta = timedelta(0)
    protocol_version: str = "1"

    def __post_init__(self) -> None:
        for name in ("train", "test", "step"):
            if getattr(self, name) <= timedelta(0):
                raise ValueError(f"{name} must be positive")
        if self.validation < timedelta(0):
            raise ValueError("validation cannot be negative")
        if self.validation == timedelta(0) and self.embargo == timedelta(0) \
                and self.label_horizon == timedelta(0):
            raise ValueError(
                "a train -> test design with no validation split needs a non-zero "
                "embargo or label horizon; otherwise nothing separates the last "
                "training bar from the first test bar"
            )
        if self.label_horizon < timedelta(0):
            raise ValueError("label_horizon cannot be negative")
        if self.embargo < timedelta(0):
            raise ValueError("embargo cannot be negative")

    def to_dict(self) -> dict:
        return {
            "train_s": self.train.total_seconds(),
            "validation_s": self.validation.total_seconds(),
            "test_s": self.test.total_seconds(),
            "step_s": self.step.total_seconds(),
            "label_horizon_s": self.label_horizon.total_seconds(),
            "embargo_s": self.embargo.total_seconds(),
            "protocol_version": self.protocol_version,
        }


def generate_windows(
    config: WalkForwardConfig, start: datetime, end: datetime, **lineage
) -> list[Window]:
    """Roll TRAIN → VALIDATE → TEST across a period.

    Windows advance by ``step``; a fold is emitted only when its full test
    period fits inside the data. A truncated final fold would be evaluated on
    less data than every other and quietly skew the aggregate.
    """
    start, end = utc(start), utc(end)
    windows: list[Window] = []
    index = 0
    train_start = start

    while True:
        train_end = train_start + config.train
        validation_start = train_end
        validation_end = validation_start + config.validation
        test_start = validation_end
        test_end = test_start + config.test
        if test_end > end:
            break
        windows.append(Window(
            index=index, train_start=train_start, train_end=train_end,
            validation_start=validation_start, validation_end=validation_end,
            test_start=test_start, test_end=test_end,
            label_horizon=config.label_horizon, embargo=config.embargo,
            **lineage,
        ))
        index += 1
        train_start += config.step

    return windows


def purge_and_embargo(
    train_times: Sequence[datetime], window: Window
) -> tuple[list[datetime], PurgeReport]:
    """Filter training observations that could contaminate the test period.

    Returns the surviving times and a report of what was removed and why.
    """
    kept: list[datetime] = []
    label_overlap = embargo_hits = out_of_range = 0

    for raw in train_times:
        moment = utc(raw)
        if moment < window.train_start or moment >= window.train_end:
            out_of_range += 1
            continue
        if moment + window.label_horizon > window.test_start:
            label_overlap += 1
            continue
        if window.embargo > timedelta(0) and moment > window.purge_cutoff:
            embargo_hits += 1
            continue
        kept.append(moment)

    return kept, PurgeReport(
        submitted=len(train_times), kept=len(kept),
        purged_label_overlap=label_overlap, purged_embargo=embargo_hits,
        purged_out_of_range=out_of_range,
    )


def is_contaminated(train_time: datetime, window: Window) -> bool:
    """Whether a training observation would leak into the test period."""
    moment = utc(train_time)
    return (
        moment + window.label_horizon > window.test_start
        or (window.embargo > timedelta(0) and moment > window.purge_cutoff)
    )
