"""Blind detection, then scoring against the revealed truth.

The detectors here are deliberately plain -- sign-conditioned forward returns,
deviation-conditioned forward returns, a per-regime breakdown. They are not
meant to be good strategies. They are the smallest instruments capable of
answering "is there a relationship of this shape", and if the machinery cannot
recover a known AR(1) coefficient with one of these, the problem is the
machinery, not the strategy.

Two verdicts are reported separately, and conflating them is the failure this
module exists to catch:

* **Statistical** -- is the effect distinguishable from noise?
* **Economic** -- does it survive costs?

A sub-cost edge is statistically real and economically worthless. A system that
reports only the first will size into it. So :class:`EconomicVerdict` is
computed from net expectancy and reported alongside the p-value, never instead
of it.

Detection runs before truth is revealed. :meth:`CalibrationRun.assert_blind`
checks the seal's reveal log was empty when the detector ran.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Sequence

from ..research.costs import CostModel
from ..research.statistics import (
    benjamini_hochberg,
    bootstrap_mean,
    deflated_sharpe_ratio,
    permutation_test,
)
from .generators import CalibrationDataset
from .truth import EdgeKind, GroundTruth

__all__ = [
    "EconomicVerdict", "StatisticalVerdict", "Detection", "RegimeBreakdown",
    "CalibrationRun", "CalibrationScore", "detect_momentum",
    "detect_mean_reversion", "detect_by_regime", "false_discovery_stress",
    "FalseDiscoveryReport", "MIN_DETECTION_SAMPLE",
]

#: Below this, no detection verdict beyond NO_EVIDENCE is defensible.
MIN_DETECTION_SAMPLE = 100


class StatisticalVerdict(str, Enum):
    """Whether the effect is distinguishable from noise."""

    NO_EVIDENCE = "no_evidence"
    POSITIVE_EFFECT = "positive_effect"
    NEGATIVE_EFFECT = "negative_effect"
    INSUFFICIENT_SAMPLE = "insufficient_sample"

    @property
    def found_something(self) -> bool:
        return self in (StatisticalVerdict.POSITIVE_EFFECT,
                        StatisticalVerdict.NEGATIVE_EFFECT)


class EconomicVerdict(str, Enum):
    """Whether the effect is worth trading after costs."""

    NOT_ASSESSED = "not_assessed"
    #: A real gross effect that costs eliminate. Statistically present,
    #: economically worthless, and the pair is the whole point.
    ECONOMICALLY_UNATTRACTIVE = "economically_unattractive"
    ECONOMICALLY_ATTRACTIVE = "economically_attractive"
    NEGATIVE_NET = "negative_net"

    @property
    def tradeable(self) -> bool:
        return self is EconomicVerdict.ECONOMICALLY_ATTRACTIVE


@dataclass(frozen=True)
class Detection:
    """One detector's blind result on one dataset."""

    name: str
    samples: int
    gross_mean_bps: float
    net_mean_bps: float
    ci_low_bps: float
    ci_high_bps: float
    p_value: float
    statistical: StatisticalVerdict
    economic: EconomicVerdict
    cost_bps: float
    note: str = ""

    @property
    def excludes_zero(self) -> bool:
        return self.ci_low_bps > 0 or self.ci_high_bps < 0

    def to_dict(self) -> dict:
        return {
            "name": self.name, "samples": self.samples,
            "gross_mean_bps": self.gross_mean_bps,
            "net_mean_bps": self.net_mean_bps,
            "ci_low_bps": self.ci_low_bps, "ci_high_bps": self.ci_high_bps,
            "p_value": self.p_value, "statistical": self.statistical.value,
            "economic": self.economic.value, "cost_bps": self.cost_bps,
            "excludes_zero": self.excludes_zero, "note": self.note,
        }


def _forward_returns(closes: Sequence[float], horizon: int) -> list[float]:
    return [(closes[i + horizon] - closes[i]) / closes[i]
            for i in range(len(closes) - horizon)]


