"""Mean-reversion strategy.

Fades statistically extreme moves: short when price stretches far above its
rolling mean, long when it stretches far below, exiting as it reverts. An
optional sentiment filter blocks the fade when the crowd agrees with the move,
which is the case where "extreme" more often means "trending" than "stretched".
"""

from __future__ import annotations

import pandas as pd

from ..features import indicators as ind
from .base import Signal, Strategy

__all__ = ["MeanReversion"]


class MeanReversion(Strategy):
    """Rolling z-score fade with a symmetric exit band.

    Args:
        symbol: Instrument label carried on emitted signals.
        window: Lookback for the rolling mean and standard deviation.
        entry_z: Absolute z-score at which to fade the move.
        exit_z: Absolute z-score at which to close back toward flat.
        weight: Absolute target weight when in a position.
        sentiment_col: Optional column in ``history``; when present, a fade is
            skipped if sentiment points the same way as the move.
    """

    name = "mean_reversion"

    def __init__(
        self,
        symbol: str = "",
        *,
        window: int = 20,
        entry_z: float = 2.0,
        exit_z: float = 0.5,
        weight: float = 1.0,
        sentiment_col: str | None = None,
    ) -> None:
        if window < 2:
            raise ValueError(f"window must be >= 2, got {window}")
        if entry_z <= 0:
            raise ValueError(f"entry_z must be > 0, got {entry_z}")
        if not 0.0 <= exit_z < entry_z:
            raise ValueError("exit_z must be in [0, entry_z)")
        if not 0.0 < weight <= 1.0:
            raise ValueError(f"weight must be in (0, 1], got {weight}")

        self.symbol = symbol
        self.window = window
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.weight = weight
        self.sentiment_col = sentiment_col
        self.warmup = window + 1
        self._position = 0.0

    def evaluate(self, history: pd.DataFrame) -> Signal | None:
        z_series = ind.zscore(history["close"], self.window)
        z = float(z_series.iloc[-1]) if pd.notna(z_series.iloc[-1]) else 0.0

        if self._position != 0.0 and abs(z) <= self.exit_z:
            self._position = 0.0
            return Signal(self.symbol, 0.0, f"Exit: z-score {z:+.2f} reverted inside +/-{self.exit_z}")

        if self._position == 0.0 and z >= self.entry_z and self._sentiment_ok(history, short=True):
            self._position = -self.weight
            return Signal(self.symbol, -self.weight, f"Short fade: z-score {z:+.2f} >= {self.entry_z}", self._confidence(z))
        if self._position == 0.0 and z <= -self.entry_z and self._sentiment_ok(history, short=False):
            self._position = self.weight
            return Signal(self.symbol, self.weight, f"Long fade: z-score {z:+.2f} <= -{self.entry_z}", self._confidence(z))

        if self._position == 0.0:
            return None
        return Signal(self.symbol, self._position, f"Hold fade, z-score {z:+.2f}")

    def _sentiment_ok(self, history: pd.DataFrame, *, short: bool) -> bool:
        """Block the fade when sentiment agrees with the move it would fight."""
        if self.sentiment_col is None or self.sentiment_col not in history.columns:
            return True
        value = history[self.sentiment_col].iloc[-1]
        if pd.isna(value):
            return True
        return float(value) < 0 if short else float(value) > 0

    def _confidence(self, z: float) -> float:
        excess = (abs(z) - self.entry_z) / max(self.entry_z, 1e-9)
        return round(min(1.0, 0.5 + 0.5 * excess), 4)

    def reset(self) -> None:
        """Clear internal position state between independent runs."""
        self._position = 0.0
