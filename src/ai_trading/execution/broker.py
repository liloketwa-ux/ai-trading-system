"""Broker interface and a paper-trading implementation.

:class:`PaperBroker` simulates fills locally against a mark price. It reaches no
network and moves no money, which makes the whole execution path testable
offline.

.. warning::
   There is deliberately **no live broker adapter** in this module. Adding one
   is the step that turns simulated orders into real ones, and it should be a
   separate, explicit decision — reviewed alongside credential handling, a
   kill switch, and position reconciliation — not something that arrives as a
   side effect of a refactor.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from .orders import Account, Order, OrderSide, OrderStatus, OrderType, Position

__all__ = ["Broker", "BrokerError", "TransientBrokerError", "PaperBroker"]


class BrokerError(RuntimeError):
    """A broker rejected a request. Not retryable."""


class TransientBrokerError(BrokerError):
    """A transport-level failure that may succeed on retry."""


class Broker(ABC):
    """Unified broker/exchange adapter interface."""

    @abstractmethod
    def submit(self, order: Order) -> Order:
        """Submit an order. Implementations must be idempotent on client id."""

    @abstractmethod
    def cancel(self, client_order_id: str) -> Order:
        """Cancel a resting order."""

    @abstractmethod
    def get_order(self, client_order_id: str) -> Order | None:
        """Look up a previously submitted order."""

    @abstractmethod
    def get_position(self, symbol: str) -> Position:
        """Current position in ``symbol`` (flat if none)."""

    @abstractmethod
    def get_account(self) -> Account:
        """Current account state."""


class PaperBroker(Broker):
    """In-memory broker that simulates fills against a mark price.

    Market orders fill immediately at the mark, moved adversely by slippage.
    Limit and stop orders rest until a price update triggers them, so tests can
    drive the full lifecycle deterministically by calling :meth:`update_price`.

    Args:
        cash: Starting cash.
        commission_bps: Per-side commission in basis points of notional.
        slippage_bps: Per-side slippage in basis points, always adverse.
    """

    def __init__(
        self,
        cash: float = 100_000.0,
        *,
        commission_bps: float = 1.0,
        slippage_bps: float = 1.0,
    ) -> None:
        if cash <= 0:
            raise ValueError(f"cash must be > 0, got {cash}")
        if commission_bps < 0 or slippage_bps < 0:
            raise ValueError("commission_bps and slippage_bps must be >= 0")

        self.account = Account(cash=cash)
        self.commission_rate = commission_bps / 10_000.0
        self.slippage_rate = slippage_bps / 10_000.0
        self.marks: dict[str, float] = {}
        self.orders: dict[str, Order] = {}
        self._resting: list[Order] = []

    # -- price feed --------------------------------------------------------

    def update_price(self, symbol: str, price: float, timestamp: pd.Timestamp | None = None) -> None:
        """Update the mark for ``symbol`` and trigger any resting orders."""
        if price <= 0:
            raise ValueError(f"price must be > 0, got {price}")
        self.marks[symbol] = float(price)

        # Snapshot: filling an order mutates the resting list.
        for order in list(self._resting):
            if order.symbol == symbol and self._should_trigger(order, price):
                self._fill(order, price, timestamp)

    # -- Broker interface --------------------------------------------------

    def submit(self, order: Order) -> Order:
        existing = self.orders.get(order.client_order_id)
        if existing is not None:
            # Idempotent: a resend is not a second position.
            return existing

        if order.symbol not in self.marks:
            order.status = OrderStatus.REJECTED
            order.reason = f"no mark price for {order.symbol}"
            self.orders[order.client_order_id] = order
            return order

        self.orders[order.client_order_id] = order
        price = self.marks[order.symbol]

        if order.order_type is OrderType.MARKET:
            self._fill(order, price, order.created_at)
        else:
            self._resting.append(order)
            if self._should_trigger(order, price):
                self._fill(order, price, order.created_at)
        return order

    def cancel(self, client_order_id: str) -> Order:
        order = self.orders.get(client_order_id)
        if order is None:
            raise BrokerError(f"unknown order {client_order_id}")
        if order.status.is_terminal:
            return order
        order.status = OrderStatus.CANCELLED
        order.reason = "cancelled by client"
        if order in self._resting:
            self._resting.remove(order)
        return order

    def get_order(self, client_order_id: str) -> Order | None:
        return self.orders.get(client_order_id)

    def get_position(self, symbol: str) -> Position:
        return self.account.position(symbol)

    def get_account(self) -> Account:
        return self.account

    # -- helpers -----------------------------------------------------------

    def equity(self) -> float:
        """Account equity marked at the latest prices."""
        return self.account.equity(self.marks)

    def gross_notional(self) -> float:
        """Total absolute exposure, for leverage checks."""
        return self.account.gross_notional(self.marks)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _should_trigger(order: Order, price: float) -> bool:
        if order.status.is_terminal:
            return False
        if order.order_type is OrderType.MARKET:
            return True
        if order.order_type is OrderType.LIMIT:
            # Buy limits fill at or below the limit; sell limits at or above.
            return (
                price <= order.limit_price
                if order.side is OrderSide.BUY
                else price >= order.limit_price
            )
        # Stops are the mirror image: they trigger once price runs against you.
        return (
            price >= order.stop_price
            if order.side is OrderSide.BUY
            else price <= order.stop_price
        )

    def _fill(self, order: Order, price: float, timestamp: pd.Timestamp | None) -> None:
        sign = order.side.sign
        fill_price = price * (1.0 + sign * self.slippage_rate)
        commission = abs(order.quantity) * fill_price * self.commission_rate

        position = self.account.position(order.symbol)
        realized = _apply_to_position(position, order.signed_quantity, fill_price)

        self.account.cash -= order.signed_quantity * fill_price + commission
        self.account.realized_pnl += realized - commission

        order.status = OrderStatus.FILLED
        order.filled_quantity = order.quantity
        order.avg_fill_price = fill_price
        order.filled_at = timestamp
        order.reason = "filled"
        if order in self._resting:
            self._resting.remove(order)


def _apply_to_position(position: Position, delta: float, price: float) -> float:
    """Apply a signed fill to ``position``, returning realized PnL.

    Uses average-cost basis, matching the backtester's accounting so simulated
    live results and backtest results stay comparable.
    """
    units = position.units
    realized = 0.0

    increasing = units == 0 or (units > 0) == (delta > 0)
    if increasing:
        total = abs(units) + abs(delta)
        position.avg_price = (position.avg_price * abs(units) + price * abs(delta)) / total
        position.units = units + delta
        return realized

    closed = min(abs(delta), abs(units))
    realized = closed * (price - position.avg_price) * (1.0 if units > 0 else -1.0)
    new_units = units + delta

    if abs(delta) > abs(units):
        # Flipped through zero: the remainder opens fresh at the fill price.
        position.avg_price = price
    elif new_units == 0:
        position.avg_price = 0.0
    position.units = new_units
    return realized