def _assess(name: str, selected: Sequence[float], costs: CostModel,
            *, seed: int, alpha: float = 0.05,
            baseline: Sequence[float] | None = None) -> Detection:
    """Score a set of selected forward returns, statistically and economically."""
    cost = costs.round_trip_bps
    if len(selected) < MIN_DETECTION_SAMPLE:
        return Detection(name, len(selected), float("nan"), float("nan"),
                         float("nan"), float("nan"), float("nan"),
                         StatisticalVerdict.INSUFFICIENT_SAMPLE,
                         EconomicVerdict.NOT_ASSESSED, cost,
                         f"{len(selected)} samples, {MIN_DETECTION_SAMPLE} required")

    gross = list(selected)
    interval = bootstrap_mean(gross, seed=seed, resamples=2_000)
    gross_mean = sum(gross) / len(gross)
    net_mean = costs.apply(gross_mean)

    reference = list(baseline) if baseline is not None else [0.0] * len(gross)
    p_value = permutation_test(gross, reference, seed=seed, resamples=1_000)

    if p_value > alpha or not interval.excludes_zero:
        statistical = StatisticalVerdict.NO_EVIDENCE
    elif gross_mean > 0:
        statistical = StatisticalVerdict.POSITIVE_EFFECT
    else:
        statistical = StatisticalVerdict.NEGATIVE_EFFECT

    if not statistical.found_something:
        economic = EconomicVerdict.NOT_ASSESSED
    elif net_mean > 0:
        economic = EconomicVerdict.ECONOMICALLY_ATTRACTIVE
    elif gross_mean > 0:
        economic = EconomicVerdict.ECONOMICALLY_UNATTRACTIVE
    else:
        economic = EconomicVerdict.NEGATIVE_NET

    return Detection(
        name, len(gross), gross_mean * 10_000, net_mean * 10_000,
        interval.lower * 10_000, interval.upper * 10_000, p_value,
        statistical, economic, cost,
        note=(f"gross {gross_mean * 10_000:.4f} bps vs {cost:.1f} bps cost"),
    )


def detect_momentum(dataset: CalibrationDataset, *, horizon: int = 1,
                    costs: CostModel, seed: int = 7,
                    indices: Sequence[int] | None = None) -> Detection:
    """Sign-conditioned forward return: does an up bar predict an up bar?

    Signed so that the detector expresses a *strategy* rather than a
    correlation: it goes long after an up bar and short after a down bar, and
    the reported number is what that position earns.
    """
    closes = dataset.closes
    forward = _forward_returns(closes, horizon)
    prior = [(closes[i] - closes[i - 1]) / closes[i - 1]
             for i in range(1, len(closes))]

    selected: list[float] = []
    candidates = range(1, len(forward)) if indices is None else indices
    for i in candidates:
        if i < 1 or i >= len(forward):
            continue
        direction = 1.0 if prior[i - 1] > 0 else -1.0
        selected.append(direction * forward[i])
    return _assess("momentum", selected, costs, seed=seed)


def detect_mean_reversion(dataset: CalibrationDataset, *, horizon: int = 1,
                          lookback: int = 20, threshold_sigma: float = 1.5,
                          costs: CostModel, seed: int = 7) -> Detection:
    """Fade deviations beyond a threshold from a rolling mean.

    The threshold and lookback are the detector's own choices, not the
    generator's -- a detector handed the true parameters proves nothing.
    """
    closes = dataset.closes
    forward = _forward_returns(closes, horizon)
    selected: list[float] = []

    for i in range(lookback, len(forward)):
        window = closes[i - lookback:i]
        mean = sum(window) / lookback
        variance = sum((p - mean) ** 2 for p in window) / lookback
        spread = math.sqrt(variance)
        if spread <= 0:
            continue
        deviation = closes[i] - mean
        if abs(deviation) <= threshold_sigma * spread:
            continue
        direction = -1.0 if deviation > 0 else 1.0     # fade the stretch
        selected.append(direction * forward[i])
    return _assess("mean_reversion", selected, costs, seed=seed)


