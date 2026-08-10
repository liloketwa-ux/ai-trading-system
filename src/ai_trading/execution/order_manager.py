"""Order manager and broker adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Broker(ABC):
    """Unified broker/exchange adapter interface."""

    @abstractmethod
    def place_order(self, symbol: str, side: str, qty: float, **kwargs: Any) -> str:
        """Submit an order and return a broker order id."""
        raise NotImplementedError


class OrderManager:
    """Routes risk-approved signals to a broker with retry/failover.

    Placeholder: implementations will translate signals into concrete order
    types (market/limit/stop/OCO/trailing) and track fills.
    """

    def __init__(self, broker: Broker) -> None:
        self.broker = broker

    def execute(self, signal: Any, qty: float) -> str:
        """Execute a sized signal via the configured broker."""
        raise NotImplementedError
