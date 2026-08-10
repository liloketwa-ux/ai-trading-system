"""Market-structure primitives for Smart Money Concept / ICT strategies.

These detect swing pivots, breaks of structure, fair value gaps, order blocks,
and liquidity sweeps.

**The causality trap.** A swing high is only a swing high once enough bars have
printed to its *right* to confirm it. A naive implementation marks the pivot at
its own bar index and then lets a backtest act on it there — which is trading on
information that did not exist yet, and is the single most common way ICT
backtests manufacture fake edge.

Every detector here avoids that the same way: it only confirms a pattern when
the confirming bars are present *inside the frame it was given*. Called on
``history[:i + 1]``, :func:`find_swings` therefore cannot report a pivot more
recent than ``i - right``, and every :class:`Zone` carries the index at which it
became actionable. Callers must respect ``Zone.confirmed_index``; the helper
:func:`zones_active_at` does it for you.

These are mechanical approximations of a discretionary methodology. They encode
one defensible reading of the rules, not the only one.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

__all__ = [
    "Swing",
    "Zone",
    "find_swings",
    "last_confirmed_swings",
    "fair_value_gaps",
    "order_blocks",
    "liquidity_sweeps",
    "zones_active_at",
    "is_mitigated",
]

REQUIRED = ("open", "high", "low", "close")


@dataclass(frozen=True)
class Swing:
    """A confirmed swing pivot.

    Attributes:
        index: Positional index of the pivot bar itself.
        price: The pivot's extreme price.
        kind: ``"high"`` or ``"low"``.
        confirmed_index: The bar at which the pivot became knowable
            (``index + right``). Never act on the pivot before this bar.
    """

    index: int
    price: float
    kind: str
    confirmed_index: int


@dataclass(frozen=True)
class Zone:
    """A price zone of interest (order block or fair value gap).

    Attributes:
        kind: One of ``bullish_ob``, ``bearish_ob``, ``bullish_fvg``,
            ``bearish_fvg``.
        lower: Lower edge of the zone.
        upper: Upper edge of the zone.
        formed_index: Bar that created the zone.
        confirmed_index: Bar at which the zone became actionable. For fair
            value gaps this equals ``formed_index``; for order blocks it is the
            later bar whose break of structure validated it.
    """

    kind: str
    lower: float
    upper: float
    formed_index: int
    confirmed_index: int

    @property
    def is_bullish(self) -> bool:
        return self.kind.startswith("bullish")

    @property
    def midpoint(self) -> float:
        return (self.lower + self.upper) / 2.0

    def contains(self, price: float) -> bool:
        """True when ``price`` sits inside the zone (edges inclusive)."""
        return self.lower <= price <= self.upper


def find_swings(bars: pd.DataFrame, left: int = 2, right: int = 2) -> list[Swing]:
    """Find confirmed swing pivots.

    A swing high at bar ``j`` is a high strictly greater than the ``left`` highs
    before it and the ``right`` highs after it (swing lows mirror this). Because
    the right-hand bars must exist within ``bars``, no pivot is returned for the
    final ``right`` bars — which is exactly what makes this safe to call on a
    growing history.

    Args:
        bars: OHLC frame.
        left: Bars required to the left of the pivot.
        right: Bars required to the right to confirm it.
    """
    _validate(bars)
    if left < 1 or right < 1:
        raise ValueError(f"left and right must be >= 1, got {left}, {right}")

    highs = bars["high"].to_numpy(dtype="float64")
    lows = bars["low"].to_numpy(dtype="float64")
    n = len(bars)
    swings: list[Swing] = []

    for j in range(left, n - right):
        window_hi = highs[j - left : j + right + 1]
        if highs[j] == window_hi.max() and (window_hi == highs[j]).sum() == 1:
            swings.append(Swing(j, float(highs[j]), "high", j + right))
        window_lo = lows[j - left : j + right + 1]
        if lows[j] == window_lo.min() and (window_lo == lows[j]).sum() == 1:
            swings.append(Swing(j, float(lows[j]), "low", j + right))

    swings.sort(key=lambda s: s.confirmed_index)
    return swings


def last_confirmed_swings(
    swings: list[Swing], as_of: int
) -> tuple[Swing | None, Swing | None]:
    """Most recent swing high and low confirmed at or before bar ``as_of``."""
    high = low = None
    for s in swings:
        if s.confirmed_index > as_of:
            continue
        if s.kind == "high" and (high is None or s.index > high.index):
            high = s
        elif s.kind == "low" and (low is None or s.index > low.index):
            low = s
    return high, low


def fair_value_gaps(bars: pd.DataFrame, min_gap_pct: float = 0.0) -> list[Zone]:
    """Detect three-candle fair value gaps (imbalances).

    A bullish FVG exists at bar ``j`` when ``low[j] > high[j - 2]`` — price
    moved up so fast that the middle candle left an untraded gap. The bearish
    case mirrors it. Detection needs no future bars, so ``confirmed_index``
    equals ``formed_index``.

    Args:
        bars: OHLC frame.
        min_gap_pct: Minimum gap width as a fraction of price, used to filter
            out negligible imbalances.
    """
    _validate(bars)
    if min_gap_pct < 0:
        raise ValueError(f"min_gap_pct must be >= 0, got {min_gap_pct}")

    highs = bars["high"].to_numpy(dtype="float64")
    lows = bars["low"].to_numpy(dtype="float64")
    closes = bars["close"].to_numpy(dtype="float64")
    zones: list[Zone] = []

    for j in range(2, len(bars)):
        if lows[j] > highs[j - 2]:
            gap = lows[j] - highs[j - 2]
            if gap / closes[j] >= min_gap_pct:
                zones.append(Zone("bullish_fvg", float(highs[j - 2]), float(lows[j]), j, j))
        elif highs[j] < lows[j - 2]:
            gap = lows[j - 2] - highs[j]
            if gap / closes[j] >= min_gap_pct:
                zones.append(Zone("bearish_fvg", float(highs[j]), float(lows[j - 2]), j, j))

    return zones


def order_blocks(bars: pd.DataFrame, left: int = 2, right: int = 2) -> list[Zone]:
    """Detect order blocks validated by a break of structure.

    A bullish order block is the last down-close candle before an impulse that
    closes above the most recently confirmed swing high. The zone spans that
    candle's low-to-high range, and becomes actionable only at the breaking bar
    — so ``confirmed_index`` is the break, not the candle.
    """
    _validate(bars)
    swings = find_swings(bars, left, right)
    opens = bars["open"].to_numpy(dtype="float64")
    highs = bars["high"].to_numpy(dtype="float64")
    lows = bars["low"].to_numpy(dtype="float64")
    closes = bars["close"].to_numpy(dtype="float64")

    zones: list[Zone] = []
    last_break_high: float | None = None
    last_break_low: float | None = None

    for i in range(len(bars)):
        swing_high, swing_low = last_confirmed_swings(swings, i)

        if swing_high is not None and closes[i] > swing_high.price:
            # Break of structure up; the origin is the last down candle before it.
            if last_break_high != swing_high.price:
                origin = _last_down_candle(opens, closes, swing_high.index, i)
                if origin is not None:
                    zones.append(
                        Zone("bullish_ob", float(lows[origin]), float(highs[origin]), origin, i)
                    )
                last_break_high = swing_high.price

        if swing_low is not None and closes[i] < swing_low.price:
            if last_break_low != swing_low.price:
                origin = _last_up_candle(opens, closes, swing_low.index, i)
                if origin is not None:
                    zones.append(
                        Zone("bearish_ob", float(lows[origin]), float(highs[origin]), origin, i)
                    )
                last_break_low = swing_low.price

    return zones


def liquidity_sweeps(bars: pd.DataFrame, left: int = 2, right: int = 2) -> list[Zone]:
    """Detect liquidity sweeps (stop runs).

    A bullish sweep is a bar whose *low* pierces a confirmed swing low but whose
    *close* recovers back above it — stops were taken and price rejected the
    level. Returned as a zone spanning the wick, confirmed at the sweeping bar.
    """
    _validate(bars)
    swings = find_swings(bars, left, right)
    highs = bars["high"].to_numpy(dtype="float64")
    lows = bars["low"].to_numpy(dtype="float64")
    closes = bars["close"].to_numpy(dtype="float64")

    zones: list[Zone] = []
    for i in range(len(bars)):
        swing_high, swing_low = last_confirmed_swings(swings, i)
        if swing_low is not None and lows[i] < swing_low.price <= closes[i]:
            zones.append(Zone("bullish_ob", float(lows[i]), float(swing_low.price), i, i))
        if swing_high is not None and highs[i] > swing_high.price >= closes[i]:
            zones.append(Zone("bearish_ob", float(swing_high.price), float(highs[i]), i, i))
    return zones


def zones_active_at(zones: list[Zone], as_of: int, max_age: int | None = None) -> list[Zone]:
    """Zones confirmed at or before ``as_of``, optionally dropping stale ones.

    Args:
        zones: Candidate zones.
        as_of: Current bar index.
        max_age: If given, exclude zones confirmed more than this many bars ago.
    """
    out = [z for z in zones if z.confirmed_index <= as_of]
    if max_age is not None:
        out = [z for z in out if as_of - z.confirmed_index <= max_age]
    return out


def is_mitigated(zone: Zone, bars: pd.DataFrame, as_of: int) -> bool:
    """True once price has traded back into the zone after it formed.

    A mitigated zone has already been "used" and is conventionally not traded
    again. Only bars in ``(confirmed_index, as_of]`` are considered.
    """
    _validate(bars)
    highs = bars["high"].to_numpy(dtype="float64")
    lows = bars["low"].to_numpy(dtype="float64")
    for i in range(zone.confirmed_index + 1, min(as_of, len(bars) - 1) + 1):
        if lows[i] <= zone.upper and highs[i] >= zone.lower:
            return True
    return False


# -- internals -------------------------------------------------------------


def _last_down_candle(opens, closes, start: int, end: int) -> int | None:
    """Index of the last down-close candle in ``[start, end)``, searching back."""
    for j in range(end - 1, start - 1, -1):
        if closes[j] < opens[j]:
            return j
    return None


def _last_up_candle(opens, closes, start: int, end: int) -> int | None:
    for j in range(end - 1, start - 1, -1):
        if closes[j] > opens[j]:
            return j
    return None


def _validate(bars: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED if c not in bars.columns]
    if missing:
        raise KeyError(f"bars is missing required column(s): {missing}")
