"""Tests for the paper broker and the order manager's risk gate."""

import pandas as pd
import pytest

from ai_trading.execution import (
    Broker,
    BrokerError,
    Order,
    OrderManager,
    OrderSide,
    OrderStatus,
    OrderType,
    PaperBroker,
    Position,
    TransientBrokerError,
    next_client_order_id,
)
from ai_trading.execution.broker import _apply_to_position
from ai_trading.risk import RiskLimits, RiskManager
from ai_trading.strategies import Signal


@pytest.fixture
def broker():
    b = PaperBroker(cash=100_000.0, commission_bps=0, slippage_bps=0)
    b.update_price("BTC", 100.0)
    return b


def market(symbol="BTC", side=OrderSide.BUY, qty=10.0):
    return Order(next_client_order_id(), symbol, side, qty, OrderType.MARKET)


# -- order validation ------------------------------------------------------


def test_order_rejects_non_positive_quantity():
    with pytest.raises(ValueError, match="quantity"):
        Order(next_client_order_id(), "BTC", OrderSide.BUY, 0.0)


def test_limit_order_requires_a_limit_price():
    with pytest.raises(ValueError, match="limit_price"):
        Order(next_client_order_id(), "BTC", OrderSide.BUY, 1.0, OrderType.LIMIT)


def test_stop_order_requires_a_stop_price():
    with pytest.raises(ValueError, match="stop_price"):
        Order(next_client_order_id(), "BTC", OrderSide.BUY, 1.0, OrderType.STOP)


def test_signed_quantity_follows_side():
    assert market(side=OrderSide.BUY, qty=5).signed_quantity == 5.0
    assert market(side=OrderSide.SELL, qty=5).signed_quantity == -5.0


def test_client_order_ids_are_unique():
    assert len({next_client_order_id() for _ in range(100)}) == 100


# -- paper broker: fills ---------------------------------------------------


def test_market_order_fills_immediately(broker):
    order = broker.submit(market())
    assert order.status is OrderStatus.FILLED
    assert order.avg_fill_price == pytest.approx(100.0)
    assert broker.get_position("BTC").units == pytest.approx(10.0)


def test_market_order_rejected_without_a_mark_price(broker):
    order = broker.submit(market(symbol="DOGE"))
    assert order.status is OrderStatus.REJECTED
    assert "no mark price" in order.reason


def test_cash_decreases_by_notional_on_a_buy(broker):
    broker.submit(market(qty=10.0))
    assert broker.get_account().cash == pytest.approx(100_000.0 - 1_000.0)


def test_slippage_and_commission_work_against_the_trader():
    b = PaperBroker(cash=100_000.0, commission_bps=10, slippage_bps=10)
    b.update_price("BTC", 100.0)
    buy = b.submit(market(side=OrderSide.BUY))
    assert buy.avg_fill_price > 100.0
    b.update_price("ETH", 100.0)
    sell = b.submit(market(symbol="ETH", side=OrderSide.SELL))
    assert sell.avg_fill_price < 100.0


def test_submission_is_idempotent_on_client_order_id(broker):
    order = market()
    broker.submit(order)
    broker.submit(order)  # resend
    assert broker.get_position("BTC").units == pytest.approx(10.0)


# -- paper broker: resting orders -----------------------------------------


def test_buy_limit_rests_until_price_falls(broker):
    order = Order(next_client_order_id(), "BTC", OrderSide.BUY, 5.0, OrderType.LIMIT, limit_price=90.0)
    broker.submit(order)
    assert order.status is OrderStatus.PENDING

    broker.update_price("BTC", 95.0)
    assert order.status is OrderStatus.PENDING

    broker.update_price("BTC", 89.0)
    assert order.status is OrderStatus.FILLED


def test_sell_limit_rests_until_price_rises(broker):
    order = Order(next_client_order_id(), "BTC", OrderSide.SELL, 5.0, OrderType.LIMIT, limit_price=110.0)
    broker.submit(order)
    broker.update_price("BTC", 105.0)
    assert order.status is OrderStatus.PENDING
    broker.update_price("BTC", 111.0)
    assert order.status is OrderStatus.FILLED


def test_buy_limit_marketable_on_arrival_fills_at_once(broker):
    order = Order(next_client_order_id(), "BTC", OrderSide.BUY, 5.0, OrderType.LIMIT, limit_price=110.0)
    broker.submit(order)
    assert order.status is OrderStatus.FILLED


def test_sell_stop_triggers_when_price_falls_through_it(broker):
    order = Order(next_client_order_id(), "BTC", OrderSide.SELL, 5.0, OrderType.STOP, stop_price=90.0)
    broker.submit(order)
    broker.update_price("BTC", 95.0)
    assert order.status is OrderStatus.PENDING
    broker.update_price("BTC", 89.0)
    assert order.status is OrderStatus.FILLED


