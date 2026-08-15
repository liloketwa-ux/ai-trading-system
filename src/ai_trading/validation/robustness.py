"""Robustness perturbation matrix and trade-removal analysis.

A result that survives only at the exact cost and delay assumed is not a
finding, it is a coincidence with a decimal point. Every candidate is re-run
under deteriorated execution, and the point at which it stops working is
reported as a number rather than assumed to be far away.

**Delay is expressed in bars, not milliseconds.** The dataset's finest
resolution bounds what can honestly be claimed: on hourly bars, a 250ms delay is
unobservable, and reporting sensitivity to it would imply a precision the data
does not contain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

import numpy as np

__all__ = [
    "PerturbationAxis", "PerturbationPoint", "SensitivityCurve",
    "TradeRemovalResult", "RobustnessMatrix", "COST_MULTIPLIERS",
    "SLIPPAGE_MULTIPLIERS", "DELAY_BARS", "run_trade_removal",
    "breakeven_multiplier",
]

COST_MULTIPLIERS = (1.0, 1.25, 1.5, 2.0, 3.0)
SLIPPAGE_MULTIPLIERS = (1.0, 1.5, 2.0, 3.0)
#: Bars, not milliseconds. The dataset's resolution bounds the claim.
DELAY_BARS = (0, 1, 2, 3)


class PerturbationAxis(str, Enum):
    COST = "cost"
    SLIPPAGE = "slippage"
    DELAY_BARS = "delay_bars"


@dataclass(frozen=True)
class PerturbationPoint:
    """One cell of the matrix."""

    axis: PerturbationAxis
    magnitude: float
    expectancy: float
    net_return: float
    trade_count: int
    max_drawdown: float

    @property
    def survives(self) -> bool:
        return self.expectancy > 0


@dataclass
class SensitivityCurve:
    """How a metric degrades along one axis."""

    axis: PerturbationAxis
    points: list[PerturbationPoint] = field(default_factory=list)

    @property
    def baseline(self) -> PerturbationPoint | None:
        return self.points[0] if self.points else None

    @property
    def breaks_at(self) -> float | None:
        """First magnitude at which expectancy turns non-positive."""
        for point in self.points:
            if not point.survives:
                return point.magnitude
        return None

    @property
    def survives_all(self) -> bool:
        return bool(self.points) and all(p.survives for p in self.points)

    def degradation(self) -> float | None:
        """Fractional drop in expectancy from the first to the last point."""
        if len(self.points) < 2:
            return None
        first, last = self.points[0].expectancy, self.points[-1].expectancy
        if first == 0:
            return None
        return (last - first) / abs(first)

    def to_dict(self) -> dict:
        return {
            "axis": self.axis.value,
            "breaks_at": self.breaks_at,
            "survives_all": self.survives_all,
            "degradation": self.degradation(),
            "points": [
                {"magnitude": p.magnitude, "expectancy": p.expectancy,
                 "net_return": p.net_return, "trades": p.trade_count,
                 "max_drawdown": p.max_drawdown, "survives": p.survives}
                for p in self.points
            ],
        }


@dataclass(frozen=True)
class TradeRemovalResult:
    """Expectancy after removing selected trades."""

    label: str
    removed: int
    remaining: int
    expectancy: float
    baseline_expectancy: float

    @property
    def change(self) -> float:
        return self.expectancy - self.baseline_expectancy

    @property
    def relative_change(self) -> float | None:
        if self.baseline_expectancy == 0:
            return None
        return self.change / abs(self.baseline_expectancy)

    @property
    def survives(self) -> bool:
        return self.expectancy > 0


def run_trade_removal(pnls: Sequence[float]) -> list[TradeRemovalResult]:
    """Recompute expectancy with outliers removed.

    A candidate whose edge disappears when its best trade is deleted did not
    have an edge; it had one lucky trade and a lot of noise.
    """
    values = [float(v) for v in pnls if v is not None and np.isfinite(v)]
    if not values:
        return []

    baseline = float(np.mean(values))
    ordered = sorted(values)
    results: list[TradeRemovalResult] = []

    def record(label: str, remaining: list[float], removed: int) -> None:
        expectancy = float(np.mean(remaining)) if remaining else float("nan")
        results.append(TradeRemovalResult(label, removed, len(remaining),
                                          expectancy, baseline))

    for count in (1, 5, 10):
        if len(ordered) > count:
            record(f"remove_best_{count}", ordered[:-count], count)
    for count in (1, 5):
        if len(ordered) > count:
            record(f"remove_worst_{count}", ordered[count:], count)

    top_five_percent = max(1, int(round(len(ordered) * 0.05)))
    if len(ordered) > top_five_percent:
        record("remove_top_5pct_wins", ordered[:-top_five_percent], top_five_percent)

    return results


def breakeven_multiplier(curve: SensitivityCurve) -> float | None:
    """Interpolated magnitude at which expectancy crosses zero."""
    points = curve.points
    for previous, current in zip(points, points[1:]):
        if previous.expectancy > 0 >= current.expectancy:
            span = previous.expectancy - current.expectancy
            if span == 0:
                return current.magnitude
            fraction = previous.expectancy / span
            return previous.magnitude + fraction * (current.magnitude - previous.magnitude)
    return None


@dataclass
class RobustnessMatrix:
    """The full perturbation result for one candidate on one instrument."""

    candidate_id: str
    instrument: str
    curves: dict[str, SensitivityCurve] = field(default_factory=dict)
    trade_removal: list[TradeRemovalResult] = field(default_factory=list)

    def add(self, curve: SensitivityCurve) -> None:
        self.curves[curve.axis.value] = curve

    @property
    def cost_breaks_at(self) -> float | None:
        curve = self.curves.get(PerturbationAxis.COST.value)
        return curve.breaks_at if curve else None

    @property
    def delay_breaks_at(self) -> float | None:
        curve = self.curves.get(PerturbationAxis.DELAY_BARS.value)
        return curve.breaks_at if curve else None

    @property
    def outlier_dependent(self) -> bool:
        """True when removing the single best trade kills the expectancy."""
        for result in self.trade_removal:
            if result.label == "remove_best_1":
                return not result.survives
        return False

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "instrument": self.instrument,
            "curves": {name: curve.to_dict() for name, curve in self.curves.items()},
            "trade_removal": [
                {"label": r.label, "removed": r.removed, "remaining": r.remaining,
                 "expectancy": r.expectancy, "change": r.change,
                 "relative_change": r.relative_change, "survives": r.survives}
                for r in self.trade_removal
            ],
            "cost_breaks_at": self.cost_breaks_at,
            "delay_breaks_at": self.delay_breaks_at,
            "outlier_dependent": self.outlier_dependent,
        }
