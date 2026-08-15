"""Robustness verdicts and the Phase 7 report.

Criteria are configuration, not constants baked into the verdict logic, and the
criteria version travels with every result. Two reasons: thresholds chosen
because they made a candidate look good are the classic way robustness testing
becomes robustness theatre, and a threshold changed later silently re-grades
every historical verdict unless the version is recorded alongside it.

``ROBUST_CANDIDATE`` requires every gate. Verdicts are ordered so the most
disqualifying condition wins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

import numpy as np

from .robustness import RobustnessMatrix

__all__ = ["Verdict", "RobustnessCriteria", "WindowResult", "InstrumentReport",
           "CandidateReport", "grade", "DEFAULT_CRITERIA"]


class Verdict(str, Enum):
    """Conservative statuses. Ordered from least to most favourable."""

    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    OUT_OF_SAMPLE_FAILURE = "OUT_OF_SAMPLE_FAILURE"
    COST_SENSITIVE = "COST_SENSITIVE"
    EXECUTION_SENSITIVE = "EXECUTION_SENSITIVE"
    REGIME_DEPENDENT = "REGIME_DEPENDENT"
    UNSTABLE = "UNSTABLE"
    SURVIVES_ROBUSTNESS = "SURVIVES_ROBUSTNESS"
    ROBUST_CANDIDATE = "ROBUST_CANDIDATE"


@dataclass(frozen=True)
class RobustnessCriteria:
    """Pre-declared gates. Version travels with every verdict."""

    version: str = "1"
    min_total_trades: int = 100
    min_windows: int = 5
    min_positive_window_fraction: float = 0.5
    require_positive_mean_expectancy: bool = True
    require_positive_median_expectancy: bool = True
    max_single_window_loss_fraction: float = 0.5   # of total equity
    min_cost_multiple_survived: float = 1.5
    min_delay_bars_survived: int = 1
    max_best_trade_dependence: float = 0.5         # expectancy drop when best removed
    min_regimes_positive: int = 2
    min_regime_sample: int = 30

    def to_dict(self) -> dict:
        return {
            "criteria_version": self.version,
            "min_total_trades": self.min_total_trades,
            "min_windows": self.min_windows,
            "min_positive_window_fraction": self.min_positive_window_fraction,
            "max_single_window_loss_fraction": self.max_single_window_loss_fraction,
            "min_cost_multiple_survived": self.min_cost_multiple_survived,
            "min_delay_bars_survived": self.min_delay_bars_survived,
            "max_best_trade_dependence": self.max_best_trade_dependence,
            "min_regimes_positive": self.min_regimes_positive,
            "min_regime_sample": self.min_regime_sample,
        }


DEFAULT_CRITERIA = RobustnessCriteria()


@dataclass(frozen=True)
class WindowResult:
    """One walk-forward fold's outcome."""

    index: int
    train_expectancy: float
    validation_expectancy: float
    test_expectancy: float
    test_trades: int
    test_net_return: float
    test_max_drawdown: float
    purged: int = 0

    @property
    def positive(self) -> bool:
        return self.test_expectancy > 0


@dataclass
class InstrumentReport:
    """Per-instrument result. Never aggregated away."""

    instrument: str
    windows: list[WindowResult] = field(default_factory=list)
    matrix: RobustnessMatrix | None = None
    regimes: dict[str, tuple[int, float]] = field(default_factory=dict)
    ambiguous_bar_count: int = 0
    contract_series: dict | None = None
    funding: dict | None = None

    @property
    def total_trades(self) -> int:
        return sum(w.test_trades for w in self.windows)

    @property
    def out_of_sample_expectancies(self) -> list[float]:
        return [w.test_expectancy for w in self.windows]

    @property
    def mean_expectancy(self) -> float:
        values = self.out_of_sample_expectancies
        return float(np.mean(values)) if values else float("nan")

    @property
    def median_expectancy(self) -> float:
        values = self.out_of_sample_expectancies
        return float(np.median(values)) if values else float("nan")

    @property
    def positive_windows(self) -> int:
        return sum(1 for w in self.windows if w.positive)

    @property
    def positive_window_fraction(self) -> float:
        return self.positive_windows / len(self.windows) if self.windows else 0.0

    @property
    def worst_window_return(self) -> float:
        return min((w.test_net_return for w in self.windows), default=0.0)

    @property
    def max_drawdown(self) -> float:
        return max((w.test_max_drawdown for w in self.windows), default=0.0)

    def reliable_regimes(self, minimum: int) -> dict[str, tuple[int, float]]:
        return {k: v for k, v in self.regimes.items() if v[0] >= minimum}


