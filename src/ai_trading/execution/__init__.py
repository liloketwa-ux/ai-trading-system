"""Execution: order management and broker/exchange adapters.

Wraps venue APIs (Alpaca, IBKR, Binance, ... ; CCXT for crypto) behind a
unified interface with retries and order-status tracking (design-doc section 8).
"""

from .order_manager import Broker, OrderManager

__all__ = ["Broker", "OrderManager"]