def test_cancel_prevents_a_resting_order_from_filling(broker):
    order = Order(next_client_order_id(), "BTC", OrderSide.BUY, 5.0, OrderType.LIMIT, limit_price=90.0)
    broker.submit(order)
    broker.cancel(order.client_order_id)
    broker.update_price("BTC", 80.0)
    assert order.status is OrderStatus.CANCELLED
    assert broker.get_position("BTC").is_flat


def test_cancelling_an_unknown_order_raises(broker):
    with pytest.raises(BrokerError, match="unknown order"):
        broker.cancel("nope")


def test_cancelling_a_filled_order_is_a_noop(broker):
    order = broker.submit(market())
    assert broker.cancel(order.client_order_id).status is OrderStatus.FILLED


def test_update_price_rejects_non_positive(broker):
    with pytest.raises(ValueError, match="price"):
        broker.update_price("BTC", 0.0)


# -- position accounting ---------------------------------------------------


def test_increasing_a_position_averages_the_cost():
    p = Position("BTC")
    _apply_to_position(p, 10.0, 100.0)
    _apply_to_position(p, 10.0, 200.0)
    assert p.units == pytest.approx(20.0)
    assert p.avg_price == pytest.approx(150.0)


def test_reducing_a_long_realizes_pnl_and_keeps_basis():
    p = Position("BTC")
    _apply_to_position(p, 10.0, 100.0)
    realized = _apply_to_position(p, -4.0, 120.0)
    assert realized == pytest.approx(80.0)
    assert p.units == pytest.approx(6.0)
    assert p.avg_price == pytest.approx(100.0)


def test_closing_a_short_realizes_positive_pnl_when_price_falls():
    p = Position("BTC")
    _apply_to_position(p, -10.0, 100.0)
    realized = _apply_to_position(p, 10.0, 80.0)
    assert realized == pytest.approx(200.0)
    assert p.is_flat


def test_flipping_through_zero_rebases_at_the_fill_price():
    p = Position("BTC")
    _apply_to_position(p, 10.0, 100.0)
    realized = _apply_to_position(p, -15.0, 120.0)
    assert realized == pytest.approx(200.0)  # only the 10 long units realize
    assert p.units == pytest.approx(-5.0)
    assert p.avg_price == pytest.approx(120.0)


def test_round_trip_realized_pnl_reaches_the_account(broker):
    broker.submit(market(qty=10.0))
    broker.update_price("BTC", 120.0)
    broker.submit(market(side=OrderSide.SELL, qty=10.0))
    assert broker.get_account().realized_pnl == pytest.approx(200.0)
    assert broker.get_position("BTC").is_flat


def test_equity_tracks_marked_positions(broker):
    broker.submit(market(qty=10.0))
    broker.update_price("BTC", 110.0)
    # 99,000 cash + 10 units marked at 110 = 100,100
    assert broker.equity() == pytest.approx(100_100.0)


def test_gross_notional_is_absolute_exposure(broker):
    broker.submit(market(side=OrderSide.SELL, qty=10.0))
    assert broker.gross_notional() == pytest.approx(1_000.0)


# -- order manager: risk gate ---------------------------------------------


def test_signal_is_sized_by_the_risk_manager(broker):
    risk = RiskManager(RiskLimits(risk_per_trade=0.01, max_position_pct=1.0, max_leverage=10.0))
    om = OrderManager(broker, risk, sleep=lambda _: None)

    report = om.execute(Signal("BTC", 1.0, "test"), price=100.0, atr=5.0)

    assert report.accepted
    # Stop 2 ATR away = 10 wide; 1% of 100k = 1000 risked -> 100 units.
    assert report.target_units == pytest.approx(100.0)
    assert broker.get_position("BTC").units == pytest.approx(100.0)


def test_half_weight_takes_half_the_risk_budget(broker):
    risk = RiskManager(RiskLimits(risk_per_trade=0.01, max_position_pct=1.0, max_leverage=10.0))
    om = OrderManager(broker, risk, sleep=lambda _: None)
    report = om.execute(Signal("BTC", 0.5, "half"), price=100.0, atr=5.0)
    assert report.target_units == pytest.approx(50.0)


def test_negative_weight_opens_a_short(broker):
    om = OrderManager(broker, RiskManager(RiskLimits(max_position_pct=1.0)), sleep=lambda _: None)
    om.execute(Signal("BTC", -1.0, "short"), price=100.0, atr=5.0)
    assert broker.get_position("BTC").units < 0


