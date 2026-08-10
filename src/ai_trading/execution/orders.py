"""Order, position, and account types shared by brokers and the order manager."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum

import pandas as pd

__all__ = [
    "OrderType",
    "OrderStatus",
    "OrderSide",
    "Order",
    "Position",
    "Account",
    "next_client_order_id",
]

_counter = itertools.count(1)


def next_client_order_id(prefix: str = "ord") -> str:
    """Generate a process-unique client order id.

    Client ids make submission idempotent: re-sending an order that already
    exists is a no-op rather than a duplicate position.
    """
    return f"{prefix}-{next(_counter):08d}"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> float:
        return 1.0 if self is OrderSide.BUY else -1.0


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"

    @property
    def is_terminal(self) -> bool:
        return self is not OrderStatus.PENDING


@dataclass
class Order:
    """A single order and its lifecycle state."""

    client_order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop_price: float | None = None
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = 0.0
    avg_fill_price: float | None = None
    created_at: pd.Timestamp | None = None
    filled_at: pd.Timestamp | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(f"quantity must be > 0, got {self.quantity}")
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit orders require a limit_price")
        if self.order_type is OrderType.STOP and self.stop_price is None:
            raise ValueError("stop orders require a stop_price")
        for name in ("limit_price", "stop_price"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be > 0, got {value}")

    @property
    def signed_quantity(self) -> float:
        """Quantity signed by direction: positive buys, negative sells."""
        return self.quantity * self.side.sign


@dataclass
class Position:
    """An open position in one symbol, tracked at average cost."""

    symbol: str
    units: float = 0.0
    avg_price: float = 0.0

    @property
    def is_flat(self) -> bool:
        return self.units == 0.0

    @property
    def direction(self) -> str:
        if self.units > 0:
            return "long"
        if self.units < 0:
            return "short"
        return "flat"

    def notional(self, mark_price: float) -> float:
        return abs(self.units) * mark_price

    def unrealized_pnl(self, mark_price: float) -> float:
        return self.units * (mark_price - self.avg_price)


@dataclass
class Account:
    """Cash, positions, and realized PnL for a trading account."""

    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    realized_pnl: float = 0.0

    def position(self, symbol: str) -> Position:
        """Return the position for ``symbol``, creating a flat one if absent."""
        return self.positions.setdefault(symbol, Position(symbol))

    def gross_notional(self, marks: dict[str, float]) -> float:
        """Total absolute exposure across positions, for leverage checks."""
        return sum(
            p.notional(marks[s]) for s, p in self.positions.items() if s in marks
        )

    def equity(self, marks: dict[str, float]) -> float:
        """Cash plus the marked value of all open positions."""
        return self.cash + sum(
            p.units * marks[s] for s, p in self.positions.items() if s in marks
        )
