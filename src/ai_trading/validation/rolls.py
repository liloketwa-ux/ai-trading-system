"""Contract-roll research policy.

Stitching futures contracts without adjustment manufactures a price jump the
trader never experienced. A back-adjusted series removes the jump but distorts
absolute price levels, so percentage returns computed from it are wrong -- and
wrong in a way that grows with history length.

There is no free option, so the policy is explicit and recorded. Where
continuous adjustment has not been implemented, :data:`RollMethod.NONE` is used
and any claim about long continuous history is refused rather than implied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

__all__ = ["RollMethod", "AdjustmentMethod", "RollPolicy", "ContractSeries",
           "RollEvent", "ContinuityError"]


class ContinuityError(RuntimeError):
    """A continuous-history claim was made without an adjustment policy."""


class RollMethod(str, Enum):
    NONE = "none"                    # individual contracts only, no stitching
    CALENDAR = "calendar"            # fixed days before expiry
    VOLUME = "volume"                # when the next contract's volume exceeds
    OPEN_INTEREST = "open_interest"  # when the next contract's OI exceeds


class AdjustmentMethod(str, Enum):
    NONE = "none"              # raw prices, gap preserved and visible
    BACK_ADJUSTED = "back_adjusted"      # subtract the gap from history
    RATIO_ADJUSTED = "ratio_adjusted"    # scale history by the price ratio


@dataclass(frozen=True)
class RollPolicy:
    """How contracts are joined, if at all."""

    method: RollMethod = RollMethod.NONE
    adjustment: AdjustmentMethod = AdjustmentMethod.NONE
    days_before_expiry: int = 8
    version: str = "1"

    def __post_init__(self) -> None:
        if self.method is RollMethod.NONE and self.adjustment is not AdjustmentMethod.NONE:
            raise ValueError("cannot adjust a series that is not being rolled")
        if self.days_before_expiry < 0:
            raise ValueError("days_before_expiry must be >= 0")

    @property
    def supports_continuous_history(self) -> bool:
        """Whether results may be described as spanning multiple contracts."""
        return self.method is not RollMethod.NONE and \
            self.adjustment is not AdjustmentMethod.NONE

    def assert_continuous_claim(self) -> None:
        """Guard a continuous-history claim. Fails closed."""
        if not self.supports_continuous_history:
            raise ContinuityError(
                f"roll method={self.method.value} adjustment={self.adjustment.value} "
                "does not produce a continuous series; results cover individual "
                "contracts only and must not be described as continuous history"
            )

    def to_dict(self) -> dict:
        return {
            "roll_method": self.method.value,
            "adjustment_method": self.adjustment.value,
            "days_before_expiry": self.days_before_expiry,
            "version": self.version,
            "supports_continuous_history": self.supports_continuous_history,
        }


@dataclass(frozen=True)
class RollEvent:
    """One transition between contracts."""

    roll_time: datetime
    from_contract: str
    to_contract: str
    from_price: float
    to_price: float

    @property
    def gap(self) -> float:
        return self.to_price - self.from_price

    @property
    def ratio(self) -> float:
        return self.to_price / self.from_price if self.from_price else 1.0


@dataclass
class ContractSeries:
    """A research series with an explicit roll record."""

    symbol: str
    policy: RollPolicy
    contract_versions: list[str] = field(default_factory=list)
    rolls: list[RollEvent] = field(default_factory=list)

    @property
    def is_single_contract(self) -> bool:
        return len(self.contract_versions) <= 1

    def describe(self) -> str:
        if self.is_single_contract:
            return (
                f"{self.symbol}: single contract "
                f"({self.contract_versions[0] if self.contract_versions else 'unspecified'}), "
                "no stitching"
            )
        return (
            f"{self.symbol}: {len(self.contract_versions)} contracts, "
            f"{len(self.rolls)} rolls, method={self.policy.method.value}, "
            f"adjustment={self.policy.adjustment.value}"
        )

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "policy": self.policy.to_dict(),
            "contract_versions": list(self.contract_versions),
            "rolls": [
                {"roll_time": r.roll_time.isoformat(), "from": r.from_contract,
                 "to": r.to_contract, "gap": r.gap, "ratio": r.ratio}
                for r in self.rolls
            ],
            "is_single_contract": self.is_single_contract,
        }
