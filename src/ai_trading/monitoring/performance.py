"""Live performance tracking.

Accumulates an equity curve as the system runs and derives the same metrics the
backtester reports, so live and simulated results are directly comparable
rather than measured two different ways.
"""

from __future__ import annotations

import pandas as pd

from ..backtest import metrics as _metrics

__all__ = ["PerformanceTracker"]


class PerformanceTracker:
    """Records equity over time and reports risk-adjusted performance.

    Args:
        periods_per_year: Bars per year, used to annualize metrics. Must match
            the bar frequency being recorded or every annualized figure is
            silently rescaled.
    """

    def __init__(self, periods_per_year: int = 252) -> None:
        if periods_per_year <= 0:
            raise ValueError(f"periods_per_year must be > 0, got {periods_per_year}")
        self.periods_per_year = periods_per_year
        self._timestamps: list[pd.Timestamp] = []
        self._equity: list[float] = []
        self._trade_pnls: list[float] = []
        self._peak: float | None = None

    def record(self, timestamp: pd.Timestamp, equity: float) -> None:
        """Append an equity observation."""
        if equity <= 0:
            raise ValueError(f"equity must be > 0, got {equity}")
        if self._timestamps and timestamp < self._timestamps[-1]:
            raise ValueError("timestamps must be non-decreasing")
        self._timestamps.append(timestamp)
        self._equity.append(float(equity))
        self._peak = equity if self._peak is None else max(self._peak, equity)

    def record_trade(self, pnl: float) -> None:
        """Record a closed trade's realized PnL, for win rate and profit factor."""
        self._trade_pnls.append(float(pnl))

    @property
    def equity_curve(self) -> pd.Series:
        return pd.Series(self._equity, index=pd.Index(self._timestamps), name="equity")

    @property
    def returns(self) -> pd.Series:
        return self.equity_curve.pct_change().dropna()

    @property
    def current_equity(self) -> float | None:
        return self._equity[-1] if self._equity else None

    @property
    def peak_equity(self) -> float | None:
        return self._peak

    @property
    def current_drawdown(self) -> float:
        """Drawdown from peak as a positive fraction (0.15 = 15% below peak)."""
        if not self._equity or not self._peak:
            return 0.0
        return max(0.0, 1.0 - self._equity[-1] / self._peak)

    def metrics(self) -> dict[str, float]:
        """Full metrics summary, matching the backtester's definitions."""
        if len(self._equity) < 2:
            return {}
        return _metrics.summarize(
            self.equity_curve, self._trade_pnls, self.periods_per_year
        )

    def rolling_sharpe(self, window: int) -> pd.Series:
        """Rolling annualized Sharpe over ``window`` observations.

        Useful for spotting decay that a single full-sample figure hides.
        """
        if window < 2:
            raise ValueError(f"window must be >= 2, got {window}")
        returns = self.returns
        mean = returns.rolling(window).mean()
        std = returns.rolling(window).std(ddof=1)
        scale = self.periods_per_year**0.5
        return (mean / std * scale).where(std > 0).rename("rolling_sharpe")
