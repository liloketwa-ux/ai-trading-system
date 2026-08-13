"""Tests for prop-firm challenge adjudication."""

import numpy as np
import pandas as pd
import pytest

from ai_trading.backtest import (
    ChallengeRules,
    DrawdownType,
    Outcome,
    evaluate_challenge,
)


def curve(values, start="2024-01-01", freq="D"):
    return pd.Series(
        [float(v) for v in values], index=pd.date_range(start, periods=len(values), freq=freq)
    )


BASE = ChallengeRules(
    profit_target=0.10,
    max_daily_loss=0.05,
    max_drawdown=0.10,
    min_trading_days=0,
    max_days=None,
)


# -- passing ---------------------------------------------------------------


def test_reaching_the_target_passes():
    result = evaluate_challenge(curve([100_000, 104_000, 108_000, 110_500]), BASE)
    assert result.passed
    assert result.outcome is Outcome.PASSED
    assert result.return_pct == pytest.approx(0.105)


def test_result_is_truthy_when_passed():
    assert evaluate_challenge(curve([100_000, 111_000]), BASE)


def test_min_trading_days_blocks_an_early_pass():
    rules = ChallengeRules(min_trading_days=5, max_days=None)
    # Target hit on day 2, but only two trading days have elapsed.
    result = evaluate_challenge(curve([100_000, 111_000, 111_500]), rules)
    assert not result.passed


def test_pass_once_min_trading_days_satisfied():
    rules = ChallengeRules(min_trading_days=3, max_days=None)
    result = evaluate_challenge(curve([100_000, 102_000, 104_000, 111_000]), rules)
    assert result.passed
    assert result.trading_days >= 3


# -- daily loss ------------------------------------------------------------


def test_daily_loss_breach_fails():
    # Day 2 falls 6% from the prior close, past the 5% limit.
    result = evaluate_challenge(curve([100_000, 94_000]), BASE)
    assert result.outcome is Outcome.FAILED_DAILY_LOSS
    assert "daily loss" in result.detail


def test_daily_loss_resets_each_day():
    """A 4% loss three days running never breaches a 5% daily limit."""
    result = evaluate_challenge(curve([100_000, 96_000, 92_160, 88_474]), BASE)
    assert result.outcome is not Outcome.FAILED_DAILY_LOSS


def test_daily_loss_measured_from_start_of_day_not_from_peak():
    # Up to 120k, then back to 115k: -4.2% on the day, under the limit.
    result = evaluate_challenge(curve([100_000, 120_000, 115_000]), BASE)
    assert result.outcome is not Outcome.FAILED_DAILY_LOSS


def test_intraday_low_triggers_the_breach():
    """A dip through the limit fails even if the day closes recovered."""
    index = pd.to_datetime(["2024-01-01 00:00", "2024-01-02 12:00", "2024-01-02 23:00"])
    equity = pd.Series([100_000.0, 94_000.0, 99_500.0], index=index)
    assert evaluate_challenge(equity, BASE).outcome is Outcome.FAILED_DAILY_LOSS


# -- drawdown --------------------------------------------------------------


def test_static_drawdown_measured_from_initial_balance():
    rules = ChallengeRules(max_daily_loss=0.99, max_drawdown=0.10, min_trading_days=0, max_days=None)
    # Slow bleed to -11% without any single day breaching.
    result = evaluate_challenge(curve([100_000, 97_000, 94_000, 91_000, 88_500]), rules)
    assert result.outcome is Outcome.FAILED_DRAWDOWN


def test_trailing_drawdown_ratchets_up_behind_profit():
    """Trailing drawdown is stricter: it follows the peak and never gives ground."""
    rules = ChallengeRules(
        max_daily_loss=0.99,
        max_drawdown=0.10,
        drawdown_type=DrawdownType.TRAILING,
        min_trading_days=0,
        max_days=None,
    )
    # Peak 108k (short of the 110k target), fall to 96k: only -4% from the
    # start, but -11.1% from peak.
    path = curve([100_000, 108_000, 96_000])
    assert evaluate_challenge(path, rules).outcome is Outcome.FAILED_DRAWDOWN
    # The same path survives a static rule, which still measures from 100k.
    static = ChallengeRules(
        max_daily_loss=0.99, max_drawdown=0.10, min_trading_days=0, max_days=None
    )
    assert evaluate_challenge(path, static).outcome is not Outcome.FAILED_DRAWDOWN


# -- precedence and deadlines ---------------------------------------------


def test_breach_takes_precedence_over_hitting_the_target():
    """Target and breach on the same day: the account still fails."""
    index = pd.to_datetime(["2024-01-01 00:00", "2024-01-02 06:00", "2024-01-02 20:00"])
    equity = pd.Series([100_000.0, 94_000.0, 112_000.0], index=index)
    assert evaluate_challenge(equity, BASE).outcome is Outcome.FAILED_DAILY_LOSS


def test_deadline_fails_an_account_short_of_target():
    rules = ChallengeRules(profit_target=0.10, min_trading_days=0, max_days=3)
    result = evaluate_challenge(curve([100_000, 101_000, 102_000, 103_000]), rules)
    assert result.outcome is Outcome.FAILED_TIMEOUT
    assert result.days_elapsed == 3


def test_incomplete_when_data_runs_out():
    rules = ChallengeRules(profit_target=0.10, min_trading_days=0, max_days=None)
    assert evaluate_challenge(curve([100_000, 101_000]), rules).outcome is Outcome.INCOMPLETE


# -- bookkeeping -----------------------------------------------------------


def test_worst_metrics_are_reported():
    result = evaluate_challenge(curve([100_000, 97_000, 99_000, 111_000]), BASE)
    assert result.worst_daily_loss == pytest.approx(0.03)
    assert result.worst_drawdown == pytest.approx(0.03)
    assert result.peak_equity == pytest.approx(111_000.0)


def test_explicit_initial_balance_overrides_first_value():
    # Returns are measured against the stated balance, not the first observation.
    result = evaluate_challenge(curve([99_000, 101_000]), BASE, initial_balance=100_000)
    assert result.return_pct == pytest.approx(0.01)


def test_starting_below_the_stated_balance_can_breach_immediately():
    """Opening 5% down against a 100k balance breaches a 5% daily limit on day 1."""
    result = evaluate_challenge(curve([95_000, 96_000]), BASE, initial_balance=100_000)
    assert result.outcome is Outcome.FAILED_DAILY_LOSS
    assert result.days_elapsed == 1


def test_summary_is_readable():
    assert "passed" in evaluate_challenge(curve([100_000, 111_000]), BASE).summary()


# -- validation ------------------------------------------------------------


def test_rejects_empty_series():
    with pytest.raises(ValueError, match="empty"):
        evaluate_challenge(pd.Series([], dtype="float64"), BASE)


def test_rejects_non_datetime_index():
    with pytest.raises(TypeError, match="DatetimeIndex"):
        evaluate_challenge(pd.Series([100_000.0, 101_000.0]), BASE)


def test_rejects_unsorted_index():
    equity = pd.Series([1.0, 2.0], index=pd.to_datetime(["2024-01-02", "2024-01-01"]))
    with pytest.raises(ValueError, match="sorted"):
        evaluate_challenge(equity, BASE)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"profit_target": 0.0},
        {"max_daily_loss": 0.0},
        {"max_daily_loss": 1.5},
        {"max_drawdown": 0.0},
        {"min_trading_days": -1},
        {"max_days": 0},
    ],
)
def test_rules_reject_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        ChallengeRules(**kwargs)
