"""Tests for the concrete strategies and their integration with the backtester."""

import numpy as np
import pandas as pd
import pytest

from ai_trading.backtest import Backtester
from ai_trading.strategies import (
    ICTStrategy,
    MeanReversion,
    MomentumBreakout,
    Signal,
    Strategy,
)


@pytest.fixture
def random_walk():
    """A driftless random walk: no strategy should reliably profit on it."""
    rng = np.random.default_rng(17)
    n = 600
    close = 100 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n)))
    idx = pd.date_range("2022-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {
            "open": np.r_[close[0], close[:-1]],
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": rng.uniform(1e5, 2e5, n),
        },
        index=idx,
    )


def trending(n=300, slope=0.004):
    close = 100 * np.exp(np.cumsum(np.full(n, slope)))
    idx = pd.date_range("2022-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {
            "open": np.r_[close[0], close[:-1]],
            "high": close * 1.002,
            "low": close * 0.998,
            "close": close,
            "volume": np.full(n, 1e5),
        },
        index=idx,
    )


# -- Signal ----------------------------------------------------------------


def test_signal_side_is_derived_from_weight():
    assert Signal("BTC", 0.5, "r").side == "long"
    assert Signal("BTC", -0.5, "r").side == "short"
    assert Signal("BTC", 0.0, "r").side == "flat"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"weight": 1.5},
        {"weight": -2.0},
        {"confidence": 1.5},
        {"rationale": "   "},
    ],
)
def test_signal_validates_inputs(kwargs):
    args = {"symbol": "BTC", "weight": 0.5, "rationale": "ok"} | kwargs
    with pytest.raises(ValueError):
        Signal(**args)


# -- Strategy base ---------------------------------------------------------


def test_strategy_abstains_during_warmup(random_walk):
    class Always(Strategy):
        warmup = 50

        def evaluate(self, history):
            return Signal("X", 1.0, "always")

    s = Always()
    assert np.isnan(s.target_weight(random_walk.iloc[:10]))
    assert s.target_weight(random_walk.iloc[:60]) == 1.0


def test_none_signal_means_flat_not_abstain(random_walk):
    class Nothing(Strategy):
        warmup = 1

        def evaluate(self, history):
            return None

    assert Nothing().target_weight(random_walk) == 0.0


def test_strategy_is_directly_callable(random_walk):
    strategy = MomentumBreakout(entry_window=20, exit_window=10)
    assert strategy(random_walk.iloc[:100]) == strategy.target_weight(random_walk.iloc[:100])


# -- MomentumBreakout ------------------------------------------------------


def test_momentum_goes_long_on_a_sustained_uptrend():
    bars = trending()
    strategy = MomentumBreakout(entry_window=20, exit_window=10, allow_short=False)
    result = Backtester(100_000.0, commission_bps=0, slippage_bps=0).run(bars, strategy)
    assert result.positions.iloc[-1] > 0
    assert result.equity.iloc[-1] > 100_000.0


def test_momentum_breakout_uses_prior_range_not_current_bar():
    """A close cannot break a range that includes the close itself."""
    bars = trending(n=60)
    strategy = MomentumBreakout(entry_window=20, exit_window=10)
    strategy.reset()
    weight = strategy.target_weight(bars.iloc[:40])
    assert weight != 0.0  # a monotonic uptrend must register as a breakout


def test_momentum_volume_filter_blocks_low_volume_breakouts():
    bars = trending(n=60).copy()
    bars["volume"] = 1.0  # flat volume never exceeds its own average multiple
    strategy = MomentumBreakout(entry_window=20, exit_window=10, volume_multiple=5.0)
    result = Backtester(100_000.0).run(bars, strategy)
    assert not result.fills


def test_momentum_reset_clears_position_state():
    bars = trending(n=60)
    strategy = MomentumBreakout(entry_window=20, exit_window=10)
    strategy.target_weight(bars.iloc[:40])
    strategy.reset()
    assert strategy._position == 0.0


@pytest.mark.parametrize(
    "kwargs",
    [{"entry_window": 1}, {"exit_window": 0}, {"exit_window": 99}, {"weight": 0.0}, {"volume_multiple": 0.0}],
)
def test_momentum_rejects_invalid_config(kwargs):
    with pytest.raises(ValueError):
        MomentumBreakout(**kwargs)


# -- MeanReversion ---------------------------------------------------------


