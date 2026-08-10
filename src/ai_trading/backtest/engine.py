"""Backtesting engine."""

from __future__ import annotations

from typing import Any


class Backtester:
    """Simulates strategy execution over historical data.

    Accounts for slippage/commission and guards against lookahead bias
    (only data available before a decision may be used). Reports metrics such
    as Sharpe, Sortino, CAGR, max drawdown, win rate, and profit factor.
    """

    def run(self, strategy: Any, data: Any) -> dict[str, float]:
        """Run the backtest and return a metrics summary."""
        raise NotImplementedError
