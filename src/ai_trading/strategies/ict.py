"""ICT / Smart Money Concept strategy.

The setup this encodes: price breaks structure in one direction, leaving behind
an order block or fair value gap; price then retraces into that zone; the
strategy takes the continuation.

Only zones already confirmed at the decision bar are considered, and zones that
price has already traded back through (mitigated) are discarded. Old zones age
out — an order block from three hundred bars ago is not a live level.

This is a mechanical reading of a discretionary methodology. Treat it as one
defensible encoding of the rules, not as "the" ICT strategy.
"""

from __future__ import annotations

import pandas as pd

from .base import Signal, Strategy
from .structure import (
    Zone,
    fair_value_gaps,
    is_mitigated,
    order_blocks,
    zones_active_at,
)

__all__ = ["ICTStrategy"]


class ICTStrategy(Strategy):
    """Trade retracements into confirmed order blocks and fair value gaps.

    Args:
        symbol: Instrument label carried on emitted signals.
        swing_left: Bars left of a swing pivot.
        swing_right: Bars right of a swing pivot (also its confirmation lag).
        lookback: Bars of history scanned for structure. Bounds the work done
            per decision and keeps stale levels out.
        max_zone_age: Bars after which a confirmed zone is no longer traded.
        min_gap_pct: Minimum fair-value-gap width, as a fraction of price.
        weight: Absolute target weight taken when a setup triggers.
        use_fvg: Include fair value gaps alongside order blocks.
    """

    name = "ict"

    def __init__(
        self,
        symbol: str = "",
        *,
        swing_left: int = 2,
        swing_right: int = 2,
        lookback: int = 200,
        max_zone_age: int = 50,
        min_gap_pct: float = 0.001,
        weight: float = 1.0,
        use_fvg: bool = True,
    ) -> None:
        if lookback < 10:
            raise ValueError(f"lookback must be >= 10, got {lookback}")
        if not 0.0 < weight <= 1.0:
            raise ValueError(f"weight must be in (0, 1], got {weight}")
        if max_zone_age < 1:
            raise ValueError(f"max_zone_age must be >= 1, got {max_zone_age}")

        self.symbol = symbol
        self.swing_left = swing_left
        self.swing_right = swing_right
        self.lookback = lookback
        self.max_zone_age = max_zone_age
        self.min_gap_pct = min_gap_pct
        self.weight = weight
        self.use_fvg = use_fvg
        self.warmup = swing_left + swing_right + 5

    def evaluate(self, history: pd.DataFrame) -> Signal | None:
        window = history.iloc[-self.lookback :] if len(history) > self.lookback else history
        last = len(window) - 1
        price = float(window["close"].iloc[-1])

        zones = order_blocks(window, self.swing_left, self.swing_right)
        if self.use_fvg:
            zones += fair_value_gaps(window, self.min_gap_pct)

        candidates = zones_active_at(zones, last, self.max_zone_age)
        # A zone the current bar itself created cannot also be a retracement
        # into that zone -- require at least one bar of separation.
        candidates = [z for z in candidates if z.confirmed_index < last]

        best = self._select(candidates, window, price, last)
        if best is None:
            return None

        direction = "Long" if best.is_bullish else "Short"
        weight = self.weight if best.is_bullish else -self.weight
        rationale = (
            f"{direction}: price {price:.4f} retraced into {best.kind} "
            f"[{best.lower:.4f}, {best.upper:.4f}] formed at bar {best.formed_index}, "
            f"confirmed at bar {best.confirmed_index}"
        )
        return Signal(
            symbol=self.symbol,
            weight=weight,
            rationale=rationale,
            confidence=self._confidence(best, price),
        )

    # -- internals ---------------------------------------------------------

    def _select(
        self, zones: list[Zone], window: pd.DataFrame, price: float, last: int
    ) -> Zone | None:
        """Pick the freshest unmitigated zone currently containing price."""
        touched = [
            z
            for z in zones
            if z.contains(price) and not is_mitigated(z, window, last - 1)
        ]
        if not touched:
            return None
        return max(touched, key=lambda z: z.confirmed_index)

    @staticmethod
    def _confidence(zone: Zone, price: float) -> float:
        """Higher when price sits deep in the zone rather than grazing its edge."""
        span = zone.upper - zone.lower
        if span <= 0:
            return 0.5
        depth = 1.0 - abs(price - zone.midpoint) / (span / 2.0)
        return round(max(0.0, min(1.0, 0.5 + 0.5 * depth)), 4)