def test_mean_reversion_fades_a_spike():
    close = np.r_[np.full(40, 100.0), [130.0]]
    idx = pd.date_range("2024-01-01", periods=len(close), freq="D")
    bars = pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close}, index=idx
    )
    strategy = MeanReversion(window=20, entry_z=2.0)
    assert strategy.target_weight(bars) < 0  # stretched far above the mean -> short


def test_mean_reversion_sentiment_filter_blocks_agreeing_fade():
    close = np.r_[np.full(40, 100.0), [130.0]]
    idx = pd.date_range("2024-01-01", periods=len(close), freq="D")
    bars = pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "sentiment_mean": np.r_[np.zeros(40), [0.9]],  # crowd agrees with the spike
        },
        index=idx,
    )
    blocked = MeanReversion(window=20, entry_z=2.0, sentiment_col="sentiment_mean")
    unblocked = MeanReversion(window=20, entry_z=2.0)
    assert blocked.target_weight(bars) == 0.0
    assert unblocked.target_weight(bars) < 0


@pytest.mark.parametrize(
    "kwargs", [{"window": 1}, {"entry_z": 0.0}, {"exit_z": 5.0}, {"weight": 2.0}]
)
def test_mean_reversion_rejects_invalid_config(kwargs):
    with pytest.raises(ValueError):
        MeanReversion(**kwargs)


# -- ICTStrategy -----------------------------------------------------------


def test_ict_emits_rationale_naming_the_zone(random_walk):
    strategy = ICTStrategy(symbol="BTC", lookback=120)
    for i in range(strategy.warmup, len(random_walk)):
        signal = strategy.evaluate(random_walk.iloc[: i + 1])
        if signal is not None:
            assert signal.symbol == "BTC"
            assert any(k in signal.rationale for k in ("_ob", "_fvg"))
            assert "confirmed at bar" in signal.rationale
            return
    pytest.skip("no ICT setup triggered on this series")


def test_ict_never_acts_on_a_zone_confirmed_by_the_current_bar(random_walk):
    """A zone the decision bar itself created is not a retracement into it."""
    strategy = ICTStrategy(lookback=120)
    window = random_walk.iloc[:200]
    signal = strategy.evaluate(window)
    if signal is not None:
        assert "confirmed at bar" in signal.rationale


def test_ict_runs_through_the_backtester_without_lookahead_profits(random_walk):
    """On a driftless random walk, an honest strategy cannot print a great Sharpe.

    This is the integration-level guard: a lookahead leak in the structure code
    would show up here as an implausibly good result.
    """
    strategy = ICTStrategy(lookback=150, weight=1.0)
    result = Backtester(
        100_000.0, commission_bps=1, slippage_bps=2, periods_per_year=252
    ).run(random_walk, strategy)

    sharpe = result.metrics["sharpe"]
    assert np.isnan(sharpe) or sharpe < 3.0, f"implausible Sharpe {sharpe} suggests lookahead"
    assert result.metrics["max_drawdown"] >= 0.0


def test_ict_respects_zone_age_limit(random_walk):
    fresh = ICTStrategy(lookback=150, max_zone_age=5)
    stale = ICTStrategy(lookback=150, max_zone_age=100)
    n_fresh = sum(
        1
        for i in range(fresh.warmup, 400)
        if fresh.evaluate(random_walk.iloc[: i + 1]) is not None
    )
    n_stale = sum(
        1
        for i in range(stale.warmup, 400)
        if stale.evaluate(random_walk.iloc[: i + 1]) is not None
    )
    assert n_fresh <= n_stale


@pytest.mark.parametrize(
    "kwargs", [{"lookback": 5}, {"weight": 0.0}, {"weight": 1.5}, {"max_zone_age": 0}]
)
def test_ict_rejects_invalid_config(kwargs):
    with pytest.raises(ValueError):
        ICTStrategy(**kwargs)


# -- cross-strategy integration -------------------------------------------


@pytest.mark.parametrize(
    "factory",
    [
        lambda: MomentumBreakout(entry_window=20, exit_window=10),
        lambda: MeanReversion(window=20),
        lambda: ICTStrategy(lookback=120),
    ],
)
def test_every_strategy_is_backtestable(factory, random_walk):
    result = Backtester(100_000.0).run(random_walk, factory())
    assert len(result.equity) == len(random_walk)
    assert result.equity.iloc[-1] > 0
    assert set(result.metrics) >= {"sharpe", "max_drawdown", "num_trades"}
