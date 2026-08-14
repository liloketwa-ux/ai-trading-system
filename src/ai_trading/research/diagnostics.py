"""Sample diagnostics.

Sparse and fragmented results must be visible, not buried. Every warning below
corresponds to a way a result can look meaningful while resting on almost
nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Sequence

__all__ = ["Warning_", "SampleDiagnostics", "diagnose_sample", "MIN_EVENTS",
           "MAX_CONDITIONS", "MIN_REGIME_BUCKET"]

MIN_EVENTS = 30
MAX_CONDITIONS = 3
MIN_REGIME_BUCKET = 20


class Warning_(str, Enum):
    LOW_SAMPLE = "LOW_SAMPLE"
    EXCESSIVE_CONDITIONING = "EXCESSIVE_CONDITIONING"
    IMBALANCED_OUTCOME = "IMBALANCED_OUTCOME"
    OVERLAPPING_SAMPLES = "OVERLAPPING_SAMPLES"
    REGIME_FRAGMENTATION = "REGIME_FRAGMENTATION"
    REDUNDANT_CONDITION = "REDUNDANT_CONDITION"


@dataclass
class SampleDiagnostics:
    """What is wrong with a sample, stated plainly."""

    n_events: int
    n_conditions: int
    effective_conditions: int
    warnings: list[Warning_] = field(default_factory=list)
    detail: dict[str, str] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        """False when a verdict beyond NO EVIDENCE would be indefensible."""
        return Warning_.LOW_SAMPLE not in self.warnings

    def add(self, warning: Warning_, detail: str) -> None:
        if warning not in self.warnings:
            self.warnings.append(warning)
        self.detail[warning.value] = detail

    def render(self) -> str:
        if not self.warnings:
            return f"sample ok (n={self.n_events})"
        lines = [f"sample warnings (n={self.n_events}):"]
        lines += [f"  {w.value}: {self.detail.get(w.value, '')}" for w in self.warnings]
        return "\n".join(lines)


def diagnose_sample(
    events: Sequence,
    outcomes: Sequence[float] | None = None,
    *,
    n_conditions: int = 0,
    effective_conditions: int | None = None,
    redundant: Sequence[str] = (),
    regime_keys: Sequence[str] = (),
    min_spacing: timedelta | None = None,
    label_horizon: timedelta | None = None,
) -> SampleDiagnostics:
    """Assess a selected sample for the ways it can mislead."""
    effective = n_conditions if effective_conditions is None else effective_conditions
    diagnostics = SampleDiagnostics(len(events), n_conditions, effective)

    if len(events) < MIN_EVENTS:
        diagnostics.add(
            Warning_.LOW_SAMPLE,
            f"{len(events)} events, below the {MIN_EVENTS} needed for any verdict",
        )

    if effective > MAX_CONDITIONS:
        diagnostics.add(
            Warning_.EXCESSIVE_CONDITIONING,
            f"{effective} effective conditions; each one starves the sample further",
        )

    if redundant:
        diagnostics.add(
            Warning_.REDUNDANT_CONDITION,
            f"{len(redundant)} condition(s) do not change the sample: {', '.join(redundant)}",
        )

    if outcomes:
        positives = sum(1 for v in outcomes if v is not None and v > 0)
        total = sum(1 for v in outcomes if v is not None)
        if total:
            rate = positives / total
            if rate < 0.1 or rate > 0.9:
                diagnostics.add(
                    Warning_.IMBALANCED_OUTCOME,
                    f"{rate:.1%} positive outcomes; effect estimates are unstable at this skew",
                )

    if min_spacing is not None and label_horizon is not None and min_spacing < label_horizon:
        diagnostics.add(
            Warning_.OVERLAPPING_SAMPLES,
            f"spacing {min_spacing} is shorter than the {label_horizon} label horizon, "
            "so outcome windows overlap and observations are not independent",
        )

    for key in regime_keys:
        buckets: dict[str, int] = {}
        for event in events:
            features = getattr(event, "features", event)
            buckets[str(features.get(key, "unknown"))] = buckets.get(
                str(features.get(key, "unknown")), 0
            ) + 1
        thin = [b for b, n in buckets.items() if n < MIN_REGIME_BUCKET]
        if thin and len(buckets) > 1:
            diagnostics.add(
                Warning_.REGIME_FRAGMENTATION,
                f"{key}: {len(thin)}/{len(buckets)} buckets below {MIN_REGIME_BUCKET} events "
                f"({', '.join(sorted(thin)[:4])})",
            )

    return diagnostics