@dataclass(frozen=True)
class RegimeBreakdown:
    """Per-regime detections and the gap between them."""

    detections: dict[str, Detection]
    #: Difference in gross expectancy, best regime minus worst, in bps.
    spread_bps: float
    separated: bool
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "detections": {k: v.to_dict() for k, v in self.detections.items()},
            "spread_bps": self.spread_bps, "separated": self.separated,
            "note": self.note,
        }


def detect_by_regime(dataset: CalibrationDataset, *, costs: CostModel,
                     seed: int = 7, horizon: int = 1) -> RegimeBreakdown:
    """Run the momentum detector separately within each labelled regime.

    Pooling regimes with opposite signs cancels the effect. Breaking down is
    the only way to see it, which is exactly what this calibrates.
    """
    if not dataset.regime_labels:
        raise ValueError(f"{dataset.name} carries no regime labels to break down")

    by_regime: dict[str, list[int]] = {}
    for index, label in enumerate(dataset.regime_labels):
        by_regime.setdefault(label, []).append(index)

    detections = {
        label: detect_momentum(dataset, horizon=horizon, costs=costs, seed=seed,
                               indices=indices)
        for label, indices in sorted(by_regime.items())
    }
    usable = [d for d in detections.values()
              if d.statistical is not StatisticalVerdict.INSUFFICIENT_SAMPLE]
    if len(usable) < 2:
        return RegimeBreakdown(detections, float("nan"), False,
                               "fewer than two regimes had a usable sample")

    best = max(usable, key=lambda d: d.gross_mean_bps)
    worst = min(usable, key=lambda d: d.gross_mean_bps)
    spread = best.gross_mean_bps - worst.gross_mean_bps
    #: Separated when the intervals do not overlap -- the regimes differ by
    #: more than the noise in either of them.
    separated = best.ci_low_bps > worst.ci_high_bps
    return RegimeBreakdown(
        detections, spread, separated,
        f"best-worst spread {spread:.4f} bps; intervals "
        f"{'disjoint' if separated else 'overlapping'}",
    )


@dataclass(frozen=True)
class CalibrationScore:
    """What the detector found, versus what the generator put in."""

    dataset: str
    expected_kind: EdgeKind
    detection: Detection
    correct: bool
    reason: str
    truth: GroundTruth

    def to_dict(self) -> dict:
        return {
            "dataset": self.dataset,
            "expected_kind": self.expected_kind.value,
            "detection": self.detection.to_dict(),
            "correct": self.correct, "reason": self.reason,
            "truth": self.truth.to_dict(),
        }


@dataclass
class CalibrationRun:
    """Runs a detector blind, then scores it against the sealed truth."""

    dataset: CalibrationDataset
    costs: CostModel
    seed: int = 7
    detection: Detection | None = None
    reveals_before_detection: int = field(default=0)

    def run(self, detector: Callable[..., Detection]) -> Detection:
        """Execute the detector. Records the seal state beforehand."""
        self.reveals_before_detection = len(self.dataset.truth.log)
        self.detection = detector(self.dataset, costs=self.costs, seed=self.seed)
        return self.detection

    def assert_blind(self) -> None:
        """Fail if ground truth was consulted before detection ran."""
        if self.reveals_before_detection:
            raise AssertionError(
                f"{self.dataset.name}: ground truth was revealed "
                f"{self.reveals_before_detection} time(s) before detection; the "
                "result cannot be treated as a blind recovery"
            )

    def score(self) -> CalibrationScore:
        """Compare the detection to the truth. Reveals, by design."""
        if self.detection is None:
            raise RuntimeError("run a detector before scoring")
        self.assert_blind()
        truth = self.dataset.truth.reveal(
            f"scoring calibration run for {self.dataset.name}")
        detection = self.detection

        if truth.edge_kind is EdgeKind.NONE:
            correct = not detection.statistical.found_something
            reason = ("correctly found nothing in null data" if correct else
                      f"FALSE POSITIVE: reported {detection.statistical.value} "
                      f"on data with no relationship")
        elif truth.edge_kind is EdgeKind.SUB_COST:
            correct = (detection.statistical is StatisticalVerdict.POSITIVE_EFFECT
                       and detection.economic is
                       EconomicVerdict.ECONOMICALLY_UNATTRACTIVE)
            reason = ("correctly found a real effect and refused it on costs"
                      if correct else
                      f"expected a positive effect ruled economically "
                      f"unattractive; got {detection.statistical.value} / "
                      f"{detection.economic.value}")
        else:
            correct = detection.statistical is StatisticalVerdict.POSITIVE_EFFECT
            reason = (f"recovered the {truth.edge_kind.value} relationship"
                      if correct else
                      f"MISS: {truth.edge_kind.value} present but detector "
                      f"reported {detection.statistical.value}")

        return CalibrationScore(self.dataset.name, truth.edge_kind, detection,
                                correct, reason, truth)


