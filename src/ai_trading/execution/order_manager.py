"""Order manager: the gate between a strategy signal and a broker.

Every signal passes the same checks before any order is sent — kill switch,
drawdown halt, risk sizing, leverage headroom — so a strategy cannot size its
own position and a bug in strategy code cannot become an oversized order.

Sizing combines two inputs. The risk manager decides the *magnitude* a full
position may take, from the entry-to-stop distance and the portfolio limits;
the signal's ``weight`` supplies *direction* and scales that magnitude, so
``weight = 1.0`` takes the full risk budget and ``weight = 0.5`` takes half.
A weight of zero flattens.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from ..risk import RiskManager
from ..strategies.base import Signal
from .broker import Broker, TransientBrokerError
from .orders import Order, OrderSide, OrderStatus, OrderType, next_client_order_id

__all__ = ["OrderManager", "ExecutionReport"]


@dataclass(frozen=True)
class ExecutionReport:
    """Outcome of routing one signal.

    ``order`` is ``None`` when nothing was sent — either the signal was
    rejected by a control, or the position was already where it should be.
    """

    accepted: bool
    reason: str
    order: Order | None = None
    target_units: float = 0.0

    def __bool__(self) -> bool:
        return self.accepted


class OrderManager:
    """Routes risk-approved signals to a broker, with retries and a kill switch.

    Args:
        broker: Destination for orders.
        risk_manager: Risk gate. A default :class:`RiskManager` is used if
            omitted, so sizing is never unguarded.
        max_retries: Attempts after the first for transient broker failures.
        backoff_seconds: Base delay for exponential backoff between retries.
        min_order_notional: Orders smaller than this are skipped as dust.
        sleep: Injectable sleep, so tests need not wait.
    """

    def __init__(
        self,
        broker: Broker,
        risk_manager: RiskManager | None = None,
        *,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
        min_order_notional: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {max_retries}")
        if backoff_seconds < 0:
            raise ValueError(f"backoff_seconds must be >= 0, got {backoff_seconds}")
        if min_order_notional < 0:
            raise ValueError(f"min_order_notional must be >= 0, got {min_order_notional}")

        self.broker = broker
        self.risk = risk_manager or RiskManager()
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.min_order_notional = min_order_notional
        self._sleep = sleep

        self._kill_switch = False
        self._kill_reason = ""
        self.history: list[ExecutionReport] = []

    # -- kill switch -------------------------------------------------------

    @property
    def halted(self) -> bool:
        return self._kill_switch

    def engage_kill_switch(self, reason: str = "manual") -> None:
        """Block all new risk-increasing orders.

        Flattening remains available: a kill switch exists to reduce exposure,
        so it must never trap the system in a position it cannot exit.
        """
        self._kill_switch = True
        self._kill_reason = reason

    def release_kill_switch(self) -> None:
        self._kill_switch = False
        self._kill_reason = ""

    # -- routing -----------------------------------------------------------

    def execute(
        self,
        signal: Signal,
        *,
        price: float,
        atr: float,
        timestamp: pd.Timestamp | None = None,
    ) -> ExecutionReport:
        """Route one signal through the risk gate to the broker.

        Args:
            signal: The strategy's signal; its ``weight`` sets direction and
                scales the risk-budgeted size.
            price: Current mark price, used as the entry reference.
            atr: Current ATR, used to place the protective stop.
            timestamp: Stamped onto the resulting order.
        """
        if self._kill_switch:
            return self._record(ExecutionReport(False, f"kill switch engaged: {self._kill_reason}"))
        if price <= 0:
            raise ValueError(f"price must be > 0, got {price}")

        if signal.weight == 0.0:
            return self.flatten(signal.symbol, timestamp=timestamp)

        equity = self._equity()
        self.risk.update_equity(equity)
        if self.risk.trading_halted(equity):
            drawdown = self.risk.current_drawdown(equity)
            return self._record(ExecutionReport(False, f"drawdown halt at {drawdown:.2%}"))

        side = "long" if signal.weight > 0 else "short"
        stop = self.risk.stop_price(price, side, atr)
        decision = self.risk.size(
            equity=equity,
            entry=price,
            stop=stop,
            existing_notional=self._gross_notional(),
        )
        if not decision.approved:
            return self._record(ExecutionReport(False, f"risk rejected: {decision.reason}"))

        target_units = decision.units * signal.weight
        current = self.broker.get_position(signal.symbol).units
        delta = target_units - current

        if abs(delta) * price < self.min_order_notional:
            return self._record(
                ExecutionReport(True, "already at target", None, target_units)
            )

        order = self._submit(signal.symbol, delta, timestamp)
        accepted = order.status is not OrderStatus.REJECTED
        reason = f"{decision.reason}; {signal.rationale}" if accepted else order.reason
        return self._record(ExecutionReport(accepted, reason, order, target_units))

    def flatten(
        self, symbol: str, *, timestamp: pd.Timestamp | None = None
    ) -> ExecutionReport:
        """Close any open position in ``symbol``.

        Permitted even while the kill switch is engaged.
        """
        units = self.broker.get_position(symbol).units
        if units == 0.0:
            return self._record(ExecutionReport(True, "already flat", None, 0.0))
        order = self._submit(symbol, -units, timestamp)
        accepted = order.status is not OrderStatus.REJECTED
        return self._record(
            ExecutionReport(accepted, "flatten" if accepted else order.reason, order, 0.0)
        )

    # -- internals ---------------------------------------------------------

    def _submit(
        self, symbol: str, delta: float, timestamp: pd.Timestamp | None
    ) -> Order:
        order = Order(
            client_order_id=next_client_order_id(),
            symbol=symbol,
            side=OrderSide.BUY if delta > 0 else OrderSide.SELL,
            quantity=abs(delta),
            order_type=OrderType.MARKET,
            created_at=timestamp,
        )
        return self._submit_with_retry(order)

    def _submit_with_retry(self, order: Order) -> Order:
        """Submit, retrying transient failures with exponential backoff.

        Retries reuse the same client order id, so a failure that actually
        reached the broker cannot produce a duplicate position.
        """
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self.broker.submit(order)
            except TransientBrokerError as exc:
                last_error = exc
                if attempt < self.max_retries:
                    self._sleep(self.backoff_seconds * (2**attempt))

        order.status = OrderStatus.REJECTED
        order.reason = f"transient failure after {self.max_retries + 1} attempts: {last_error}"
        return order

    def _equity(self) -> float:
        broker = self.broker
        if hasattr(broker, "equity"):
            return float(broker.equity())
        return float(broker.get_account().cash)

    def _gross_notional(self) -> float:
        broker = self.broker
        if hasattr(broker, "gross_notional"):
            return float(broker.gross_notional())
        return 0.0

    def _record(self, report: ExecutionReport) -> ExecutionReport:
        self.history.append(report)
        return report
