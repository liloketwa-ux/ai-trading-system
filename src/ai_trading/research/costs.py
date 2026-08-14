"""Execution cost assumptions for research.

Gross forward returns are not a result. A signal with a two-basis-point edge on
an instrument costing five basis points to trade is a losing strategy that looks
like a discovery, and the difference is invisible unless costs are applied
explicitly and reported alongside.

Three presets, pessimistic by default. Every result is reported gross **and**
net, so cost sensitivity is visible rather than assumed away.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["CostModel", "PESSIMISTIC", "REALISTIC", "OPTIMISTIC", "PRESETS"]


@dataclass(frozen=True)
class CostModel:
    """Round-trip cost assumptions in basis points."""

    name: str
    spread_bps: float
    slippage_bps: float
    commission_bps: float
    exchange_fee_bps: float = 0.0

    def __post_init__(self) -> None:
        for field_name in ("spread_bps", "slippage_bps", "commission_bps", "exchange_fee_bps"):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be >= 0")

    @property
    def round_trip_bps(self) -> float:
        """Total cost of entering and exiting once.

        Spread is crossed once per side; slippage, commission and fees apply to
        both sides.
        """
        return (
            self.spread_bps
            + 2 * (self.slippage_bps + self.commission_bps + self.exchange_fee_bps)
        )

    def apply(self, gross_return: float) -> float:
        return gross_return - self.round_trip_bps / 10_000.0

    def to_dict(self) -> dict:
        return {
            "name": self.name, "spread_bps": self.spread_bps,
            "slippage_bps": self.slippage_bps, "commission_bps": self.commission_bps,
            "exchange_fee_bps": self.exchange_fee_bps,
            "round_trip_bps": self.round_trip_bps,
        }


PESSIMISTIC = CostModel("pessimistic", spread_bps=2.0, slippage_bps=3.0, commission_bps=1.0)
REALISTIC = CostModel("realistic", spread_bps=1.0, slippage_bps=1.5, commission_bps=0.5)
OPTIMISTIC = CostModel("optimistic", spread_bps=0.5, slippage_bps=0.5, commission_bps=0.2)

PRESETS = {c.name: c for c in (PESSIMISTIC, REALISTIC, OPTIMISTIC)}