@dataclass(frozen=True)
class FalseDiscoveryReport:
    """A large hypothesis family run against data with nothing in it."""

    trials: int
    observations: int
    alpha: float
    raw_discoveries: int
    bh_discoveries: int
    bonferroni_discoveries: int
    best_sharpe: float
    deflated_sharpe: float
    observed_false_positive_rate: float

    @property
    def raw_rate_is_calibrated(self) -> bool:
        """Uncorrected discoveries should land near alpha.

        A wide band: with a few hundred trials the binomial spread around alpha
        is substantial, and a tight assertion here would fail on seed choice
        rather than on a real defect.
        """
        return abs(self.observed_false_positive_rate - self.alpha) < 0.04

    @property
    def correction_reduces_discoveries(self) -> bool:
        return self.bh_discoveries <= self.raw_discoveries

    @property
    def dsr_penalises_selection(self) -> bool:
        """The deflated Sharpe should not endorse the best of many nulls."""
        return self.deflated_sharpe < 0.95

    def to_dict(self) -> dict:
        return {
            "trials": self.trials, "observations": self.observations,
            "alpha": self.alpha, "raw_discoveries": self.raw_discoveries,
            "bh_discoveries": self.bh_discoveries,
            "bonferroni_discoveries": self.bonferroni_discoveries,
            "best_sharpe": self.best_sharpe,
            "deflated_sharpe": self.deflated_sharpe,
            "observed_false_positive_rate": self.observed_false_positive_rate,
            "raw_rate_is_calibrated": self.raw_rate_is_calibrated,
            "correction_reduces_discoveries": self.correction_reduces_discoveries,
            "dsr_penalises_selection": self.dsr_penalises_selection,
        }


def false_discovery_stress(*, trials: int = 200, observations: int = 750,
                           alpha: float = 0.05,
                           seed: int = 101) -> FalseDiscoveryReport:
    """Run a pre-declared family of hypotheses against pure noise.

    Every hypothesis is false by construction, so every discovery is a false
    one. Three things are being checked: that the uncorrected rate lands near
    alpha (the p-values are calibrated), that Benjamini-Hochberg cuts the
    family down, and that the deflated Sharpe reacts to the number of trials
    rather than endorsing whichever null happened to look best.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    p_values: list[float] = []
    sharpes: list[float] = []

    for _ in range(trials):
        sample = rng.normal(0.0, 1.0, observations)
        mean = float(sample.mean())
        spread = float(sample.std(ddof=1))
        t_stat = mean / (spread / math.sqrt(observations)) if spread > 0 else 0.0
        # Two-sided normal approximation; observations is large enough that the
        # t and normal tails agree to well within the precision that matters.
        p_values.append(float(2.0 * (1.0 - 0.5 * (1.0 + math.erf(
            abs(t_stat) / math.sqrt(2.0))))))
        sharpes.append(mean / spread if spread > 0 else 0.0)

    raw = sum(1 for p in p_values if p <= alpha)
    bh = sum(benjamini_hochberg(p_values, alpha))
    bonf = sum(1 for p in p_values if p <= alpha / trials)
    best = max(sharpes)
    dsr = deflated_sharpe_ratio(best, n_trials=trials, n_observations=observations)

    return FalseDiscoveryReport(
        trials=trials, observations=observations, alpha=alpha,
        raw_discoveries=raw, bh_discoveries=bh, bonferroni_discoveries=bonf,
        best_sharpe=best, deflated_sharpe=dsr,
        observed_false_positive_rate=raw / trials,
    )
