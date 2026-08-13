"""Regression tests for the ICT position-persistence bug (P0.1).

The bug: ``ICTStrategy.evaluate`` was stateless. It returned a ``Signal`` only
on bars where price sat inside a fresh unmitigated zone and ``None`` otherwise,
and ``Strategy.target_weight`` maps ``None`` to ``0.0`` -- *go flat*, not
*hold*. Every entry was therefore unwound on the following bar, giving a mean
holding period of 1.32 bars, 339x turnover in 30 days, roughly 17% of equity in
cost drag, and a 0% challenge pass rate.

The signal was never the problem: it fired on 25.4% of bars. These tests pin
the behaviour that was missing, so the bug cannot return silently.
"""

import numpy as np
import pandas as pd
import pytest

from ai_trading.backtest import Backtester, ChallengeRules, Outcome, evaluate_challenge
from ai_trading.strategies import ICTStrategy

BARS = 24 * 30
PPY = 24 * 365
SIGMA = 0.60 / np.sqrt(PPY)
RULES = ChallengeRules(
    profit_target=0.10, max_daily_loss=0.05, max_drawdown=0.10,
    min_trading_days=4, max_days=30,
)


def zero_drift_path(rng, n=BARS):
    """Geometric Brownian motion with no drift: no edge exists by construction."""
    close = 100 * np.exp(np.cumsum(rng.normal(0.0, SIGMA, n)))
    return pd.DataFrame(
        {
            "open": np.r_[close[0], close[:-1]],
            "high": close * 1.002,
            "low": close * 0.998,
            "close": close,
            "volume": np.full(n, 1e5),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="h"),
    )


class TurnoverMatchedRandom:
    """Random direction held for a while -- the honest baseline.

    Flipping every bar would pay hundreds of round trips of costs and lose on
    turnover rather than on lack of signal, which is not the comparison we want.
    """

    def __init__(self, rng, switch_prob=0.02):
        self.rng, self.p, self.pos = rng, switch_prob, 0.0

    def __call__(self, history):
        if len(history) < 30:
            return float("nan")
        if self.pos == 0.0 or self.rng.random() < self.p:
            self.pos = float(self.rng.choice([-1.0, 1.0]))
        return self.pos


def run(bars, strategy):
    return Backtester(
        100_000.0, commission_bps=2, slippage_bps=3, periods_per_year=PPY
    ).run(bars, strategy)


def turnover(result):
    """Gross traded notional as a multiple of starting equity."""
    return sum(abs(f.units * f.price) for f in result.fills) / 100_000.0


# -- the bug itself --------------------------------------------------------


def test_ict_actually_trades():
    """Zero trades would mean the signal never fires. It always did."""
    result = run(zero_drift_path(np.random.default_rng(4)), ICTStrategy(lookback=120))
    assert len(result.trades) > 0
    assert len(result.fills) > 0


def test_ict_holds_positions_across_multiple_bars():
    """The core regression: mean holding period must not collapse to ~1 bar."""
    bars = zero_drift_path(np.random.default_rng(4))
    strategy = ICTStrategy(lookback=120)

    weights = [strategy.target_weight(bars.iloc[: i + 1]) for i in range(len(bars))]
    held = pd.Series(weights).fillna(0.0) != 0.0

    runs, current = [], 0
    for flag in held:
        if flag:
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)

    assert runs, "strategy never held a position"
    assert np.mean(runs) > 3.0, (
        f"mean holding period {np.mean(runs):.2f} bars -- the persistence bug is back "
        "(it was 1.32 before the fix)"
    )


def test_ict_turnover_is_bounded():
    """339x turnover in 30 days was the cost-churn death spiral."""
    result = run(zero_drift_path(np.random.default_rng(4)), ICTStrategy(lookback=120))
    assert turnover(result) < 60.0, (
        f"turnover {turnover(result):.1f}x starting equity is churn, not trading"
    )


def test_ict_holds_until_the_zone_is_invalidated():
    """Once long, the position survives a bar that is not inside any zone."""
    strategy = ICTStrategy(lookback=120)
    bars = zero_drift_path(np.random.default_rng(4))

    entered_at = None
    for i in range(strategy.warmup, len(bars)):
        weight = strategy.target_weight(bars.iloc[: i + 1])
        if weight not in (0.0, float("nan")) and weight == weight:
            entered_at = i
            break
    assert entered_at is not None, "strategy never entered"

    # The next bar must still be held, not flattened.
    assert strategy.target_weight(bars.iloc[: entered_at + 2]) != 0.0


def test_reset_clears_held_position():
    strategy = ICTStrategy(lookback=120)
    bars = zero_drift_path(np.random.default_rng(4))
    for i in range(strategy.warmup, 200):
        if strategy.target_weight(bars.iloc[: i + 1]) != 0.0:
            break
    strategy.reset()
    assert strategy._position == 0.0
    assert strategy._entry_zone is None


def test_stop_buffer_is_validated():
    with pytest.raises(ValueError, match="stop_buffer"):
        ICTStrategy(stop_buffer=-1.0)


def test_wider_stop_buffer_holds_longer():
    bars = zero_drift_path(np.random.default_rng(4))
    tight = turnover(run(bars, ICTStrategy(lookback=120, stop_buffer=0.0)))
    loose = turnover(run(bars, ICTStrategy(lookback=120, stop_buffer=2.0)))
    assert loose <= tight, "a wider invalidation buffer should not increase turnover"


# -- the statistical acceptance criterion ----------------------------------


@pytest.mark.slow
def test_ict_pass_rate_is_comparable_to_a_turnover_matched_random_baseline():
    """On zero-drift data nothing has edge, so ICT must land near the baseline.

    A 0% pass rate against a baseline near 30% is the signature of the
    persistence bug. This is the acceptance criterion for the fix; it is marked
    slow because it needs enough paths for the interval to mean anything.
    """
    n_paths = 120

    def pass_rate(factory, seed):
        rng = np.random.default_rng(seed)
        passed = 0
        for _ in range(n_paths):
            result = run(zero_drift_path(rng), factory(rng))
            if evaluate_challenge(result.equity, RULES).outcome is Outcome.PASSED:
                passed += 1
        p = passed / n_paths
        return p, 1.96 * np.sqrt(p * (1 - p) / n_paths)

    ict, ict_ci = pass_rate(lambda rng: ICTStrategy(lookback=120), seed=4)
    baseline, base_ci = pass_rate(TurnoverMatchedRandom, seed=7)

    assert ict > 0.05, f"ICT pass rate {ict:.1%} -- the persistence bug is back"
    # Intervals must overlap: neither should beat the other on edgeless data.
    assert abs(ict - baseline) <= ict_ci + base_ci, (
        f"ICT {ict:.1%}±{ict_ci:.1%} vs random {baseline:.1%}±{base_ci:.1%} -- "
        "a difference on zero-drift data indicates a bug, not edge"
    )
