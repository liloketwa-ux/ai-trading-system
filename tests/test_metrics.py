"""Tests for backtest performance metrics, verified against hand-computed values."""

import numpy as np
import pandas as pd
import pytest

from ai_trading.backtest import metrics as m


def test_sharpe_matches_manual_computation():
    returns = pd.Series([0.01, -0.005, 0.02, 0.0, 0.015])
    expected = returns.mean() / returns.std(ddof=1) * np.sqrt(252)
    assert m.sharpe_ratio(returns, 252) == pytest.approx(expected)


def test_sharpe_is_nan_for_flat_returns():
    assert np.isnan(m.sharpe_ratio(pd.Series([0.01] * 5), 252))


def test_sharpe_is_nan_with_insufficient_data():
    assert np.isnan(m.sharpe_ratio(pd.Series([0.01]), 252))


def test_risk_free_rate_reduces_sharpe():
    returns = pd.Series([0.01, 0.02, 0.015, 0.005])
    assert m.sharpe_ratio(returns, 252, risk_free_rate=0.05) < m.sharpe_ratio(returns, 252)


def test_sortino_ignores_upside_volatility():
    """Two series with identical downside but different upside: same denominator,
    so the one with more upside must score higher."""
    mild = pd.Series([0.01, -0.01, 0.01, -0.01])
    wild = pd.Series([0.05, -0.01, 0.05, -0.01])
    assert m.sortino_ratio(wild, 252) > m.sortino_ratio(mild, 252)


def test_sortino_is_nan_without_downside():
    assert np.isnan(m.sortino_ratio(pd.Series([0.01, 0.02, 0.03]), 252))


def test_max_drawdown_known_curve():
    # Peak 120 -> trough 60 is a 50% decline.
    equity = pd.Series([100.0, 120.0, 90.0, 60.0, 110.0])
    assert m.max_drawdown(equity) == pytest.approx(0.5)


def test_max_drawdown_zero_for_monotonic_curve():
    assert m.max_drawdown(pd.Series([100.0, 101.0, 105.0])) == pytest.approx(0.0)


def test_cagr_doubling_in_one_year():
    equity = pd.Series([100.0] + [0.0] * 251)
    equity.iloc[-1] = 200.0
    equity = pd.Series(np.linspace(100.0, 200.0, 253))
    assert m.cagr(equity, 252) == pytest.approx(1.0, rel=1e-6)


def test_win_rate_and_profit_factor():
    pnls = [10.0, -5.0, 20.0, -5.0]
    assert m.win_rate(pnls) == pytest.approx(0.5)
    assert m.profit_factor(pnls) == pytest.approx(30.0 / 10.0)


def test_profit_factor_infinite_without_losses():
    assert m.profit_factor([1.0, 2.0]) == float("inf")


def test_metrics_are_nan_with_no_trades():
    assert np.isnan(m.win_rate([]))
    assert np.isnan(m.profit_factor([]))


def test_summarize_reports_all_keys():
    equity = pd.Series(
        np.linspace(100.0, 130.0, 50),
        index=pd.date_range("2024-01-01", periods=50, freq="D"),
    )
    summary = m.summarize(equity, [5.0, -2.0], 252)
    assert set(summary) == {
        "total_return",
        "cagr",
        "sharpe",
        "sortino",
        "max_drawdown",
        "win_rate",
        "profit_factor",
        "num_trades",
    }
    assert summary["total_return"] == pytest.approx(0.3)
    assert summary["num_trades"] == 2.0