def grade(
    report: InstrumentReport, criteria: RobustnessCriteria = DEFAULT_CRITERIA
) -> tuple[Verdict, list[str]]:
    """Assign a verdict deterministically. Most disqualifying condition wins."""
    reasons: list[str] = []

    if len(report.windows) < criteria.min_windows or \
            report.total_trades < criteria.min_total_trades:
        return Verdict.INSUFFICIENT_SAMPLE, [
            f"{report.total_trades} trades across {len(report.windows)} windows; "
            f"gates require {criteria.min_total_trades} trades and "
            f"{criteria.min_windows} windows before any robustness claim"
        ]

    if criteria.require_positive_mean_expectancy and not report.mean_expectancy > 0:
        reasons.append(f"mean out-of-sample expectancy {report.mean_expectancy:+.4f}")
        return Verdict.OUT_OF_SAMPLE_FAILURE, reasons

    if criteria.require_positive_median_expectancy and not report.median_expectancy > 0:
        reasons.append(
            f"median window expectancy {report.median_expectancy:+.4f} -- the mean is "
            "carried by a minority of windows"
        )
        return Verdict.UNSTABLE, reasons

    if report.positive_window_fraction < criteria.min_positive_window_fraction:
        reasons.append(
            f"only {report.positive_windows}/{len(report.windows)} windows positive"
        )
        return Verdict.UNSTABLE, reasons

    if report.worst_window_return < -abs(criteria.max_single_window_loss_fraction):
        reasons.append(
            f"catastrophic single window: {report.worst_window_return:+.1%}"
        )
        return Verdict.UNSTABLE, reasons

    matrix = report.matrix
    if matrix is not None:
        breaks = matrix.cost_breaks_at
        if breaks is not None and breaks <= criteria.min_cost_multiple_survived:
            reasons.append(
                f"expectancy turns negative at {breaks}x costs, below the "
                f"{criteria.min_cost_multiple_survived}x gate"
            )
            return Verdict.COST_SENSITIVE, reasons

        delay_breaks = matrix.delay_breaks_at
        if delay_breaks is not None and delay_breaks <= criteria.min_delay_bars_survived:
            reasons.append(f"fails at {delay_breaks} bar(s) of execution delay")
            return Verdict.EXECUTION_SENSITIVE, reasons

        for removal in matrix.trade_removal:
            if removal.label == "remove_best_1":
                if not removal.survives:
                    reasons.append("expectancy turns negative without its single best trade")
                    return Verdict.UNSTABLE, reasons
                drop = removal.relative_change
                if drop is not None and abs(drop) > criteria.max_best_trade_dependence:
                    reasons.append(
                        f"removing the best trade moves expectancy {drop:+.1%}, beyond the "
                        f"{criteria.max_best_trade_dependence:.0%} gate"
                    )
                    return Verdict.UNSTABLE, reasons

    reliable = report.reliable_regimes(criteria.min_regime_sample)
    if reliable:
        positive = sum(1 for _, (_, mean) in reliable.items() if mean > 0)
        if positive < min(criteria.min_regimes_positive, len(reliable)):
            reasons.append(
                f"positive in only {positive}/{len(reliable)} regimes with adequate samples"
            )
            return Verdict.REGIME_DEPENDENT, reasons

    reasons.append(
        f"{report.positive_windows}/{len(report.windows)} windows positive, "
        f"mean expectancy {report.mean_expectancy:+.4f}, "
        f"survives costs and delay gates"
    )
    if matrix is not None and matrix.curves and not matrix.outlier_dependent:
        return Verdict.ROBUST_CANDIDATE, reasons
    reasons.append("no perturbation matrix supplied; cannot certify ROBUST_CANDIDATE")
    return Verdict.SURVIVES_ROBUSTNESS, reasons


