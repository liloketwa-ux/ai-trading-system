"""Effect sizes, bootstrap intervals, and multiple-testing control.

A p-value alone is not a finding. Three things are reported for every
hypothesis, and none is sufficient by itself:

* **Effect size** -- how big, in units someone can act on. A statistically
  reliable one-basis-point edge is a real effect and an economically useless
  one.
* **Confidence interval** -- bootstrapped, seeded, reproducible. The interval
  is the result; the point estimate is a summary of it.
* **Multiple-testing adjustment** -- because testing forty hypotheses and
  reporting the best one guarantees a discovery whether or not anything is
  there.

The Benjamini-Hochberg procedure is the default control: Bonferroni is correct
but so conservative on a large family that it rejects real effects, while
false-discovery-rate control keeps the family honest without destroying power.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

__all__ = [
    "BootstrapResult", "EffectSize", "bootstrap_mean", "bootstrap_difference",
    "cohens_d", "hit_rate", "benjamini_hochberg", "bonferroni",
    "deflated_sharpe_ratio", "permutation_test",
]


@dataclass(frozen=True)
class BootstrapResult:
    """A point estimate with a resampled interval."""

    estimate: float
    lower: float
    upper: float
    n: int
    confidence: float
    seed: int
    resamples: int

    @property
    def excludes_zero(self) -> bool:
        """Whether the interval is entirely on one side of zero."""
        return (self.lower > 0) or (self.upper < 0)

    @property
    def width(self) -> float:
        return self.upper - self.lower

    def __str__(self) -> str:
        return (
            f"{self.estimate:+.6f} [{self.lower:+.6f}, {self.upper:+.6f}] "
            f"({self.confidence:.0%} CI, n={self.n})"
        )


@dataclass(frozen=True)
class EffectSize:
    """Absolute and relative difference against a baseline."""

    metric: str
    treatment: float
    baseline: float
    absolute: float
    relative: float | None
    standardized: float | None = None

    def __str__(self) -> str:
        relative = f"{self.relative:+.1%}" if self.relative is not None else "n/a"
        return (
            f"{self.metric}: {self.treatment:+.6f} vs {self.baseline:+.6f} "
            f"(abs {self.absolute:+.6f}, rel {relative})"
        )


def bootstrap_mean(
    values, *, seed: int = 0, resamples: int = 10_000, confidence: float = 0.95
) -> BootstrapResult:
    """Percentile bootstrap of the mean. Seeded, therefore reproducible."""
    data = np.asarray([v for v in values if v is not None], dtype="float64")
    data = data[np.isfinite(data)]
    if data.size == 0:
        return BootstrapResult(float("nan"), float("nan"), float("nan"), 0,
                               confidence, seed, resamples)
    if data.size == 1:
        only = float(data[0])
        return BootstrapResult(only, only, only, 1, confidence, seed, resamples)

    rng = np.random.default_rng(seed)
    draws = rng.choice(data, size=(resamples, data.size), replace=True).mean(axis=1)
    tail = (1.0 - confidence) / 2.0
    return BootstrapResult(
        float(data.mean()), float(np.quantile(draws, tail)),
        float(np.quantile(draws, 1.0 - tail)), int(data.size),
        confidence, seed, resamples,
    )


def bootstrap_difference(
    treatment, baseline, *, seed: int = 0, resamples: int = 10_000,
    confidence: float = 0.95,
) -> BootstrapResult:
    """Bootstrap the difference in means between two independent samples."""
    a = np.asarray([v for v in treatment if v is not None], dtype="float64")
    b = np.asarray([v for v in baseline if v is not None], dtype="float64")
    a, b = a[np.isfinite(a)], b[np.isfinite(b)]
    if a.size == 0 or b.size == 0:
        return BootstrapResult(float("nan"), float("nan"), float("nan"),
                               int(a.size), confidence, seed, resamples)

    rng = np.random.default_rng(seed)
    draws = (
        rng.choice(a, size=(resamples, a.size), replace=True).mean(axis=1)
        - rng.choice(b, size=(resamples, b.size), replace=True).mean(axis=1)
    )
    tail = (1.0 - confidence) / 2.0
    return BootstrapResult(
        float(a.mean() - b.mean()), float(np.quantile(draws, tail)),
        float(np.quantile(draws, 1.0 - tail)), int(a.size),
        confidence, seed, resamples,
    )


def permutation_test(treatment, baseline, *, seed: int = 0, resamples: int = 10_000) -> float:
    """Two-sided permutation p-value for a difference in means.

    Makes no distributional assumption, which suits forward returns -- they are
    fat-tailed and a t-test's assumptions do not hold.
    """
    a = np.asarray([v for v in treatment if v is not None], dtype="float64")
    b = np.asarray([v for v in baseline if v is not None], dtype="float64")
    a, b = a[np.isfinite(a)], b[np.isfinite(b)]
    if a.size == 0 or b.size == 0:
        return float("nan")

    observed = abs(a.mean() - b.mean())
    pooled = np.concatenate([a, b])
    rng = np.random.default_rng(seed)

    count = 0
    for _ in range(resamples):
        rng.shuffle(pooled)
        if abs(pooled[: a.size].mean() - pooled[a.size:].mean()) >= observed:
            count += 1
    return (count + 1) / (resamples + 1)


def cohens_d(treatment, baseline) -> float | None:
    """Standardized mean difference, pooled standard deviation."""
    a = np.asarray([v for v in treatment if v is not None], dtype="float64")
    b = np.asarray([v for v in baseline if v is not None], dtype="float64")
    a, b = a[np.isfinite(a)], b[np.isfinite(b)]
    if a.size < 2 or b.size < 2:
        return None
    pooled = math.sqrt(
        ((a.size - 1) * a.var(ddof=1) + (b.size - 1) * b.var(ddof=1))
        / (a.size + b.size - 2)
    )
    return float((a.mean() - b.mean()) / pooled) if pooled > 0 else None


def hit_rate(values, threshold: float = 0.0) -> float:
    """Fraction of outcomes strictly above a threshold."""
    data = np.asarray([v for v in values if v is not None], dtype="float64")
    data = data[np.isfinite(data)]
    return float((data > threshold).mean()) if data.size else float("nan")


def bonferroni(p_values: list[float], alpha: float = 0.05) -> list[bool]:
    """Family-wise error control. Correct, and conservative on large families."""
    if not p_values:
        return []
    threshold = alpha / len(p_values)
    return [p <= threshold for p in p_values]


def benjamini_hochberg(p_values: list[float], alpha: float = 0.05) -> list[bool]:
    """False-discovery-rate control.

    Keeps a large hypothesis family honest without the power loss Bonferroni
    imposes when dozens of combinations are tested.
    """
    n = len(p_values)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: p_values[i])
    rejected = [False] * n
    largest = -1
    for rank, index in enumerate(order, start=1):
        if p_values[index] <= alpha * rank / n:
            largest = rank
    for rank, index in enumerate(order, start=1):
        if rank <= largest:
            rejected[index] = True
    return rejected


def deflated_sharpe_ratio(
    observed_sharpe: float, n_trials: int, n_observations: int,
    *, skew: float = 0.0, kurtosis: float = 3.0,
) -> float:
    """Probability the observed Sharpe exceeds what selection bias alone gives.

    Testing many configurations makes the best-looking Sharpe rise even with no
    edge at all. This deflates for the trial count, sample length and the
    non-normality of returns.

    Returns a probability in [0, 1]; values near 1 mean the result survives the
    selection effect, near 0 that it does not.
    """
    if n_trials < 1 or n_observations < 2:
        return float("nan")

    euler = 0.5772156649
    # Expected maximum Sharpe from n_trials independent draws of pure noise.
    if n_trials == 1:
        expected_max = 0.0
    else:
        z1 = _inverse_normal_cdf(1.0 - 1.0 / n_trials)
        z2 = _inverse_normal_cdf(1.0 - 1.0 / (n_trials * math.e))
        expected_max = (1 - euler) * z1 + euler * z2

    variance = (
        1.0 - skew * observed_sharpe
        + ((kurtosis - 1.0) / 4.0) * observed_sharpe**2
    ) / (n_observations - 1)
    if variance <= 0:
        return float("nan")

    return float(_normal_cdf((observed_sharpe - expected_max) / math.sqrt(variance)))


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _inverse_normal_cdf(p: float) -> float:
    """Acklam's rational approximation. Adequate for the tail sizes used here."""
    if not 0.0 < p < 1.0:
        return 0.0
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    low, high = 0.02425, 1 - 0.02425
    if p < low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > high:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
