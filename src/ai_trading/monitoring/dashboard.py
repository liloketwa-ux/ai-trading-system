"""The monitor: thresholds, health, and event emission.

Ties performance tracking, drift detection, and backtest divergence together
behind one object that a running system can call on each bar. It decides
nothing about trading — it observes and emits events. Acting on a CRITICAL
event (engaging the kill switch, flattening) stays an explicit decision in the
execution layer, so monitoring can never surprise the trading path.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .divergence import DivergenceReport, compare_to_backtest
from .drift import PSI_SHIFTED, PSI_STABLE, drift_report
from .events import EventLog, Severity
from .performance import PerformanceTracker

__all__ = ["MonitorThresholds", "Monitor"]


@dataclass(frozen=True)
class MonitorThresholds:
    """Levels at which the monitor escalates.

    Attributes:
        drawdown_warning: Drawdown fraction that raises a WARNING.
        drawdown_critical: Drawdown fraction that raises a CRITICAL.
        psi_warning: PSI above which a feature is flagged as moderately shifted.
        psi_critical: PSI above which a feature is flagged as shifted.
        divergence_alpha: Significance level for the backtest comparison.
        min_rolling_sharpe: Rolling Sharpe below which decay is flagged.
            ``None`` disables the check.
    """

    drawdown_warning: float = 0.10
    drawdown_critical: float = 0.15
    psi_warning: float = PSI_STABLE
    psi_critical: float = PSI_SHIFTED
    divergence_alpha: float = 0.05
    min_rolling_sharpe: float | None = 0.0

    def __post_init__(self) -> None:
        if not 0.0 < self.drawdown_warning < self.drawdown_critical <= 1.0:
            raise ValueError("require 0 < drawdown_warning < drawdown_critical <= 1")
        if not 0.0 <= self.psi_warning < self.psi_critical:
            raise ValueError("require 0 <= psi_warning < psi_critical")
        if not 0.0 < self.divergence_alpha < 1.0:
            raise ValueError("divergence_alpha must be in (0, 1)")


class Monitor:
    """Observes a running system and emits events when thresholds are breached.

    Args:
        thresholds: Escalation levels.
        periods_per_year: Bars per year, for annualized metrics.
    """

    def __init__(
        self,
        thresholds: MonitorThresholds | None = None,
        *,
        periods_per_year: int = 252,
    ) -> None:
        self.thresholds = thresholds or MonitorThresholds()
        self.performance = PerformanceTracker(periods_per_year)
        self.log = EventLog()

    # -- observation -------------------------------------------------------

    def record_equity(self, timestamp: pd.Timestamp, equity: float) -> None:
        """Record equity and escalate if drawdown has breached a threshold."""
        self.performance.record(timestamp, equity)
        drawdown = self.performance.current_drawdown
        t = self.thresholds

        if drawdown >= t.drawdown_critical:
            self.log.emit(
                "drawdown",
                Severity.CRITICAL,
                f"drawdown {drawdown:.2%} at or beyond critical {t.drawdown_critical:.2%}",
                timestamp=timestamp,
                drawdown=drawdown,
            )
        elif drawdown >= t.drawdown_warning:
            self.log.emit(
                "drawdown",
                Severity.WARNING,
                f"drawdown {drawdown:.2%} at or beyond warning {t.drawdown_warning:.2%}",
                timestamp=timestamp,
                drawdown=drawdown,
            )

    def record_trade(self, pnl: float) -> None:
        """Record a closed trade's realized PnL."""
        self.performance.record_trade(pnl)

    # -- checks ------------------------------------------------------------

    def check_drift(
        self,
        reference: pd.DataFrame,
        current: pd.DataFrame,
        *,
        timestamp: pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        """Run a drift report and emit an event per shifted feature."""
        report = drift_report(reference, current)
        t = self.thresholds
        for feature, row in report.iterrows():
            psi = float(row["psi"])
            if psi >= t.psi_critical:
                severity = Severity.CRITICAL
            elif psi >= t.psi_warning:
                severity = Severity.WARNING
            else:
                continue
            self.log.emit(
                "drift",
                severity,
                f"feature '{feature}' shifted (PSI {psi:.3f}, KS p={row['ks_p_value']:.4f})",
                timestamp=timestamp,
                psi=psi,
                ks_p_value=float(row["ks_p_value"]),
            )
        return report

    def check_divergence(
        self,
        backtest_returns: pd.Series,
        *,
        timestamp: pd.Timestamp | None = None,
    ) -> DivergenceReport | None:
        """Compare recorded live returns against backtested returns.

        Returns ``None`` when there is not yet enough overlapping history.
        """
        live = self.performance.returns
        if len(live) < 2:
            return None
        try:
            report = compare_to_backtest(
                live,
                backtest_returns,
                periods_per_year=self.performance.periods_per_year,
                alpha=self.thresholds.divergence_alpha,
            )
        except ValueError:
            return None

        if report.verdict == "underperforming":
            self.log.emit(
                "divergence",
                Severity.WARNING,
                f"live trails backtest: {report.summary()}",
                timestamp=timestamp,
                annualized_difference=report.annualized_difference,
                p_value=report.p_value,
            )
        elif report.verdict == "outperforming":
            self.log.emit(
                "divergence",
                Severity.INFO,
                f"live leads backtest: {report.summary()}",
                timestamp=timestamp,
                annualized_difference=report.annualized_difference,
                p_value=report.p_value,
            )
        return report

    def check_performance_decay(
        self, window: int = 60, *, timestamp: pd.Timestamp | None = None
    ) -> float | None:
        """Flag a rolling Sharpe that has fallen below the configured floor."""
        floor = self.thresholds.min_rolling_sharpe
        if floor is None:
            return None
        rolling = self.performance.rolling_sharpe(window)
        if rolling.empty or pd.isna(rolling.iloc[-1]):
            return None

        latest = float(rolling.iloc[-1])
        if latest < floor:
            self.log.emit(
                "performance_decay",
                Severity.WARNING,
                f"rolling Sharpe ({window} bars) {latest:.2f} below floor {floor:.2f}",
                timestamp=timestamp,
                rolling_sharpe=latest,
            )
        return latest

    # -- reporting ---------------------------------------------------------

    @property
    def healthy(self) -> bool:
        """False once anything CRITICAL has been recorded."""
        worst = self.log.worst_severity
        return worst is None or worst < Severity.CRITICAL

    def snapshot(self) -> dict[str, object]:
        """Current state, suitable for a dashboard row or a status log line."""
        metrics = self.performance.metrics()
        return {
            "equity": self.performance.current_equity or float("nan"),
            "peak_equity": self.performance.peak_equity or float("nan"),
            "drawdown": self.performance.current_drawdown,
            "sharpe": metrics.get("sharpe", float("nan")),
            "max_drawdown": metrics.get("max_drawdown", float("nan")),
            "num_trades": metrics.get("num_trades", 0.0),
            "events": len(self.log),
            "healthy": self.healthy,
        }