@dataclass
class CandidateReport:
    """Full Phase 7 record for one candidate across instruments."""

    candidate_id: str
    lineage: dict
    criteria: RobustnessCriteria
    walk_forward: dict
    instruments: dict[str, InstrumentReport] = field(default_factory=dict)
    verdicts: dict[str, Verdict] = field(default_factory=dict)
    reasons: dict[str, list[str]] = field(default_factory=dict)
    holdout: dict | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def add(self, report: InstrumentReport) -> None:
        self.instruments[report.instrument] = report
        verdict, reasons = grade(report, self.criteria)
        self.verdicts[report.instrument] = verdict
        self.reasons[report.instrument] = reasons

    @property
    def overall(self) -> Verdict:
        """The weakest instrument verdict.

        Never the best: a candidate that works on ES and fails on NQ is not a
        robust candidate, it is an ES candidate at best, and reporting the
        maximum would hide exactly the failure worth knowing about.
        """
        if not self.verdicts:
            return Verdict.INSUFFICIENT_SAMPLE
        order = list(Verdict)
        return min(self.verdicts.values(), key=lambda v: order.index(v))

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "lineage": self.lineage,
            "criteria": self.criteria.to_dict(),
            "walk_forward": self.walk_forward,
            "overall_verdict": self.overall.value,
            "instruments": {
                name: {
                    "windows": len(r.windows),
                    "total_trades": r.total_trades,
                    "mean_expectancy": r.mean_expectancy,
                    "median_expectancy": r.median_expectancy,
                    "positive_windows": f"{r.positive_windows}/{len(r.windows)}",
                    "max_drawdown": r.max_drawdown,
                    "worst_window_return": r.worst_window_return,
                    "ambiguous_bar_count": r.ambiguous_bar_count,
                    "regimes": {k: {"n": v[0], "mean": v[1]} for k, v in r.regimes.items()},
                    "robustness": r.matrix.to_dict() if r.matrix else None,
                    "contract_series": r.contract_series,
                    "funding": r.funding,
                    "verdict": self.verdicts[name].value,
                    "reasons": self.reasons[name],
                }
                for name, r in self.instruments.items()
            },
            "holdout": self.holdout,
            "created_at": self.created_at.isoformat(),
        }

    def render(self) -> str:
        lines = [
            f"Candidate        : {self.candidate_id}",
            f"Fingerprint      : {self.lineage.get('fingerprint')}",
            f"Dataset          : {self.lineage.get('dataset_version')}",
            f"Criteria version : {self.criteria.version}",
            f"Walk-forward     : {self.walk_forward.get('protocol_version')} "
            f"(embargo {self.walk_forward.get('embargo_s', 0)}s, "
            f"purge horizon {self.walk_forward.get('label_horizon_s', 0)}s)",
            "",
        ]
        for name, report in sorted(self.instruments.items()):
            verdict = self.verdicts[name]
            lines += [
                f"[{name}]  {verdict.value}",
                f"  windows          : {len(report.windows)}  "
                f"({report.positive_windows} positive)",
                f"  trades           : {report.total_trades}",
                f"  mean expectancy  : {report.mean_expectancy:+.4f}",
                f"  median expectancy: {report.median_expectancy:+.4f}",
                f"  max drawdown     : {report.max_drawdown:.1%}",
                f"  worst window     : {report.worst_window_return:+.1%}",
                f"  ambiguous bars   : {report.ambiguous_bar_count}",
            ]
            if report.matrix:
                lines.append(
                    f"  cost breaks at   : {report.matrix.cost_breaks_at or 'survives all'}"
                )
                lines.append(
                    f"  delay breaks at  : {report.matrix.delay_breaks_at or 'survives all'}"
                )
                lines.append(f"  outlier dependent: {report.matrix.outlier_dependent}")
            if report.contract_series:
                lines.append(
                    f"  contracts        : {report.contract_series.get('symbol')} "
                    f"continuous={report.contract_series['policy']['supports_continuous_history']}"
                )
            if report.funding:
                lines.append(
                    f"  economically conf: {report.funding.get('economically_confident')}"
                )
            lines += [f"    - {r}" for r in self.reasons[name]]
            lines.append("")

        lines.append(f"OVERALL (weakest instrument): {self.overall.value}")
        if self.holdout is None:
            lines.append("Holdout          : NOT SPENT")
        return "\n".join(lines)
