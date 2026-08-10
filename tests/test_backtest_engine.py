"""Tests for the backtesting engine.

The most important tests here are the lookahead-safety ones: they assert the
engine's causality guarantee directly rather than trusting it by convention.
"""

import numpy as np
import pandas as pd
import pytest

from ai_trading.backtest import Backtester


def make_bars(closes, opens=None, start="2024-01-01"):
    idx = pd.date_range(start, periods=len(closes), freq="D")
    opens = list(closes) if opens is None else list(opens)
    return pd.DataFrame(
        {
            "open": opens,
            "high": [max(o, c) for o, c in zip(opens, closes)],
            "low": [min(o, c) for o, c in zip(opens, closes)],
            "close": list(closes),
            "volume": [1_000.0] * len(closes),
        },
        index=idx,
    )


# -- lookahead safety ------------------------------------------------------


def test_strategy_only_ever_sees_history_up_to_decision_bar():
    """The slice handed to the strategy must end exactly at the decision bar."""
    bars = make_bars([100.0, 101.0, 102.0, 103.0, 104.0])
    seen = []

    def signal_fn(history):
        seen.append((len(history), history.index[-1]))
        return 0.0

    Backtester(10_000.0).run(bars, signal_fn)

    # One decision per bar except the last (nothing left to fill against).
    assert len(seen) == len(bars) - 1
    for i, (length, last_ts) in enumerate(seen):
        assert length == i + 1
        assert last_ts == bars.index[i]


def test_future_bars_cannot_influence_earlier_decisions():
    """Changing only the final bar must not alter any earlier fill."""
    base = [100.0, 101.0, 102.0, 103.0, 104.0]

    def momentum(history):
        return 1.0 if len(history) >= 2 and history["close"].iloc[-1] > history["close"].iloc[-2] else 0.0

    first = Backtester(10_000.0).run(make_bars(base), momentum)
    altered = Backtester(10_000.0).run(make_bars(base[:-1] + [500.0]), momentum)

    # Every fill before the final bar must be identical.
    for a, b in zip(first.fills[:-1], altered.fills[:-1]):
        assert a.timestamp == b.timestamp
        assert a.units == pytest.approx(b.units)
        assert a.price == pytest.approx(b.price)


def test_order_fills_at_next_bar_open_not_current_close():
    bars = make_bars(closes=[100.0, 200.0, 300.0], opens=[100.0, 150.0, 250.0])

    result = Backtester(10_000.0, commission_bps=0, slippage_bps=0).run(
        bars, lambda h: 1.0 if len(h) == 1 else 0.0
    )

    # Decision on bar 0 fills at bar 1's OPEN (150), not bar 0's close (100).
    assert result.fills[0].timestamp == bars.index[1]
    assert result.fills[0].price == pytest.approx(150.0)


def test_final_bar_signal_is_never_executed():
    bars = make_bars([100.0, 100.0, 100.0])
    result = Backtester(10_000.0).run(bars, lambda h: 1.0)
    # 3 bars -> decisions on bars 0 and 1 only; the bar-2 signal has no successor.
    assert all(f.timestamp <= bars.index[-1] for f in result.fills)
    assert len(result.fills) <= 2


# -- accounting ------------------------------------------------------------


def test_flat_strategy_preserves_capital_exactly():
    result = Backtester(10_000.0).run(make_bars([100.0, 110.0, 90.0, 105.0]), lambda h: 0.0)
    assert result.equity.iloc[-1] == pytest.approx(10_000.0)
    assert not result.fills
    assert not result.trades


def test_long_position_gains_on_rising_prices():
    bars = make_bars([100.0, 100.0, 120.0])
    result = Backtester(10_000.0, commission_bps=0, slippage_bps=0).run(bars, lambda h: 1.0)
    assert result.equity.iloc[-1] > 10_000.0


def test_short_position_gains_on_falling_prices():
    bars = make_bars([100.0, 100.0, 80.0])
    result = Backtester(10_000.0, commission_bps=0, slippage_bps=0).run(bars, lambda h: -1.0)
    assert result.equity.iloc[-1] > 10_000.0


def test_costs_always_work_against_the_trader():
    bars = make_bars([100.0, 100.0, 100.0, 100.0])
    free = Backtester(10_000.0, commission_bps=0, slippage_bps=0).run(bars, lambda h: 1.0)
    costly = Backtester(10_000.0, commission_bps=10, slippage_bps=10).run(bars, lambda h: 1.0)
    assert costly.equity.iloc[-1] < free.equity.iloc[-1]


