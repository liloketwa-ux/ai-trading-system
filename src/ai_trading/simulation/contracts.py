"""Contract specifications.

Nothing about a futures contract is inferable from its price series. ES moves
$12.50 per tick and NQ $5.00; assuming a multiplier of 1 turns every PnL figure
into fiction while leaving the equity curve's *shape* intact, which is why the
error survives review.

So the multiplier is required, never defaulted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..features.sessions import CME_EQUITY, SessionDefinition

__all__ = ["ContractSpec", "CONTRACTS", "roll_date"]


@dataclass(frozen=True)
class ContractSpec:
    """Everything needed to convert price movement into money."""

    symbol: str
    exchange: str
    tick_size: float
    tick_value: float          # currency per tick per contract
    multiplier: float          # currency per full point per contract
    currency: str = "USD"
    session: SessionDefinition = CME_EQUITY
    description: str = ""
    expiry_months: tuple[int, ...] = (3, 6, 9, 12)
    roll_days_before_expiry: int = 8

    def __post_init__(self) -> None:
        for field_name in ("tick_size", "tick_value", "multiplier"):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{self.symbol}: {field_name} must be > 0")
        # Consistency: value per point should equal ticks per point x tick value.
        implied = self.tick_value / self.tick_size
        if abs(implied - self.multiplier) / self.multiplier > 0.01:
            raise ValueError(
                f"{self.symbol}: tick_value/tick_size = {implied:.4f} contradicts "
                f"multiplier {self.multiplier}"
            )

    def ticks(self, price_move: float) -> float:
        return price_move / self.tick_size

    def round_to_tick(self, price: float) -> float:
        return round(price / self.tick_size) * self.tick_size

    def pnl(self, entry: float, exit_price: float, contracts: float, direction: int) -> float:
        """Currency PnL for a position. Direction is +1 long, -1 short."""
        return direction * (exit_price - entry) * self.multiplier * contracts

    def notional(self, price: float, contracts: float) -> float:
        return price * self.multiplier * abs(contracts)


#: CME futures. Values are the standard published specifications.
CONTRACTS: dict[str, ContractSpec] = {
    "ES": ContractSpec("ES", "CME", 0.25, 12.50, 50.0, description="E-mini S&P 500"),
    "NQ": ContractSpec("NQ", "CME", 0.25, 5.00, 20.0, description="E-mini Nasdaq 100"),
    "YM": ContractSpec("YM", "CBOT", 1.0, 5.00, 5.0, description="E-mini Dow"),
    "GC": ContractSpec("GC", "COMEX", 0.10, 10.00, 100.0, description="Gold",
                       expiry_months=(2, 4, 6, 8, 10, 12)),
    "CL": ContractSpec("CL", "NYMEX", 0.01, 10.00, 1000.0, description="Crude Oil",
                       expiry_months=tuple(range(1, 13))),
}


def roll_date(spec: ContractSpec, expiry: date) -> date:
    """When a position should move to the next contract.

    Rolling matters for research: a naive continuous series that ignores the
    roll gap manufactures a price jump the trader never experienced.
    """
    from datetime import timedelta

    return expiry - timedelta(days=spec.roll_days_before_expiry)