def test_zero_weight_flattens_an_open_position(broker):
    om = OrderManager(broker, sleep=lambda _: None)
    om.execute(Signal("BTC", 1.0, "open"), price=100.0, atr=5.0)
    assert not broker.get_position("BTC").is_flat

    om.execute(Signal("BTC", 0.0, "close"), price=100.0, atr=5.0)
    assert broker.get_position("BTC").is_flat


def test_drawdown_halt_blocks_new_orders(broker):
    risk = RiskManager(RiskLimits(max_drawdown=0.10))
    om = OrderManager(broker, risk, sleep=lambda _: None)
    risk.update_equity(200_000.0)  # peak well above current equity

    report = om.execute(Signal("BTC", 1.0, "test"), price=100.0, atr=5.0)
    assert not report.accepted
    assert "drawdown halt" in report.reason
    assert broker.get_position("BTC").is_flat


def test_leverage_cap_limits_position_size(broker):
    risk = RiskManager(RiskLimits(risk_per_trade=0.9, max_position_pct=1.0, max_leverage=0.5))
    om = OrderManager(broker, risk, sleep=lambda _: None)
    om.execute(Signal("BTC", 1.0, "big"), price=100.0, atr=5.0)
    assert broker.gross_notional() <= 100_000.0 * 0.5 + 1e-6


def test_repeated_identical_signal_does_not_stack_position(broker):
    om = OrderManager(broker, sleep=lambda _: None)
    om.execute(Signal("BTC", 1.0, "first"), price=100.0, atr=5.0)
    units_after_first = broker.get_position("BTC").units
    report = om.execute(Signal("BTC", 1.0, "again"), price=100.0, atr=5.0)
    assert broker.get_position("BTC").units == pytest.approx(units_after_first)
    assert "already at target" in report.reason


def test_execute_rejects_non_positive_price(broker):
    with pytest.raises(ValueError, match="price"):
        OrderManager(broker, sleep=lambda _: None).execute(
            Signal("BTC", 1.0, "x"), price=0.0, atr=5.0
        )


def test_history_records_every_decision(broker):
    om = OrderManager(broker, sleep=lambda _: None)
    om.execute(Signal("BTC", 1.0, "a"), price=100.0, atr=5.0)
    om.execute(Signal("BTC", 0.0, "b"), price=100.0, atr=5.0)
    assert len(om.history) == 2


# -- order manager: kill switch -------------------------------------------


def test_kill_switch_blocks_new_positions(broker):
    om = OrderManager(broker, sleep=lambda _: None)
    om.engage_kill_switch("incident")

    report = om.execute(Signal("BTC", 1.0, "test"), price=100.0, atr=5.0)
    assert not report.accepted
    assert "kill switch" in report.reason
    assert broker.get_position("BTC").is_flat


def test_kill_switch_still_allows_flattening(broker):
    """A kill switch reduces exposure; it must never trap an open position."""
    om = OrderManager(broker, sleep=lambda _: None)
    om.execute(Signal("BTC", 1.0, "open"), price=100.0, atr=5.0)
    om.engage_kill_switch("incident")

    report = om.flatten("BTC")
    assert report.accepted
    assert broker.get_position("BTC").is_flat


def test_kill_switch_can_be_released(broker):
    om = OrderManager(broker, sleep=lambda _: None)
    om.engage_kill_switch()
    assert om.halted
    om.release_kill_switch()
    assert not om.halted
    assert om.execute(Signal("BTC", 1.0, "test"), price=100.0, atr=5.0).accepted


def test_flatten_on_a_flat_symbol_is_a_noop(broker):
    report = OrderManager(broker, sleep=lambda _: None).flatten("BTC")
    assert report.accepted
    assert report.order is None


# -- order manager: retries ------------------------------------------------


class FlakyBroker(Broker):
    """Fails transiently a fixed number of times, then delegates."""

    def __init__(self, inner: PaperBroker, failures: int):
        self.inner = inner
        self.remaining = failures
        self.attempts = 0

    def submit(self, order):
        self.attempts += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise TransientBrokerError("network blip")
        return self.inner.submit(order)

    def cancel(self, cid):
        return self.inner.cancel(cid)

    def get_order(self, cid):
        return self.inner.get_order(cid)

    def get_position(self, symbol):
        return self.inner.get_position(symbol)

    def get_account(self):
        return self.inner.get_account()

    def equity(self):
        return self.inner.equity()

    def gross_notional(self):
        return self.inner.gross_notional()


def test_transient_failures_are_retried_then_succeed(broker):
    flaky = FlakyBroker(broker, failures=2)
    om = OrderManager(flaky, max_retries=3, sleep=lambda _: None)

    report = om.execute(Signal("BTC", 1.0, "test"), price=100.0, atr=5.0)
    assert report.accepted
    assert flaky.attempts == 3  # two failures, then success


