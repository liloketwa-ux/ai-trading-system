"""Momentum breakout strategy.

Enters when price closes beyond its recent range, optionally requiring a volume
or sentiment confirmation, and exits when price falls back through a shorter
trailing range. The asymmetry between entry and exit windows is deliberate: it
lets winners run while cutting the whipsaw that a symmetric rule produces.
"""

from __future__ import annotations

import pandas as pd

from .base import Signal, Strategy

__all__ = ["MomentumBreakout"]


class MomentumBreakout(Strategy):
    """Donchian-style breakout with an optional volume filter.

    Args:
        symbol: Instrument label carried on emitted signals.
        entry_window: Lookback whose high/low defines a breakout.
        exit_window: Shorter lookback used to exit.
        weight: Absolute target weight when in a position.
        allow_short: Take short breakouts as well as long.
        volume_multiple: If set, require volume above this multiple of its
            ``entry_window`` average to accept a breakout.
    """

    name = "momentum_breakout"

    def __init__(
        self,
        symbol: str = "",
        *,
        entry_window: int = 20,
        exit_window: int = 10,
        weight: float = 1.0,
        allow_short: bool = True,
        volume_multiple: float | None = None,
    ) -> None:
        if entry_window < 2:
            raise ValueError(f"entry_window must be >= 2, got {entry_window}")
        if not 1 <= exit_window <= entry_window:
            raise ValueError("exit_window must be in [1, entry_window]")
        if not 0.0 < weight <= 1.0:
            raise ValueError(f"weight must be in (0, 1], got {weight}")
        if volume_multiple is not None and volume_multiple <= 0:
            raise ValueError(f"volume_multiple must be > 0, got {volume_multiple}")

        self.symbol = symbol
        self.entry_window = entry_window
        self.exit_window = exit_window
        self.weight = weight
        self.allow_short = allow_short
        self.volume_multiple = volume_multiple
        self.warmup = entry_window + 1
        self._position = 0.0

    def evaluate(self, history: pd.DataFrame) -> Signal | None:
        close = float(history["close"].iloc[-1])
        # Exclude the current bar: comparing a close to a range that contains it
        # would make a breakout impossible by construction.
        prior = history.iloc[:-1]
        entry_hi = float(prior["high"].iloc[-self.entry_window :].max())
        entry_lo = float(prior["low"].iloc[-self.entry_window :].min())
        exit_hi = float(prior["high"].iloc[-self.exit_window :].max())
        exit_lo = float(prior["low"].iloc[-self.exit_window :].min())

        if self._position > 0 and close < exit_lo:
            self._position = 0.0
            return Signal(self.symbol, 0.0, f"Exit long: close {close:.4f} < {self.exit_window}-bar low {exit_lo:.4f}")
        if self._position < 0 and close > exit_hi:
            self._position = 0.0
            return Signal(self.symbol, 0.0, f"Exit short: close {close:.4f} > {self.exit_window}-bar high {exit_hi:.4f}")

        if self._position == 0.0 and close > entry_hi and self._volume_ok(history):
            self._position = self.weight
            return Signal(self.symbol, self.weight, f"Long breakout: close {close:.4f} > {self.entry_window}-bar high {entry_hi:.4f}")
        if (
            self.allow_short
            and self._position == 0.0
            and close < entry_lo
            and self._volume_ok(history)
        ):
            self._position = -self.weight
            return Signal(self.symbol, -self.weight, f"Short breakout: close {close:.4f} < {self.entry_window}-bar low {entry_lo:.4f}")

        if self._position == 0.0:
            return None
        return Signal(self.symbol, self._position, f"Hold {'long' if self._position > 0 else 'short'} breakout position")

    def _volume_ok(self, history: pd.DataFrame) -> bool:
        if self.volume_multiple is None or "volume" not in history.columns:
            return True
        volume = history["volume"]
        average = float(volume.iloc[-self.entry_window - 1 : -1].mean())
        if average <= 0:
            return True
        return float(volume.iloc[-1]) >= self.volume_multiple * average

    def reset(self) -> None:
        """Clear internal position state between independent runs."""
        self._position = 0.0
