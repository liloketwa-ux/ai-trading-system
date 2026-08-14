"""Event-driven backtest engine.

The loop processes events in **availability order**, and a strategy is handed
only a point-in-time state object. It never receives the bar frame, so
``df.iloc[-1]`` is not reachable from strategy code — the leak is closed by the
interface rather than by review.

The engine produces research evidence. It does not produce a strategy, and the
result object deliberately reports execution sensitivity (ambiguous bars,
latency, slippage model) alongside performance so a number can never be quoted
without the assumptions that produced it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from ..storage.records import utc
from .config import BacktestConfig
from .contracts import ContractSpec
from .events import EventType, SimEvent
from .execution import (
    ExecutionSimulator,
    Fill,
    OrderSide,
    OrderState,
    OrderType,
    SimOrder,
)
from .portfolio import Account
from .results import BacktestResult, TradeRecord, summarize

__all__ = ["PointInTimeState", "TradeCandidate", "SimStrategy", "BacktestEngine"]


@dataclass
class PointInTimeState:
    """Everything a strategy may see at one decision time.

    Deliberately narrow. There is no bar frame, no forward index, and no handle
    back to the engine — a strategy cannot reach data it should not have because
    the object does not carry it.
    """

    decision_time: datetime
    instrument: str
    bars: list[dict] = field(default_factory=list)      # completed bars only
    features: dict[str, Any] = field(default_factory=dict)
    position_contracts: float = 0.0
    equity: float = 0.0
    session_date: date | None = None

    @property
    def last_close(self) -> float | None:
        return self.bars[-1]["close"] if self.bars else None

    @property
    def in_position(self) -> bool:
        return abs(self.position_contracts) > 1e-12


@dataclass(frozen=True)
class TradeCandidate:
    """A strategy's proposal. The engine decides whether it becomes an order."""

    direction: int                  # +1 long, -1 short
    contracts: float = 1.0
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    reason: str = ""
    hypothesis_id: str = ""

    def __post_init__(self) -> None:
        if self.direction not in (-1, 1):
            raise ValueError("direction must be +1 or -1")
        if self.contracts <= 0:
            raise ValueError("contracts must be > 0")


class SimStrategy(ABC):
    """Deterministic strategy interface.

    Receives point-in-time state and nothing else.
    """

    name: str = "abstract"
    version: str = "1"

    @abstractmethod
    def evaluate(self, state: PointInTimeState) -> TradeCandidate | None:
        """Return a candidate, or None to stand aside."""

    def on_exit(self, state: PointInTimeState, trade: TradeRecord) -> None:
        """Optional hook after a position closes."""


