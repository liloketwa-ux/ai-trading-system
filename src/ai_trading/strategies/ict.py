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
        stop_buffer: Invalidation distance beyond the zone edge, as a multiple
            of the zone's own height. Zero exits the instant price closes past
            the edge; larger values tolerate a wick through it.

    .. note::
       This strategy is **stateful** -- it holds a position until the zone that
       justified it is invalidated, and so assumes one sequential call per bar
       (which is how the backtester drives it). Call :meth:`reset` before
       reusing an instance for a second run.
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
        stop_buffer: float = 0.25,
    ) -> None:
        if stop_buffer < 0:
            raise ValueError(f"stop_buffer must be >= 0, got {stop_buffer}")
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
        self.stop_buffer = stop_buffer
        self.warmup = swing_left + swing_right + 5

        # Held position and the zone that justified it. Without this the
        # strategy returns None on every bar where price is not sitting inside
        # a fresh zone, and None means *go flat* -- which unwound every entry
        # on the following bar and turned the strategy into pure cost churn.
        self._position = 0.0
        self._entry_zone: Zone | None = None

    def evaluate(self, history: pd.DataFrame) -> Signal | None:
        window = history.iloc[-self.lookback :] if len(history) > self.lookback else history
        last = len(window) - 1
        price = float(window["close"].iloc[-1])

        # Hold an open position until the zone that justified it is invalidated.
        if self._position != 0.0 and self._entry_zone is not None:
            if not self._invalidated(self._entry_zone, price):
                return Signal(
                    self.symbol,
                    self._position,
                    f"Hold {'long' if self._position > 0 else 'short'}: price {price:.4f} "
                    f"still respecting {self._entry_zone.kind} "
                    f"[{self._entry_zone.lower:.4f}, {self._entry_zone.upper:.4f}]",
                )
            invalidated = self._entry_zone
            self._position = 0.0
            self._entry_zone = None
            # Fall through: an invalidation bar may itself sit in an opposing zone.
            exit_reason = (
                f"Exit: price {price:.4f} closed beyond {invalidated.kind} "
                f"[{invalidated.lower:.4f}, {invalidated.upper:.4f}]"
            )
        else:
            exit_reason = None

        zones = order_blocks(window, self.swing_left, self.swing_right)
        if self.use_fvg:
            zones += fair_value_gaps(window, self.min_gap_pct)

        candidates = zones_active_at(zones, last, self.max_zone_age)
        # A zone the current bar itself created cannot also be a retracement
        # into that zone -- require at least one bar of separation.
        candidates = [z for z in candidates if z.confirmed_index < last]

        best = self._select(candidates, window, price, last)
        if best is None:
            # Nothing to enter. Report the exit if one just happened.
            return Signal(self.symbol, 0.0, exit_reason) if exit_reason else None

        direction = "Long" if best.is_bullish else "Short"
        weight = self.weight if best.is_bullish else -self.weight
        self._position = weight
        self._entry_zone = best
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

    def _invalidated(self, zone: Zone, price: float) -> bool:
        """True once price has closed through the far side of the entry zone.

        A long taken at a demand zone is wrong the moment price closes below
        it; a short at a supply zone is wrong once price closes above it. This
        is the stop, expressed structurally rather than as a fixed distance.
        """
        buffer = zone.upper - zone.lower
        if self._position > 0:
            return price < zone.lower - self.stop_buffer * buffer
        return price > zone.upper + self.stop_buffer * buffer

    def reset(self) -> None:
        """Clear held position and zone between independent runs."""
        self._position = 0.0
        self._entry_zone = None

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
