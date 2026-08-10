"""Performance metrics for backtest results.

Return-based metrics take a series of **per-period** returns and an explicit
``periods_per_year`` for annualization (252 for daily bars, 8760 for hourly,
365 for daily crypto, and so on). Getting that constant wrong silently rescales
Sharpe, so it is always required rather than guessed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "sharpe_ratio",
    "sortino_ratio",
    "max_drawdown",
    "cagr",
    "win_rate",
    "profit_factor",
    "summarize",
]


def sharpe_ratio(
    returns: pd.Series,
    periods_per_year: int,
    risk_free_rate: float = 0.0,
) -> float:
    """Annualized Sharpe ratio.

    ``risk_free_rate`` is an annual rate, converted to per-period internally.
    Returns ``nan`` when there is no variance to measure (fewer than two
    observations, or a flat series).
    """
    excess = _excess(returns, periods_per_year, risk_free_rate)
    if len(excess) < 2:
        return float("nan")
    std = excess.std(ddof=1)
    if std == 0 or np.isnan(std):
        return float("nan")
    return float(excess.mean() / std * np.sqrt(periods_per_year))


def sortino_ratio(
    returns: pd.Series,
    periods_per_year: int,
    risk_free_rate: float = 0.0,
) -> float:
    """Annualized Sortino ratio (downside deviation instead of total volatility).

    Downside deviation is the root-mean-square of returns below zero, taken over
    *all* observations. Returns ``nan`` when there is no downside (an undefined
    ratio) or fewer than two observations.
    """
    excess = _excess(returns, periods_per_year, risk_free_rate)
    if len(excess) < 2:
        return float("nan")
    downside = excess.clip(upper=0.0)
    downside_dev = float(np.sqrt((downside**2).mean()))
    if downside_dev == 0 or np.isnan(downside_dev):
        return float("nan")
    return float(excess.mean() / downside_dev * np.sqrt(periods_per_year))


def max_drawdown(equity: pd.Series) -> float:
    """Largest peak-to-trough decline, as a **positive** fraction (0.2 = -20%).

    Returns 0.0 for a series that never declines.
    """
    equity = equity.dropna()
    if equity.empty:
        return float("nan")
    running_peak = equity.cummax()
    drawdowns = 1.0 - equity / running_peak
    return float(max(0.0, drawdowns.max()))


def cagr(equity: pd.Series, periods_per_year: int) -> float:
    """Compound annual growth rate implied by an equity curve.

    Returns ``nan`` if the curve is too short or starts at or below zero.
    """
    equity = equity.dropna()
    if len(equity) < 2:
        return float("nan")
    start, end = float(equity.iloc[0]), float(equity.iloc[-1])
    if start <= 0:
        return float("nan")
    years = (len(equity) - 1) / periods_per_year
    if years <= 0:
        return float("nan")
    if end <= 0:
        return -1.0
    return float((end / start) ** (1.0 / years) - 1.0)


def win_rate(trade_pnls: pd.Series | list[float]) -> float:
    """Fraction of closed trades with positive PnL. ``nan`` with no trades."""
    pnls = pd.Series(list(trade_pnls), dtype="float64").dropna()
    if pnls.empty:
        return float("nan")
    return float((pnls > 0).sum() / len(pnls))


def profit_factor(trade_pnls: pd.Series | list[float]) -> float:
    """Gross profit divided by gross loss.

    Returns ``inf`` when there are winners but no losers, and ``nan`` when there
    are no trades at all.
    """
    pnls = pd.Series(list(trade_pnls), dtype="float64").dropna()
    if pnls.empty:
        return float("nan")
    gross_profit = float(pnls[pnls > 0].sum())
    gross_loss = float(-pnls[pnls < 0].sum())
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else float("nan")
    return gross_profit / gross_loss


def summarize(
    equity: pd.Series,
    trade_pnls: pd.Series | list[float],
    periods_per_year: int,
    risk_free_rate: float = 0.0,
) -> dict[str, float]:
    """Full metrics summary for an equity curve and its closed trades."""
    returns = equity.pct_change().dropna()
    return {
        "total_return": _total_return(equity),
        "cagr": cagr(equity, periods_per_year),
        "sharpe": sharpe_ratio(returns, periods_per_year, risk_free_rate),
        "sortino": sortino_ratio(returns, periods_per_year, risk_free_rate),
        "max_drawdown": max_drawdown(equity),
        "win_rate": win_rate(trade_pnls),
        "profit_factor": profit_factor(trade_pnls),
        "num_trades": float(len(list(trade_pnls))),
    }


def _total_return(equity: pd.Series) -> float:
    equity = equity.dropna()
    if len(equity) < 2 or float(equity.iloc[0]) == 0:
        return float("nan")
    return float(equity.iloc[-1] / equity.iloc[0] - 1.0)


def _excess(returns: pd.Series, periods_per_year: int, risk_free_rate: float) -> pd.Series:
    if periods_per_year <= 0:
        raise ValueError(f"periods_per_year must be > 0, got {periods_per_year}")
    per_period_rf = risk_free_rate / periods_per_year
    return pd.Series(returns, dtype="float64").dropna() - per_period_rf