class BacktestEngine:
    """Runs one hypothesis over one instrument's event stream."""

    def __init__(self, config: BacktestConfig, spec: ContractSpec) -> None:
        self.config = config
        self.spec = spec
        self.account = Account(config.starting_balance)
        self.execution = ExecutionSimulator(config.execution, spec)
        self.trades: list[TradeRecord] = []
        self.fills: list[Fill] = []
        self.events_processed = 0
        self._open: dict[str, Any] | None = None

    def run(self, events: list[SimEvent], strategy: SimStrategy) -> BacktestResult:
        """Process the event stream and return research evidence."""
        ordered = sorted(events, key=lambda e: e.sort_key)
        bars: list[dict] = []
        instrument = self.spec.symbol

        for event in ordered:
            self.events_processed += 1
            if event.event_type is not EventType.BAR:
                continue

            bar = dict(event.payload)
            bar_time = event.available_at        # act at the bar's CLOSE
            session_date = self.spec.session.session_date_of(bar_time)

            # 1. Resolve an open position's protective levels against this bar.
            if self._open is not None:
                self._resolve_exit(bar, bar_time)

            # 2. Fill any working orders.
            outcome = self.execution.process_bar(bar, bar_time)
            for fill in outcome.fills:
                self._apply_fill(fill, bar_time)

            # 3. Mark to market before the strategy sees equity.
            marks = {instrument: bar["close"]}
            specs = {instrument: self.spec}
            self.account.mark(bar_time, marks, specs, session_date)

            bars.append(bar)

            # 4. Ask the strategy, using only completed bars.
            state = PointInTimeState(
                decision_time=bar_time,
                instrument=instrument,
                bars=list(bars),
                features=dict(event.payload.get("features", {})),
                position_contracts=self.account.position(instrument).contracts,
                equity=self.account.equity(marks, specs),
                session_date=session_date,
            )
            candidate = strategy.evaluate(state)
            if candidate is not None and self._open is None:
                self._submit(candidate, bar_time, bar)

        return self._result()

    # -- internals ---------------------------------------------------------

    def _submit(self, candidate: TradeCandidate, now: datetime, bar: dict) -> None:
        side = OrderSide.BUY if candidate.direction > 0 else OrderSide.SELL
        order = SimOrder(
            instrument=self.spec.symbol, side=side, quantity=candidate.contracts,
            order_type=candidate.order_type, created_at=now,
            limit_price=candidate.limit_price, tag=candidate.hypothesis_id,
        )
        self.execution.submit(order, now)
        self._open = {
            "candidate": candidate, "order": order, "entry_time": now,
            "entry_price": None, "stop": candidate.stop_loss,
            "target": candidate.take_profit, "mae": 0.0, "mfe": 0.0,
            "ambiguous": False,
        }

    def _apply_fill(self, fill: Fill, now: datetime) -> None:
        signed = fill.quantity * fill.side.sign
        self.account.apply_fill(fill.instrument, signed, fill.price,
                                fill.total_cost, self.spec)
        self.fills.append(fill)
        if self._open is not None and self._open["entry_price"] is None:
            self._open["entry_price"] = fill.price
            self._open["entry_time"] = now

    def _resolve_exit(self, bar: dict, now: datetime) -> None:
        """Check protective levels; the stop wins ambiguous bars."""
        open_trade = self._open
        if open_trade["entry_price"] is None:
            return

        direction = open_trade["candidate"].direction
        entry = open_trade["entry_price"]
        stop, target = open_trade["stop"], open_trade["target"]

        favourable = (bar["high"] - entry) if direction > 0 else (entry - bar["low"])
        adverse = (entry - bar["low"]) if direction > 0 else (bar["high"] - entry)
        open_trade["mfe"] = max(open_trade["mfe"], favourable)
        open_trade["mae"] = max(open_trade["mae"], adverse)

        outcome, ambiguous = self.execution.check_exit_levels(bar, direction, stop, target)
        if ambiguous:
            open_trade["ambiguous"] = True
        if outcome is None:
            return

        exit_price = stop if outcome == "stop" else target
        position = self.account.position(self.spec.symbol)
        # Capture size before the fill closes the position and zeroes it.
        closed_contracts = abs(position.contracts)
        if closed_contracts < 1e-12:
            return
        signed = -position.contracts
        cost = (
            self.config.execution.commission_per_contract
            + self.config.execution.exchange_fee_per_contract
        ) * closed_contracts
        realized = self.account.apply_fill(self.spec.symbol, signed, exit_price,
                                           cost, self.spec)
        risk_per_contract = abs(entry - stop) if stop is not None else 0.0
        total_risk = risk_per_contract * self.spec.multiplier * closed_contracts

        self.trades.append(TradeRecord(
            instrument=self.spec.symbol,
            direction=direction,
            entry_time=open_trade["entry_time"],
            exit_time=now,
            entry_price=entry,
            exit_price=exit_price,
            contracts=closed_contracts,
            pnl=realized,
            costs=cost,
            outcome=outcome,
            mae=open_trade["mae"],
            mfe=open_trade["mfe"],
            ambiguous_bar=open_trade["ambiguous"],
            hypothesis_id=open_trade["candidate"].hypothesis_id,
            r_multiple=realized / total_risk if total_risk > 0 else None,
        ))
        self._open = None

    def _result(self) -> BacktestResult:
        return summarize(
            config=self.config,
            spec=self.spec,
            account=self.account,
            trades=self.trades,
            fills=self.fills,
            ambiguous_bar_count=self.execution.ambiguous_bar_count,
            events_processed=self.events_processed,
        )
