"""Backtesting engine and performance metrics (design-doc section 6).

The engine is structurally lookahead-safe: decisions for bar ``i`` see only
bars up to ``i`` and fill at bar ``i + 1``'s open.
"""

from .engine import BacktestResult, Backtester, Fill, SignalFn, Trade
from .metrics import (
    cagr,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
    summarize,
    win_rate,
)

__all__ = [
    "BacktestResult",
    "Backtester",
    "Fill",
    "SignalFn",
    "Trade",
    "cagr",
    "max_drawdown",
    "profit_factor",
    "sharpe_ratio",
    "sortino_ratio",
    "summarize",
    "win_rate",
]