def test_buy_fill_price_is_worse_than_reference_and_sell_is_too():
    bars = make_bars(closes=[100.0, 100.0, 100.0], opens=[100.0, 100.0, 100.0])
    result = Backtester(10_000.0, commission_bps=10, slippage_bps=10).run(
        bars, lambda h: 1.0 if len(h) == 1 else 0.0
    )
    buy, sell = result.fills[0], result.fills[1]
    assert buy.units > 0 and buy.price > 100.0  # paid up
    assert sell.units < 0 and sell.price < 100.0  # received less


def test_equity_curve_aligns_with_bars():
    bars = make_bars([100.0, 101.0, 102.0])
    result = Backtester(10_000.0).run(bars, lambda h: 0.5)
    assert len(result.equity) == len(bars)
    assert result.equity.index.equals(bars.index)


# -- trade accounting ------------------------------------------------------


def test_round_trip_records_a_trade_with_correct_direction_and_pnl():
    bars = make_bars(closes=[100.0, 100.0, 120.0, 120.0], opens=[100.0, 100.0, 120.0, 120.0])

    def signal_fn(history):
        return 1.0 if len(history) == 1 else 0.0

    result = Backtester(10_000.0, commission_bps=0, slippage_bps=0).run(bars, signal_fn)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.direction == "long"
    assert trade.entry_price == pytest.approx(100.0)
    assert trade.exit_price == pytest.approx(120.0)
    assert trade.pnl == pytest.approx(trade.units * 20.0)


def test_short_round_trip_pnl_sign_is_correct():
    bars = make_bars(closes=[100.0, 100.0, 80.0, 80.0], opens=[100.0, 100.0, 80.0, 80.0])

    def signal_fn(history):
        return -1.0 if len(history) == 1 else 0.0

    result = Backtester(10_000.0, commission_bps=0, slippage_bps=0).run(bars, signal_fn)
    trade = result.trades[0]
    assert trade.direction == "short"
    assert trade.pnl > 0  # shorted at 100, covered at 80


def test_flipping_long_to_short_closes_then_reopens():
    bars = make_bars(closes=[100.0] * 5, opens=[100.0] * 5)

    def signal_fn(history):
        return 1.0 if len(history) <= 2 else -1.0

    result = Backtester(10_000.0, commission_bps=0, slippage_bps=0).run(bars, signal_fn)
    assert len(result.trades) == 1
    assert result.trades[0].direction == "long"
    assert result.positions.iloc[-1] < 0  # ended net short


# -- guards ----------------------------------------------------------------


def test_nan_signal_is_treated_as_no_decision():
    bars = make_bars([100.0, 101.0, 102.0])
    result = Backtester(10_000.0).run(bars, lambda h: float("nan"))
    assert not result.fills
    assert result.equity.iloc[-1] == pytest.approx(10_000.0)


def test_weight_is_clamped_to_max_weight():
    bars = make_bars(closes=[100.0, 100.0, 100.0], opens=[100.0, 100.0, 100.0])
    result = Backtester(10_000.0, commission_bps=0, slippage_bps=0, max_weight=1.0).run(
        bars, lambda h: 99.0
    )
    # 10_000 equity at price 100 -> at most 100 units under a 1.0 weight cap.
    assert result.positions.max() == pytest.approx(100.0)


def test_rejects_missing_columns():
    bad = pd.DataFrame({"close": [1.0, 2.0]}, index=pd.date_range("2024-01-01", periods=2))
    with pytest.raises(KeyError, match="open"):
        Backtester().run(bad, lambda h: 0.0)


def test_rejects_non_datetime_index():
    bad = pd.DataFrame({"open": [1.0, 2.0], "close": [1.0, 2.0]})
    with pytest.raises(TypeError, match="DatetimeIndex"):
        Backtester().run(bad, lambda h: 0.0)


def test_rejects_unsorted_index():
    idx = pd.to_datetime(["2024-01-02", "2024-01-01"])
    bad = pd.DataFrame({"open": [1.0, 2.0], "close": [1.0, 2.0]}, index=idx)
    with pytest.raises(ValueError, match="sorted"):
        Backtester().run(bad, lambda h: 0.0)


def test_rejects_non_positive_prices():
    with pytest.raises(ValueError, match="non-positive"):
        Backtester().run(make_bars([100.0, 0.0, 100.0]), lambda h: 0.0)


def test_rejects_too_few_bars():
    with pytest.raises(ValueError, match="at least 2 bars"):
        Backtester().run(make_bars([100.0]), lambda h: 0.0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"initial_capital": 0.0},
        {"commission_bps": -1.0},
        {"slippage_bps": -1.0},
        {"periods_per_year": 0},
        {"max_weight": 0.0},
    ],
)
def test_rejects_invalid_construction(kwargs):
    with pytest.raises(ValueError):
        Backtester(**kwargs)
