"""Backtest-versus-live divergence.

A strategy that backtested well and then underperforms in production has told
you something, and the sooner it is measured the better. This compares live
returns against the same strategy's backtested returns over the same periods
and asks whether the gap is larger than noise.

The comparison is **paired**: replay the backtest across the live window and
compare bar by bar. Pairing removes the shared market move, so what remains is
the implementation gap — slippage, latency, missed fills, or a strategy whose
edge has decayed.

A caution on interpretation. Live results underperforming a backtest is the
normal case, not proof of a bug: backtests omit costs that production pays.
Treat a significant result as a prompt to investigate, not a verdict.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..backtest import metrics as _metrics

__all__ = ["DivergenceReport", "compare_to_backtest"]


@dataclass(frozen=True)
class DivergenceReport:
    """Result of comparing live returns to backtested returns.

    Attributes:
        n_periods: Paired observations compared.
        mean_difference: Average per-period gap (live minus backtest).
        annualized_difference: That gap scaled to a year.
        tracking_error: Annualized standard deviation of the gap.
        t_statistic: Paired t-statistic on the mean gap.
        p_value: Two-sided p-value, normal approximation.
        live_sharpe: Annualized Sharpe of the live returns.
        backtest_sharpe: Annualized Sharpe of the backtested returns.
        verdict: ``aligned``, ``underperforming``, or ``outperforming``.
    """

    n_periods: int
    mean_difference: float
    annualized_difference: float
    tracking_error: float
    t_statistic: float
    p_value: float
    live_sharpe: float
    backtest_sharpe: float
    verdict: str

    @property
    def is_significant(self) -> bool:
        return self.verdict != "aligned"

    def summary(self) -> str:
        return (
            f"{self.verdict}: live minus backtest {self.annualized_difference:+.2%}/yr "
            f"(tracking error {self.tracking_error:.2%}, t={self.t_statistic:+.2f}, "
            f"p={self.p_value:.4f}, n={self.n_periods})"
        )


def compare_to_backtest(
    live_returns: pd.Series,
    backtest_returns: pd.Series,
    *,
    periods_per_year: int = 252,
    alpha: float = 0.05,
) -> DivergenceReport:
    """Compare live returns to backtested returns over the same periods.

    The two series are aligned on their index and only overlapping periods are
    compared, so a partial live history does not silently compare mismatched
    dates.

    Args:
        live_returns: Realized per-period returns.
        backtest_returns: Backtested per-period returns over the same periods.
        periods_per_year: Bars per year, for annualization.
        alpha: Two-sided significance level for the verdict.

    Raises:
        ValueError: If fewer than two periods overlap.
    """
    if periods_per_year <= 0:
        raise ValueError(f"periods_per_year must be > 0, got {periods_per_year}")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")

    paired = pd.concat(
        [live_returns.rename("live"), backtest_returns.rename("backtest")],
        axis=1,
        join="inner",
    ).dropna()
    if len(paired) < 2:
        raise ValueError(
            f"need at least 2 overlapping periods to compare, got {len(paired)}"
        )

    difference = paired["live"] - paired["backtest"]
    n = len(difference)
    mean_diff = float(difference.mean())
    std_diff = float(difference.std(ddof=1))

    if std_diff == 0:
        t_stat = 0.0 if mean_diff == 0 else math.copysign(float("inf"), mean_diff)
        p_value = 1.0 if mean_diff == 0 else 0.0
    else:
        t_stat = mean_diff / (std_diff / math.sqrt(n))
        p_value = _two_sided_normal_p(t_stat)

    if p_value >= alpha:
        verdict = "aligned"
    else:
        verdict = "underperforming" if mean_diff < 0 else "outperforming"

    return DivergenceReport(
        n_periods=n,
        mean_difference=mean_diff,
        annualized_difference=mean_diff * periods_per_year,
        tracking_error=std_diff * math.sqrt(periods_per_year),
        t_statistic=t_stat,
        p_value=p_value,
        live_sharpe=_metrics.sharpe_ratio(paired["live"], periods_per_year),
        backtest_sharpe=_metrics.sharpe_ratio(paired["backtest"], periods_per_year),
        verdict=verdict,
    )


def _two_sided_normal_p(t_stat: float) -> float:
    """Two-sided p-value under a normal approximation to the t-distribution.

    Accurate for moderate and large samples; slightly anti-conservative for
    very small ones, where the t-distribution's heavier tails matter.
    """
    if not np.isfinite(t_stat):
        return 0.0
    return float(math.erfc(abs(t_stat) / math.sqrt(2.0)))
