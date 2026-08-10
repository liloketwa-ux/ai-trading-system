"""Execution: order management and broker adapters.

Signals reach a broker only through :class:`OrderManager`, which applies the
kill switch, drawdown halt, and risk sizing first (design-doc section 8).

Only :class:`PaperBroker` is provided. There is deliberately no live adapter --
see the warning in :mod:`ai_trading.execution.broker`.
"""

from .broker import Broker, BrokerError, PaperBroker, TransientBrokerError
from .order_manager import ExecutionReport, OrderManager
from .orders import (
    Account,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    next_client_order_id,
)

__all__ = [
    "Account",
    "Broker",
    "BrokerError",
    "ExecutionReport",
    "Order",
    "OrderManager",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "PaperBroker",
    "Position",
    "TransientBrokerError",
    "next_client_order_id",
]
