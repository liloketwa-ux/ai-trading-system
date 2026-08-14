"""Hypothesis evaluation and verdicts.

Produces a full research record for one hypothesis: raw and cost-adjusted
outcomes, comparison against every baseline, effect sizes with bootstrapped
intervals, a regime breakdown, and the trial count that contextualises it all.

The verdict vocabulary deliberately excludes "profitable strategy". Phase 5
asks whether information is present, not whether money can be made from it, and
conflating the two is how a weak association becomes a trading system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import numpy as np

from .baselines import BASELINES
from .costs import CostModel, PESSIMISTIC
from .hypotheses import Hypothesis
from .labels import Label, LabelDefinition
from .sampling import Event, SamplingPolicy
from .statistics import (
    BootstrapResult,
    EffectSize,
    benjamini_hochberg,
    bootstrap_difference,
    bootstrap_mean,
    cohens_d,
    hit_rate,
    permutation_test,
)

__all__ = ["Conclusion", "BaselineComparison", "RegimeBreakdown", "HypothesisResult",
           "evaluate_hypothesis", "conclude", "MIN_SAMPLE"]

#: Below this, no verdict beyond NO_EVIDENCE is defensible. Effect sizes on a
#: handful of overlapping events are noise with a decimal point.
MIN_SAMPLE = 30


class Conclusion(str, Enum):
    """Permitted Phase 5 verdicts. 'Profitable strategy' is not among them."""

    NO_EVIDENCE = "NO EVIDENCE"
    WEAK = "WEAK"
    PROMISING = "PROMISING"
    UNSTABLE = "UNSTABLE"
    ECONOMICALLY_UNATTRACTIVE = "ECONOMICALLY UNATTRACTIVE"
    OUT_OF_SAMPLE_FAILURE = "OUT-OF-SAMPLE FAILURE"
    ROBUST_CANDIDATE = "ROBUST CANDIDATE"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT SAMPLE"


@dataclass(frozen=True)
class BaselineComparison:
    """Incremental performance against one baseline."""

    baseline: str
    baseline_n: int
    baseline_mean: float
    difference: BootstrapResult
    p_value: float
    effect: EffectSize

    @property
    def beats_baseline(self) -> bool:
        """Only when the interval excludes zero on the favourable side."""
        return self.difference.excludes_zero and self.difference.estimate > 0


@dataclass(frozen=True)
class RegimeBreakdown:
    """Outcome statistics within one regime."""

    regime: str
    value: str
    n: int
    mean: float
    median: float
    hit_rate: float
    ci_lower: float
    ci_upper: float

    @property
    def reliable(self) -> bool:
        return self.n >= MIN_SAMPLE


@dataclass
class HypothesisResult:
    """The complete research record for one hypothesis."""

    hypothesis_id: str
    dataset_version: str
    feature_versions: dict[str, str]
    label_definition: dict[str, Any]
    sampling_policy: dict[str, Any]
    cost_model: dict[str, Any]
    n_events: int
    raw: BootstrapResult
    net: BootstrapResult
    raw_hit_rate: float
    net_hit_rate: float
    baselines: list[BaselineComparison]
    regimes: list[RegimeBreakdown]
    n_trials: int
    seed: int
    conclusion: Conclusion
    reasons: list[str] = field(default_factory=list)
    validation: dict[str, Any] | None = None
    holdout: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "hypothesis_id": self.hypothesis_id,
            "dataset_version": self.dataset_version,
            "feature_versions": self.feature_versions,
            "label_definition": self.label_definition,
            "sampling_policy": self.sampling_policy,
            "cost_model": self.cost_model,
            "n_events": self.n_events,
            "raw": {"estimate": self.raw.estimate, "ci": [self.raw.lower, self.raw.upper],
                    "n": self.raw.n},
            "net": {"estimate": self.net.estimate, "ci": [self.net.lower, self.net.upper],
                    "n": self.net.n},
            "raw_hit_rate": self.raw_hit_rate,
            "net_hit_rate": self.net_hit_rate,
            "baselines": [
                {"baseline": b.baseline, "n": b.baseline_n,
                 "difference": b.difference.estimate,
                 "ci": [b.difference.lower, b.difference.upper],
                 "p_value": b.p_value, "beats": b.beats_baseline}
                for b in self.baselines
            ],
            "regimes": [
                {"regime": r.regime, "value": r.value, "n": r.n, "mean": r.mean,
                 "median": r.median, "hit_rate": r.hit_rate,
                 "ci": [r.ci_lower, r.ci_upper], "reliable": r.reliable}
                for r in self.regimes
            ],
            "n_trials": self.n_trials,
            "seed": self.seed,
            "conclusion": self.conclusion.value,
            "reasons": self.reasons,
            "validation": self.validation,
            "holdout": self.holdout,
            "created_at": self.created_at.isoformat(),
        }

    def render(self) -> str:
        """Human-readable report."""
        lines = [
            f"Hypothesis        : {self.hypothesis_id}",
            f"Dataset version   : {self.dataset_version}",
            f"Feature versions  : {self.feature_versions}",
            f"Label             : {self.label_definition.get('key')}",
            f"Sampling          : min_spacing={self.sampling_policy.get('min_spacing_s')}s "
            f"dedup={self.sampling_policy.get('deduplicate_overlaps')}",
            f"Cost model        : {self.cost_model.get('name')} "
            f"({self.cost_model.get('round_trip_bps')}bp round trip)",
            f"Events            : {self.n_events}",
            f"Raw outcome       : {self.raw}",
            f"Cost-adjusted     : {self.net}",
            f"Hit rate          : raw {self.raw_hit_rate:.1%} / net {self.net_hit_rate:.1%}",
            "",
            "Baseline comparison (incremental over simpler signals):",
        ]
        for b in self.baselines:
            verdict = "BEATS" if b.beats_baseline else "no"
            lines.append(
                f"  {b.baseline:<22} n={b.baseline_n:<5} diff={b.difference.estimate:+.6f} "
                f"[{b.difference.lower:+.6f}, {b.difference.upper:+.6f}] "
                f"p={b.p_value:.3f}  {verdict}"
            )
        lines.append("")
        lines.append("Regime breakdown:")
        for r in self.regimes:
            flag = "" if r.reliable else "  (n too small)"
            lines.append(
                f"  {r.regime}={r.value:<12} n={r.n:<5} mean={r.mean:+.6f} "
                f"median={r.median:+.6f} hit={r.hit_rate:.1%}{flag}"
            )
        lines += [
            "",
            f"Trials in family  : {self.n_trials}",
            f"Seed              : {self.seed}",
            f"CONCLUSION        : {self.conclusion.value}",
        ]
        lines += [f"  - {reason}" for reason in self.reasons]
        return "\n".join(lines)


def conclude(
    n_events: int,
    raw: BootstrapResult,
    net: BootstrapResult,
    baselines: list[BaselineComparison],
    regimes: list[RegimeBreakdown],
    *,
    validation_passed: bool | None = None,
    holdout_passed: bool | None = None,
) -> tuple[Conclusion, list[str]]:
    """Derive a verdict deterministically from the evidence.

    Ordered so the most disqualifying condition wins. In particular a result
    that survives gross but dies after costs is ECONOMICALLY UNATTRACTIVE, not
    WEAK -- the distinction matters because the first is a dead end and the
    second might be worth more data.
    """
    reasons: list[str] = []

    if n_events < MIN_SAMPLE:
        return Conclusion.INSUFFICIENT_SAMPLE, [
            f"only {n_events} independent events; {MIN_SAMPLE} is the minimum for any verdict"
        ]

    if not raw.excludes_zero:
        reasons.append(
            f"raw outcome interval includes zero ({raw.lower:+.6f}, {raw.upper:+.6f})"
        )
        return Conclusion.NO_EVIDENCE, reasons

    beaten = [b for b in baselines if b.beats_baseline]
    if not beaten:
        reasons.append(
            "raw outcome is non-zero but beats none of the "
            f"{len(baselines)} baselines -- no incremental information"
        )
        return Conclusion.NO_EVIDENCE, reasons
    reasons.append(f"beats {len(beaten)}/{len(baselines)} baselines: "
                   f"{', '.join(b.baseline for b in beaten)}")

    if not net.excludes_zero:
        reasons.append(
            f"survives gross but not after costs (net {net.estimate:+.6f}, "
            f"CI [{net.lower:+.6f}, {net.upper:+.6f}])"
        )
        return Conclusion.ECONOMICALLY_UNATTRACTIVE, reasons

    reliable = [r for r in regimes if r.reliable]
    if reliable:
        positive = sum(1 for r in reliable if r.mean > 0)
        if positive and positive != len(reliable):
            reasons.append(
                f"sign flips across regimes: positive in {positive}/{len(reliable)} "
                "reliable regimes"
            )
            return Conclusion.UNSTABLE, reasons

    if holdout_passed is False:
        reasons.append("failed the locked holdout")
        return Conclusion.OUT_OF_SAMPLE_FAILURE, reasons

    if validation_passed is False:
        reasons.append("failed out-of-sample validation")
        return Conclusion.OUT_OF_SAMPLE_FAILURE, reasons

    if holdout_passed:
        reasons.append("survived the locked holdout")
        return Conclusion.ROBUST_CANDIDATE, reasons

    if validation_passed:
        reasons.append("survived validation; holdout not yet spent")
        return Conclusion.PROMISING, reasons

    reasons.append("development-set only; no out-of-sample evidence yet")
    return Conclusion.WEAK, reasons


def evaluate_hypothesis(
    hypothesis: Hypothesis,
    events: list[Event],
    labels: dict[datetime, Label],
    *,
    label_definition: LabelDefinition,
    sampling_policy: SamplingPolicy,
    cost_model: CostModel = PESSIMISTIC,
    n_trials: int = 1,
    seed: int = 0,
    feature_versions: dict[str, str] | None = None,
    regime_keys: tuple[str, ...] = ("session", "htf_bias"),
    validation_passed: bool | None = None,
    holdout: dict[str, Any] | None = None,
    resamples: int = 2_000,
) -> HypothesisResult:
    """Evaluate one hypothesis against its baselines and regimes."""
    matched = [e for e in events if e.decision_time in labels]
    outcomes = [labels[e.decision_time].value for e in matched]
    usable = [(e, v) for e, v in zip(matched, outcomes) if v is not None]

    treatment_events = [e for e, _ in usable]
    gross = [v for _, v in usable]
    cost = cost_model.round_trip_bps / 10_000.0
    net_values = [v - cost for v in gross]

    raw = bootstrap_mean(gross, seed=seed, resamples=resamples)
    net = bootstrap_mean(net_values, seed=seed, resamples=resamples)

    comparisons: list[BaselineComparison] = []
    p_values: list[float] = []
    for name, baseline in BASELINES.items():
        selected = (
            baseline.select(events, seed, len(treatment_events))
            if name == "hold_matched_random"
            else baseline.select(events, seed)
        )
        baseline_outcomes = [
            labels[e.decision_time].value for e in selected
            if e.decision_time in labels and labels[e.decision_time].value is not None
        ]
        if not baseline_outcomes:
            continue

        difference = bootstrap_difference(gross, baseline_outcomes, seed=seed,
                                          resamples=resamples)
        p_value = permutation_test(gross, baseline_outcomes, seed=seed,
                                   resamples=min(resamples, 2_000))
        p_values.append(p_value)
        baseline_mean = float(np.mean(baseline_outcomes))
        comparisons.append(BaselineComparison(
            baseline=name,
            baseline_n=len(baseline_outcomes),
            baseline_mean=baseline_mean,
            difference=difference,
            p_value=p_value,
            effect=EffectSize(
                metric="mean_forward_outcome",
                treatment=raw.estimate,
                baseline=baseline_mean,
                absolute=raw.estimate - baseline_mean,
                relative=((raw.estimate - baseline_mean) / abs(baseline_mean)
                          if baseline_mean else None),
                standardized=cohens_d(gross, baseline_outcomes),
            ),
        ))

    # Family-wise control across the baseline comparisons.
    if p_values:
        survived = benjamini_hochberg(p_values, alpha=0.05)
        comparisons = [
            BaselineComparison(
                c.baseline, c.baseline_n, c.baseline_mean, c.difference,
                c.p_value if keep else max(c.p_value, 0.999), c.effect,
            )
            for c, keep in zip(comparisons, survived)
        ]

    regimes: list[RegimeBreakdown] = []
    for key in regime_keys:
        buckets: dict[str, list[float]] = {}
        for event, value in usable:
            bucket = str(event.features.get(key, "unknown"))
            buckets.setdefault(bucket, []).append(value)
        for bucket, values in sorted(buckets.items()):
            interval = bootstrap_mean(values, seed=seed, resamples=min(resamples, 1_000))
            regimes.append(RegimeBreakdown(
                regime=key, value=bucket, n=len(values),
                mean=float(np.mean(values)), median=float(np.median(values)),
                hit_rate=hit_rate(values), ci_lower=interval.lower,
                ci_upper=interval.upper,
            ))

    conclusion, reasons = conclude(
        len(usable), raw, net, comparisons, regimes,
        validation_passed=validation_passed,
        holdout_passed=(holdout or {}).get("passed"),
    )
    if n_trials > 1:
        reasons.append(
            f"{n_trials} hypotheses in the family; treat any single result as exploratory"
        )

    return HypothesisResult(
        hypothesis_id=hypothesis.hypothesis_id,
        dataset_version=hypothesis.dataset_version,
        feature_versions=feature_versions or {},
        label_definition=label_definition.to_dict(),
        sampling_policy=sampling_policy.to_dict(),
        cost_model=cost_model.to_dict(),
        n_events=len(usable),
        raw=raw,
        net=net,
        raw_hit_rate=hit_rate(gross),
        net_hit_rate=hit_rate(net_values),
        baselines=comparisons,
        regimes=regimes,
        n_trials=n_trials,
        seed=seed,
        conclusion=conclusion,
        reasons=reasons,
        validation={"passed": validation_passed} if validation_passed is not None else None,
        holdout=holdout,
    )
