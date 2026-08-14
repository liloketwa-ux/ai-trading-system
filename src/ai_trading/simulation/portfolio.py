"""Equity accounting -- the foundation the prop-firm layer will sit on.

Balance and equity are tracked separately because prop rules care about the
difference: most firms breach on *equity* including open positions, so a
balance-only account understates drawdown for exactly as long as a loser is
held.

Daily figures reset on the contract's session boundary, not UTC midnight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from ..storage.records import utc
from .contracts import ContractSpec

__all__ = ["Position", "Account", "EquityPoint"]


@dataclass
class Position:
    """An open position in one instrument, at average cost."""

    instrument: str
    contracts: float = 0.0     # signed: positive long
    average_price: float = 0.0

    @property
    def direction(self) -> int:
        return 1 if self.contracts > 0 else -1 if self.contracts < 0 else 0

    @property
    def is_flat(self) -> bool:
        return abs(self.contracts) < 1e-12

    def unrealized(self, mark: float, spec: ContractSpec) -> float:
        if self.is_flat:
            return 0.0
        return (mark - self.average_price) * spec.multiplier * self.contracts


@dataclass(frozen=True)
class EquityPoint:
    timestamp: datetime
    balance: float
    equity: float
    unrealized: float
    position: float


@dataclass
class Account:
    """Balance, equity, and the daily figures prop rules are computed from."""

    starting_balance: float
    balance: float = 0.0
    realized_pnl: float = 0.0
    total_costs: float = 0.0
    peak_equity: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)
    curve: list[EquityPoint] = field(default_factory=list)
    day_start_equity: float = 0.0
    day_peak_equity: float = 0.0
    current_session: date | None = None
    max_drawdown: float = 0.0
    max_daily_drawdown: float = 0.0

    def __post_init__(self) -> None:
        if self.starting_balance <= 0:
            raise ValueError("starting_balance must be > 0")
        self.balance = self.starting_balance
        self.peak_equity = self.starting_balance
        self.day_start_equity = self.starting_balance
        self.day_peak_equity = self.starting_balance

    def position(self, instrument: str) -> Position:
        return self.positions.setdefault(instrument, Position(instrument))

    def equity(self, marks: dict[str, float], specs: dict[str, ContractSpec]) -> float:
        unrealized = sum(
            p.unrealized(marks[s], specs[s])
            for s, p in self.positions.items()
            if s in marks and s in specs
        )
        return self.balance + unrealized

    def apply_fill(self, instrument: str, signed_qty: float, price: float,
                   cost: float, spec: ContractSpec) -> float:
        """Apply a fill, returning realized PnL. Average-cost accounting."""
        position = self.position(instrument)
        realized = 0.0
        current = position.contracts

        increasing = current == 0 or (current > 0) == (signed_qty > 0)
        if increasing:
            total = abs(current) + abs(signed_qty)
            position.average_price = (
                position.average_price * abs(current) + price * abs(signed_qty)
            ) / total
            position.contracts = current + signed_qty
        else:
            closed = min(abs(signed_qty), abs(current))
            realized = (price - position.average_price) * spec.multiplier * closed * (
                1 if current > 0 else -1
            )
            new_contracts = current + signed_qty
            if abs(signed_qty) > abs(current):
                position.average_price = price       # flipped through zero
            elif abs(new_contracts) < 1e-12:
                position.average_price = 0.0
            position.contracts = new_contracts

        self.realized_pnl += realized
        self.total_costs += cost
        self.balance += realized - cost
        return realized

    def mark(self, timestamp: datetime, marks: dict[str, float],
             specs: dict[str, ContractSpec], session_date: date | None = None) -> EquityPoint:
        """Mark to market and update drawdown figures."""
        moment = utc(timestamp)
        if session_date is not None and session_date != self.current_session:
            self.current_session = session_date
            self.day_start_equity = self.equity(marks, specs)
            self.day_peak_equity = self.day_start_equity

        equity = self.equity(marks, specs)
        unrealized = equity - self.balance

        self.peak_equity = max(self.peak_equity, equity)
        self.day_peak_equity = max(self.day_peak_equity, equity)
        if self.peak_equity > 0:
            self.max_drawdown = max(self.max_drawdown, 1.0 - equity / self.peak_equity)
        if self.day_peak_equity > 0:
            self.max_daily_drawdown = max(
                self.max_daily_drawdown, 1.0 - equity / self.day_peak_equity
            )

        point = EquityPoint(moment, self.balance, equity, unrealized,
                            sum(p.contracts for p in self.positions.values()))
        self.curve.append(point)
        return point

    @property
    def net_return(self) -> float:
        if not self.curve:
            return 0.0
        return self.curve[-1].equity / self.starting_balance - 1.0
