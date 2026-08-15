"""Funding and financing accounting.

A perpetual-swap strategy that holds through funding pays or receives it, and
the amounts are frequently larger than the trading edge being measured. Reporting
a "net" figure that silently excludes funding is not an approximation -- it is
the wrong number, and it is wrong in the direction that flatters the strategy.

So the components are separated, and a net result that omits a material
component is refused rather than rounded away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from ..storage.records import utc

__all__ = ["ComponentStatus", "PnLBreakdown", "FundingAccrual", "EconomicConfidenceError"]


class EconomicConfidenceError(RuntimeError):
    """A net economic claim was made while a material component is unavailable."""


class ComponentStatus(str, Enum):
    MEASURED = "measured"
    ESTIMATED = "estimated"
    NOT_APPLICABLE = "not_applicable"
    UNAVAILABLE = "unavailable"

    @property
    def usable(self) -> bool:
        return self in (ComponentStatus.MEASURED, ComponentStatus.ESTIMATED,
                        ComponentStatus.NOT_APPLICABLE)


@dataclass(frozen=True)
class FundingAccrual:
    """One funding payment."""

    timestamp: datetime
    rate: float
    notional: float
    direction: int          # +1 long pays a positive rate

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", utc(self.timestamp))

    @property
    def amount(self) -> float:
        """Negative when the position pays."""
        return -self.direction * self.rate * abs(self.notional)


@dataclass
class PnLBreakdown:
    """Economic result decomposed, with each component's provenance."""

    price_pnl: float = 0.0
    trading_fees: float = 0.0
    spread_cost: float = 0.0
    slippage_cost: float = 0.0
    funding: float = 0.0
    borrow_cost: float = 0.0

    price_status: ComponentStatus = ComponentStatus.MEASURED
    fees_status: ComponentStatus = ComponentStatus.MEASURED
    spread_status: ComponentStatus = ComponentStatus.MEASURED
    slippage_status: ComponentStatus = ComponentStatus.MEASURED
    funding_status: ComponentStatus = ComponentStatus.NOT_APPLICABLE
    borrow_status: ComponentStatus = ComponentStatus.NOT_APPLICABLE

    accruals: list[FundingAccrual] = field(default_factory=list)

    @property
    def components(self) -> dict[str, tuple[float, ComponentStatus]]:
        return {
            "price_pnl": (self.price_pnl, self.price_status),
            "trading_fees": (-abs(self.trading_fees), self.fees_status),
            "spread_cost": (-abs(self.spread_cost), self.spread_status),
            "slippage_cost": (-abs(self.slippage_cost), self.slippage_status),
            "funding": (self.funding, self.funding_status),
            "borrow_cost": (-abs(self.borrow_cost), self.borrow_status),
        }

    @property
    def unavailable(self) -> list[str]:
        return [name for name, (_, status) in self.components.items()
                if status is ComponentStatus.UNAVAILABLE]

    @property
    def gross(self) -> float:
        return self.price_pnl

    def net(self, *, require_all: bool = True) -> float:
        """Net economic result.

        Raises when a component is UNAVAILABLE and ``require_all`` is set: a net
        figure missing funding is not a conservative estimate, it is a wrong
        number that flatters the strategy.
        """
        missing = self.unavailable
        if missing and require_all:
            raise EconomicConfidenceError(
                f"cannot report a net result while {', '.join(missing)} "
                f"{'is' if len(missing) == 1 else 'are'} UNAVAILABLE; "
                "label the claim as gross-only or supply the missing component"
            )
        return sum(value for value, status in self.components.values()
                   if status is not ComponentStatus.UNAVAILABLE)

    @property
    def economically_confident(self) -> bool:
        return not self.unavailable

    def add_funding(self, accrual: FundingAccrual) -> None:
        self.accruals.append(accrual)
        self.funding += accrual.amount
        self.funding_status = ComponentStatus.MEASURED

    def to_dict(self) -> dict:
        return {
            "gross": self.gross,
            "net": self.net(require_all=False),
            "economically_confident": self.economically_confident,
            "unavailable_components": self.unavailable,
            "components": {
                name: {"amount": value, "status": status.value}
                for name, (value, status) in self.components.items()
            },
            "funding_accruals": len(self.accruals),
        }
