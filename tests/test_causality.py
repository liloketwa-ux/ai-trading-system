"""Mechanical look-ahead audit across every strategy and indicator (P0.3).

**On the test design.** The obvious test — shift the whole price series by one
bar and assert results change — does not discriminate. Shifting the entire
series just relabels it: a causal strategy makes the same decisions one index
later, so its results barely move (measured: 0.36% on a strategy the engine
provably feeds only past data). "Assert results change" would flag correct code.

Comparing *final equity* is also confounded. Mutating the last bar swings equity
by hundreds of percent on a provably causal strategy, because the open position
is marked to market at the mutated close — that is accounting, not leakage.

The discriminating test is this: **mutate bars from index K onward, then assert
the decision sequence strictly before K is byte-identical.** A strategy that
peeks at the future produces different earlier fills; a causal one cannot.
"""

import numpy as np
import pandas as pd
import pytest

from ai_trading.backtest import Backtester
from ai_trading.features import FeatureEngine
from ai_trading.features import indicators as ind
from ai_trading.strategies import ICTStrategy, MeanReversion, MomentumBreakout

MUTATION_POINT = 300


@pytest.fixture
def bars():
    rng = np.random.default_rng(11)
    n = 500
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
    return pd.DataFrame(
        {
            "open": np.r_[close[0], close[:-1]],
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": rng.uniform(1e5, 2e5, n),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="D"),
    )


# Factories receive the full frame under test. Honest strategies ignore it; a
# deliberate cheater uses it to peek, which is what makes the guard test valid.
STRATEGIES = {
    "momentum": lambda _bars: MomentumBreakout(entry_window=20, exit_window=10),
    "mean_reversion": lambda _bars: MeanReversion(window=20, entry_z=2.0),
    "ict": lambda _bars: ICTStrategy(lookback=120),
}


def fill_signature(bars, factory):
    """Fills as comparable tuples. A fresh strategy per run — these are stateful."""
    result = Backtester(100_000.0, commission_bps=2, slippage_bps=3).run(bars, factory(bars))
    return [
        (f.timestamp, round(f.units, 9), round(f.price, 9)) for f in result.fills
    ]


def mutate_future(bars, k=MUTATION_POINT, factor=3.0):
    """Scale every price from bar ``k`` onward. Bars before ``k`` are untouched."""
    out = bars.copy()
    for column in ("open", "high", "low", "close"):
        out.iloc[k:, out.columns.get_loc(column)] *= factor
    return out


@pytest.mark.parametrize("name", sorted(STRATEGIES))
def test_strategy_decisions_are_invariant_to_future_bars(bars, name):
    """No strategy may change a past decision when a future bar changes."""
    factory = STRATEGIES[name]
    cutoff = bars.index[MUTATION_POINT]

    original = [f for f in fill_signature(bars, factory) if f[0] < cutoff]
    mutated = [f for f in fill_signature(mutate_future(bars), factory) if f[0] < cutoff]

    assert original, f"{name} produced no fills before the mutation point"
    assert original == mutated, (
        f"{name} changed {sum(a != b for a, b in zip(original, mutated))} of "
        f"{len(original)} pre-mutation fills when only future bars moved -- "
        "this strategy is reading the future"
    )


@pytest.mark.parametrize("name", sorted(STRATEGIES))
def test_strategy_sees_only_history_up_to_the_decision_bar(bars, name):
    """The slice handed to a strategy must never extend past the decision bar."""
    seen = []
    strategy = STRATEGIES[name](bars)

    def spy(history):
        seen.append((len(history), history.index[-1]))
        return strategy.target_weight(history)

    Backtester(100_000.0).run(bars, spy)

    assert len(seen) == len(bars) - 1  # every bar but the last decides
    for i, (length, last_timestamp) in enumerate(seen):
        assert length == i + 1
        assert last_timestamp == bars.index[i]


@pytest.mark.parametrize(
    "indicator",
    [
        lambda s: ind.sma(s, 20),
        lambda s: ind.ema(s, 20),
        lambda s: ind.rsi(s, 14),
        lambda s: ind.momentum(s, 10),
        lambda s: ind.volatility(s, 20),
        lambda s: ind.zscore(s, 20),
    ],
    ids=["sma", "ema", "rsi", "momentum", "volatility", "zscore"],
)
def test_indicator_values_are_invariant_to_future_bars(bars, indicator):
    """An indicator value at bar t must not move when bar t+k changes."""
    original = indicator(bars["close"])
    mutated = indicator(mutate_future(bars)["close"])

    head_original = original.iloc[:MUTATION_POINT].dropna()
    head_mutated = mutated.iloc[:MUTATION_POINT].dropna()

    assert len(head_original) > 0
    assert np.allclose(head_original.to_numpy(), head_mutated.to_numpy(), equal_nan=True)


def test_feature_engine_rows_are_invariant_to_future_bars(bars):
    """The whole feature frame, not just individual indicators."""
    original = FeatureEngine().build(bars).iloc[:MUTATION_POINT]
    mutated = FeatureEngine().build(mutate_future(bars)).iloc[:MUTATION_POINT]

    for column in original.columns:
        a = original[column].to_numpy(dtype="float64")
        b = mutated[column].to_numpy(dtype="float64")
        assert np.allclose(a, b, equal_nan=True), f"feature '{column}' peeks at the future"


def test_the_audit_can_actually_catch_a_cheater(bars):
    """Guard the guard: a deliberately peeking strategy must fail this test.

    Without this, a bug that made the audit vacuous would go unnoticed and
    every strategy would "pass" by not being tested at all.
    """
    def make_cheater(frame):
        """Peeks five bars past the decision bar, in the frame actually under test."""
        closes = frame["close"]

        def cheater(history):
            future = min(len(history) + 5, len(closes) - 1)
            return 1.0 if closes.iloc[future] > history["close"].iloc[-1] else -1.0

        return cheater

    cutoff = bars.index[MUTATION_POINT]
    original = [f for f in fill_signature(bars, make_cheater) if f[0] < cutoff]
    mutated = [f for f in fill_signature(mutate_future(bars), make_cheater) if f[0] < cutoff]
    assert original != mutated, "the look-ahead audit failed to catch a known cheater"