def test_exhausted_retries_reject_the_order(broker):
    flaky = FlakyBroker(broker, failures=99)
    om = OrderManager(flaky, max_retries=2, sleep=lambda _: None)

    report = om.execute(Signal("BTC", 1.0, "test"), price=100.0, atr=5.0)
    assert not report.accepted
    assert "transient failure after 3 attempts" in report.order.reason
    assert broker.get_position("BTC").is_flat


def test_retries_reuse_the_client_order_id(broker):
    """Reusing the id is what stops a retry becoming a duplicate position."""
    seen = []

    class Recorder(FlakyBroker):
        def submit(self, order):
            seen.append(order.client_order_id)
            return super().submit(order)

    om = OrderManager(Recorder(broker, failures=2), max_retries=3, sleep=lambda _: None)
    om.execute(Signal("BTC", 1.0, "test"), price=100.0, atr=5.0)
    assert len(set(seen)) == 1


def test_backoff_delays_grow_exponentially(broker):
    delays = []
    om = OrderManager(
        FlakyBroker(broker, failures=3),
        max_retries=3,
        backoff_seconds=1.0,
        sleep=delays.append,
    )
    om.execute(Signal("BTC", 1.0, "test"), price=100.0, atr=5.0)
    assert delays == [1.0, 2.0, 4.0]


@pytest.mark.parametrize(
    "kwargs", [{"max_retries": -1}, {"backoff_seconds": -1.0}, {"min_order_notional": -1.0}]
)
def test_order_manager_rejects_invalid_config(broker, kwargs):
    with pytest.raises(ValueError):
        OrderManager(broker, **kwargs)


# -- end to end ------------------------------------------------------------


def test_paper_broker_accounting_matches_the_backtester():
    """Cross-validate two independent implementations of average-cost accounting.

    The broker and the backtester each track positions and realized PnL on their
    own. Driven with identical prices, costs, sizing, and fill timing, they must
    agree exactly -- if they ever diverge, one of them has a bug.
    """
    import numpy as np

    from ai_trading.backtest import Backtester

    rng = np.random.default_rng(21)
    n = 300
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.015, n)))
    idx = pd.date_range("2023-01-01", periods=n, freq="D")
    bars = pd.DataFrame(
        {"open": np.r_[close[0], close[:-1]], "high": close * 1.01, "low": close * 0.99, "close": close},
        index=idx,
    )

    def strat(history):
        if len(history) < 30:
            return float("nan")
        c = history["close"]
        return 1.0 if c.iloc[-10:].mean() > c.iloc[-30:].mean() else -1.0

    comm, slip, capital = 2.0, 3.0, 100_000.0
    backtest = Backtester(capital, commission_bps=comm, slippage_bps=slip).run(bars, strat)

    paper = PaperBroker(cash=capital, commission_bps=comm, slippage_bps=slip)
    opens, closes = bars["open"].to_numpy(), bars["close"].to_numpy()
    for i in range(n):
        paper.marks["X"] = closes[i]
        if i == n - 1:
            break
        weight = strat(bars.iloc[: i + 1])
        if weight != weight:  # NaN -- abstain
            continue
        delta = weight * paper.equity() / closes[i] - paper.get_position("X").units
        if delta == 0:
            continue
        paper.marks["X"] = opens[i + 1]  # fill at the next bar's open, as the engine does
        paper.submit(
            Order(
                next_client_order_id(),
                "X",
                OrderSide.BUY if delta > 0 else OrderSide.SELL,
                abs(delta),
                OrderType.MARKET,
            )
        )
    paper.marks["X"] = closes[-1]

    assert paper.equity() == pytest.approx(backtest.equity.iloc[-1], rel=1e-12)


def test_signal_to_fill_round_trip_updates_equity(broker):
    om = OrderManager(
        broker,
        RiskManager(RiskLimits(risk_per_trade=0.01, max_position_pct=1.0, max_leverage=10.0)),
        sleep=lambda _: None,
    )
    ts = pd.Timestamp("2024-01-01")

    om.execute(Signal("BTC", 1.0, "entry"), price=100.0, atr=5.0, timestamp=ts)
    broker.update_price("BTC", 110.0)
    om.execute(Signal("BTC", 0.0, "exit"), price=110.0, atr=5.0, timestamp=ts)

    assert broker.get_position("BTC").is_flat
    assert broker.get_account().realized_pnl == pytest.approx(1_000.0)  # 100 units x 10
    assert broker.equity() == pytest.approx(101_000.0)
