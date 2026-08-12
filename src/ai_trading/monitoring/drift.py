"""Data drift detection.

Models are trained on one distribution and deployed into another. When the
inputs shift — a data provider changes its scoring, social volume steps up,
volatility regime turns over — a model can keep returning confident numbers
that no longer mean what they meant in training. These tests catch that.

Two complementary measures are provided. The **population stability index**
asks how much probability mass moved between bins, and is the standard
industry threshold-based check. The **Kolmogorov-Smirnov** test asks whether
two samples plausibly came from the same distribution at all, and gives a
p-value. PSI is the more forgiving of the two; KS will flag shifts PSI shrugs
at, especially on large samples.

Implemented on numpy alone — no SciPy dependency — using the standard
asymptotic approximation for the KS p-value.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

__all__ = [
    "PSI_STABLE",
    "PSI_SHIFTED",
    "KSResult",
    "population_stability_index",
    "ks_two_sample",
    "drift_report",
]

#: Conventional PSI interpretation thresholds.
PSI_STABLE = 0.10  # below this: no meaningful shift
PSI_SHIFTED = 0.25  # above this: significant shift, investigate


@dataclass(frozen=True)
class KSResult:
    """Two-sample Kolmogorov-Smirnov result."""

    statistic: float
    p_value: float

    def is_significant(self, alpha: float = 0.05) -> bool:
        return self.p_value < alpha


def population_stability_index(
    reference: np.ndarray | pd.Series,
    current: np.ndarray | pd.Series,
    bins: int = 10,
    epsilon: float = 1e-6,
) -> float:
    """Population stability index between a reference and current sample.

    Bin edges come from the *reference* quantiles, so the reference is by
    construction uniformly spread and any concentration in the current sample
    shows up as movement. Returns 0.0 for identical distributions and grows
    without bound as they separate.

    Args:
        reference: Baseline sample (e.g. the training distribution).
        current: Sample to compare against it.
        bins: Number of quantile bins.
        epsilon: Floor applied to bin proportions so empty bins do not make
            the logarithm diverge.

    Returns:
        The PSI. Compare against :data:`PSI_STABLE` and :data:`PSI_SHIFTED`.
    """
    ref = _clean(reference)
    cur = _clean(current)
    if bins < 2:
        raise ValueError(f"bins must be >= 2, got {bins}")
    if ref.size == 0 or cur.size == 0:
        return float("nan")

    edges = np.unique(np.quantile(ref, np.linspace(0.0, 1.0, bins + 1)))
    if edges.size < 2:
        # A constant reference has no spread to compare against.
        return 0.0 if np.allclose(cur, ref[0]) else float("inf")

    # Open the outer edges so values beyond the reference range still land in a bin.
    edges[0], edges[-1] = -np.inf, np.inf

    ref_pct = np.histogram(ref, bins=edges)[0] / ref.size
    cur_pct = np.histogram(cur, bins=edges)[0] / cur.size

    ref_pct = np.clip(ref_pct, epsilon, None)
    cur_pct = np.clip(cur_pct, epsilon, None)

    return float(np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)))


def ks_two_sample(
    reference: np.ndarray | pd.Series,
    current: np.ndarray | pd.Series,
) -> KSResult:
    """Two-sample Kolmogorov-Smirnov test.

    The statistic is the largest gap between the two empirical CDFs. The
    p-value uses the standard asymptotic series, which is accurate for
    moderate and large samples and conservative for very small ones.
    """
    ref = np.sort(_clean(reference))
    cur = np.sort(_clean(current))
    n1, n2 = ref.size, cur.size
    if n1 == 0 or n2 == 0:
        return KSResult(float("nan"), float("nan"))

    grid = np.concatenate([ref, cur])
    cdf_ref = np.searchsorted(ref, grid, side="right") / n1
    cdf_cur = np.searchsorted(cur, grid, side="right") / n2
    statistic = float(np.max(np.abs(cdf_ref - cdf_cur)))

    effective_n = np.sqrt(n1 * n2 / (n1 + n2))
    return KSResult(statistic, _ks_p_value(statistic, effective_n))


def drift_report(
    reference: pd.DataFrame,
    current: pd.DataFrame,
    *,
    bins: int = 10,
    columns: list[str] | None = None,
) -> pd.DataFrame:
    """Per-feature drift summary for two frames.

    Only columns present in both frames are compared; the rest are ignored
    rather than silently treated as drifted.

    Returns:
        A frame indexed by feature with ``psi``, ``ks_statistic``,
        ``ks_p_value``, and a ``verdict`` of ``stable``, ``moderate``, or
        ``shifted``, sorted worst-first by PSI.
    """
    shared = [c for c in (columns or reference.columns) if c in current.columns]
    if not shared:
        return pd.DataFrame(
            columns=["psi", "ks_statistic", "ks_p_value", "verdict"]
        ).rename_axis("feature")

    rows = {}
    for column in shared:
        if not pd.api.types.is_numeric_dtype(reference[column]):
            continue
        psi = population_stability_index(reference[column], current[column], bins)
        ks = ks_two_sample(reference[column], current[column])
        rows[column] = {
            "psi": psi,
            "ks_statistic": ks.statistic,
            "ks_p_value": ks.p_value,
            "verdict": _verdict(psi),
        }

    report = pd.DataFrame.from_dict(rows, orient="index").rename_axis("feature")
    return report.sort_values("psi", ascending=False) if not report.empty else report


# -- internals -------------------------------------------------------------


def _verdict(psi: float) -> str:
    if not np.isfinite(psi):
        return "unknown"
    if psi < PSI_STABLE:
        return "stable"
    if psi < PSI_SHIFTED:
        return "moderate"
    return "shifted"


def _ks_p_value(statistic: float, effective_n: float, terms: int = 100) -> float:
    """Asymptotic KS p-value: ``2 * sum (-1)^(j-1) exp(-2 j^2 lambda^2)``."""
    if effective_n <= 0 or not np.isfinite(statistic):
        return float("nan")
    lam = (effective_n + 0.12 + 0.11 / effective_n) * statistic
    if lam <= 0:
        return 1.0
    j = np.arange(1, terms + 1)
    total = 2.0 * np.sum((-1.0) ** (j - 1) * np.exp(-2.0 * (j**2) * lam**2))
    return float(np.clip(total, 0.0, 1.0))


def _clean(values: np.ndarray | pd.Series) -> np.ndarray:
    array = np.asarray(values, dtype="float64").ravel()
    return array[np.isfinite(array)]
