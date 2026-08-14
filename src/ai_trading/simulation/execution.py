"""Execution simulation: orders, fills, slippage, latency, ambiguity.

Three rules carry most of the realism.

**An order cannot fill before it was submitted.** Latency is modelled
explicitly, and a resting order becomes eligible only on bars whose close is at
or after ``submitted_at + latency``. Filling on the signal bar is the most
common way a backtest invents an edge.

**When a bar spans both stop and target, the stop wins.** OHLC data cannot say
which was touched first. Assuming the favourable order inflates every
R-multiple, and the inflation is largest exactly on the volatile bars that
matter most. Ambiguous bars are counted and reported so the size of the
assumption is visible rather than hidden.

**Slippage is adverse by construction.** Every model here moves the fill against
the trader; none can improve a price.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from ..storage.records import utc
from .contracts import ContractSpec

__all__ = [
    "OrderType", "OrderSide", "OrderState", "SimOrder", "Fill", "SlippageModel",
    "FixedTickSlippage", "PercentageSlippage", "SpreadSlippage",
    "VolatilityAdjustedSlippage", "ExecutionConfig", "ExecutionSimulator",
    "BarFillOutcome",
]

_ids = itertools.count(1)


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        return 1 if self is OrderSide.BUY else -1


class OrderState(str, Enum):
    """Deterministic lifecycle. Transitions are validated."""

    CREATED = "created"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"

    @property
    def is_terminal(self) -> bool:
        return self in (OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED)

    @property
    def is_working(self) -> bool:
        return self in (OrderState.SUBMITTED, OrderState.PARTIALLY_FILLED)


_ALLOWED_TRANSITIONS = {
    OrderState.CREATED: {OrderState.SUBMITTED, OrderState.REJECTED},
    OrderState.SUBMITTED: {OrderState.PARTIALLY_FILLED, OrderState.FILLED,
                           OrderState.CANCELLED, OrderState.REJECTED},
    OrderState.PARTIALLY_FILLED: {OrderState.PARTIALLY_FILLED, OrderState.FILLED,
                                  OrderState.CANCELLED},
    OrderState.FILLED: set(),
    OrderState.CANCELLED: set(),
    OrderState.REJECTED: set(),
}


@dataclass
class SimOrder:
    """A simulated order with an explicit state machine."""

    instrument: str
    side: OrderSide
    quantity: float
    order_type: OrderType
    created_at: datetime
    limit_price: float | None = None
    stop_price: float | None = None
    order_id: str = ""
    state: OrderState = OrderState.CREATED
    submitted_at: datetime | None = None
    eligible_at: datetime | None = None   # submitted_at + latency
    filled_quantity: float = 0.0
    average_fill_price: float | None = None
    tag: str = ""

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("quantity must be > 0")
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit order needs a limit_price")
        if self.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and self.stop_price is None:
            raise ValueError("stop order needs a stop_price")
        if self.order_type is OrderType.STOP_LIMIT and self.limit_price is None:
            raise ValueError("stop-limit order needs a limit_price")
        if not self.order_id:
            self.order_id = f"ord-{next(_ids):06d}"
        object.__setattr__(self, "created_at", utc(self.created_at))

    @property
    def remaining(self) -> float:
        return max(0.0, self.quantity - self.filled_quantity)

    def transition(self, state: OrderState) -> None:
        if state not in _ALLOWED_TRANSITIONS[self.state]:
            raise ValueError(f"illegal transition {self.state} -> {state}")
        self.state = state


@dataclass(frozen=True)
class Fill:
    """One execution against an order."""

    order_id: str
    instrument: str
    timestamp: datetime
    side: OrderSide
    quantity: float
    price: float
    commission: float = 0.0
    fees: float = 0.0
    partial: bool = False
    ambiguous_bar: bool = False

    @property
    def total_cost(self) -> float:
        return self.commission + self.fees


# -- slippage models -------------------------------------------------------


class SlippageModel:
    """Base. Every implementation must move the fill *against* the trader."""

    name = "none"

    def adjust(self, price: float, side: OrderSide, context: dict) -> float:
        return price


@dataclass
class FixedTickSlippage(SlippageModel):
    ticks: float = 1.0
    name: str = "fixed_ticks"

    def adjust(self, price: float, side: OrderSide, context: dict) -> float:
        tick = context.get("tick_size", 0.25)
        return price + side.sign * self.ticks * tick


@dataclass
class PercentageSlippage(SlippageModel):
    fraction: float = 0.0001
    name: str = "percentage"

    def adjust(self, price: float, side: OrderSide, context: dict) -> float:
        return price * (1.0 + side.sign * self.fraction)


@dataclass
class SpreadSlippage(SlippageModel):
    """Cross the spread. The floor is one tick when no spread is supplied."""

    spread_ticks: float = 1.0
    name: str = "spread"

    def adjust(self, price: float, side: OrderSide, context: dict) -> float:
        tick = context.get("tick_size", 0.25)
        # `or` rather than a get-default: the key is present holding None
        # whenever bars carry no spread field, and a default never fires.
        spread = context.get("spread") or self.spread_ticks * tick
        return price + side.sign * spread / 2.0


@dataclass
class VolatilityAdjustedSlippage(SlippageModel):
    """Scales with recent range -- fills degrade exactly when markets move."""

    atr_fraction: float = 0.05
    minimum_ticks: float = 0.5
    name: str = "volatility_adjusted"

    def adjust(self, price: float, side: OrderSide, context: dict) -> float:
        tick = context.get("tick_size", 0.25)
        atr = context.get("atr")
        move = max(self.atr_fraction * atr, self.minimum_ticks * tick) if atr else \
            self.minimum_ticks * tick
        return price + side.sign * move


# -- configuration ---------------------------------------------------------


@dataclass(frozen=True)
class ExecutionConfig:
    """Execution assumptions. Recorded on every backtest."""

    latency: timedelta = timedelta(0)
    slippage: SlippageModel = field(default_factory=lambda: FixedTickSlippage(1.0))
    commission_per_contract: float = 2.25
    exchange_fee_per_contract: float = 1.35
    max_fill_fraction: float = 1.0     # < 1 forces partial fills
    ambiguous_bar_policy: str = "stop_wins"
    version: str = "1"

    def __post_init__(self) -> None:
        if self.latency < timedelta(0):
            raise ValueError("latency cannot be negative")
        if not 0.0 < self.max_fill_fraction <= 1.0:
            raise ValueError("max_fill_fraction must be in (0, 1]")
        if self.ambiguous_bar_policy != "stop_wins":
            raise ValueError(
                "only 'stop_wins' is supported: OHLC data cannot establish "
                "intrabar order, and favourable ordering inflates every result"
            )

    def to_dict(self) -> dict:
        return {
            "latency_ms": self.latency.total_seconds() * 1000,
            "slippage_model": self.slippage.name,
            "commission_per_contract": self.commission_per_contract,
            "exchange_fee_per_contract": self.exchange_fee_per_contract,
            "max_fill_fraction": self.max_fill_fraction,
            "ambiguous_bar_policy": self.ambiguous_bar_policy,
            "version": self.version,
        }


@dataclass
class BarFillOutcome:
    """What a bar did to the working orders."""

    fills: list[Fill] = field(default_factory=list)
    ambiguous: bool = False


class ExecutionSimulator:
    """Fills orders against bars under explicit, adverse assumptions."""

    def __init__(self, config: ExecutionConfig, spec: ContractSpec) -> None:
        self.config = config
        self.spec = spec
        self.ambiguous_bar_count = 0
        self.orders: dict[str, SimOrder] = {}

    def submit(self, order: SimOrder, now: datetime) -> SimOrder:
        """Submit an order. It becomes fillable only after the latency elapses."""
        if order.order_id in self.orders:
            return self.orders[order.order_id]     # idempotent
        order.transition(OrderState.SUBMITTED)
        order.submitted_at = utc(now)
        order.eligible_at = order.submitted_at + self.config.latency
        self.orders[order.order_id] = order
        return order

    def cancel(self, order_id: str) -> SimOrder:
        order = self.orders[order_id]
        if not order.state.is_terminal:
            order.transition(OrderState.CANCELLED)
        return order

    def working_orders(self) -> list[SimOrder]:
        return [o for o in self.orders.values() if o.state.is_working]

    def process_bar(self, bar: dict, bar_time: datetime, *, atr: float | None = None) -> BarFillOutcome:
        """Attempt to fill working orders against one bar."""
        outcome = BarFillOutcome()
        moment = utc(bar_time)
        context = {"tick_size": self.spec.tick_size, "atr": atr,
                   "spread": bar.get("spread")}

        for order in self.working_orders():
            # An order cannot fill before it was submitted, plus latency.
            if order.eligible_at is not None and moment < order.eligible_at:
                continue

            price = self._fill_price(order, bar)
            if price is None:
                continue

            filled = order.remaining * self.config.max_fill_fraction
            filled = min(filled, order.remaining)
            partial = filled < order.remaining - 1e-12

            executed = self.spec.round_to_tick(
                self.config.slippage.adjust(price, order.side, context)
            )
            commission = self.config.commission_per_contract * filled
            fees = self.config.exchange_fee_per_contract * filled

            fill = Fill(order.order_id, order.instrument, moment, order.side,
                        filled, executed, commission, fees, partial)
            outcome.fills.append(fill)

            previous_notional = (order.average_fill_price or 0.0) * order.filled_quantity
            order.filled_quantity += filled
            order.average_fill_price = (previous_notional + executed * filled) / order.filled_quantity
            order.transition(
                OrderState.FILLED if order.remaining <= 1e-12 else OrderState.PARTIALLY_FILLED
            )
        return outcome

    def check_exit_levels(
        self, bar: dict, direction: int, stop: float | None, target: float | None
    ) -> tuple[str | None, bool]:
        """Which protective level a bar hit, and whether the bar was ambiguous.

        Returns ``(outcome, ambiguous)`` where outcome is ``"stop"``,
        ``"target"`` or ``None``. When both levels sit inside the bar's range the
        stop wins and the bar is counted as ambiguous.
        """
        high, low = bar["high"], bar["low"]
        hit_stop = hit_target = False

        if stop is not None:
            hit_stop = low <= stop if direction > 0 else high >= stop
        if target is not None:
            hit_target = high >= target if direction > 0 else low <= target

        if hit_stop and hit_target:
            self.ambiguous_bar_count += 1
            return "stop", True          # never the favourable ordering
        if hit_stop:
            return "stop", False
        if hit_target:
            return "target", False
        return None, False

    # -- internals ---------------------------------------------------------

    def _fill_price(self, order: SimOrder, bar: dict) -> float | None:
        """Reference price at which this order would trade, or None."""
        open_, high, low = bar["open"], bar["high"], bar["low"]

        if order.order_type is OrderType.MARKET:
            return open_

        if order.order_type is OrderType.LIMIT:
            if order.side is OrderSide.BUY and low <= order.limit_price:
                return min(open_, order.limit_price)
            if order.side is OrderSide.SELL and high >= order.limit_price:
                return max(open_, order.limit_price)
            return None

        if order.order_type is OrderType.STOP:
            if order.side is OrderSide.BUY and high >= order.stop_price:
                return max(open_, order.stop_price)
            if order.side is OrderSide.SELL and low <= order.stop_price:
                return min(open_, order.stop_price)
            return None

        # Stop-limit: the stop must trigger and the limit must then be marketable.
        triggered = (
            high >= order.stop_price if order.side is OrderSide.BUY
            else low <= order.stop_price
        )
        if not triggered:
            return None
        if order.side is OrderSide.BUY and low <= order.limit_price:
            return min(max(open_, order.stop_price), order.limit_price)
        if order.side is OrderSide.SELL and high >= order.limit_price:
            return max(min(open_, order.stop_price), order.limit_price)
        return None
