"""Event-driven backtesting engine.

**Lookahead safety is structural, not conventional.** The decision for bar ``i``
is computed from ``bars.iloc[:i + 1]`` — data through bar ``i``'s close — and
the resulting order fills at bar ``i + 1``'s *open*. A strategy therefore cannot
see a price it would not have had, even by accident, because the engine never
hands it a longer slice. The final bar's signal is intentionally never executed:
there is no subsequent bar to fill against.

Costs are folded into the fill price so that cash, equity, and realized trade
PnL stay exactly consistent with one another:

``fill = open * (1 ± slippage) * (1 ± commission)``

with the sign following the trade direction, so both always work against you.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from . import metrics as _metrics

__all__ = ["Backtester", "BacktestResult", "Fill", "Trade", "SignalFn"]

#: A strategy callable: receives all bars up to and including the decision bar,
#: returns a target position weight (fraction of equity; negative is short).
SignalFn = Callable[[pd.DataFrame], float]

REQUIRED_COLUMNS = ("open", "close")


@dataclass(frozen=True)
class Fill:
    """A single executed order."""

    timestamp: pd.Timestamp
    units: float  # signed: positive buys, negative sells
    price: float  # all-in fill price, inclusive of slippage and commission


@dataclass(frozen=True)
class Trade:
    """A closed round trip, with PnL net of costs."""

    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    units: float  # absolute size closed
    entry_price: float
    exit_price: float
    direction: str  # "long" or "short"
    pnl: float


@dataclass
class BacktestResult:
    """Output of a backtest run."""

    equity: pd.Series
    positions: pd.Series
    fills: list[Fill] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def trade_pnls(self) -> list[float]:
        return [t.pnl for t in self.trades]

    def to_frame(self) -> pd.DataFrame:
        """Equity curve and position series as a single frame."""
        return pd.DataFrame({"equity": self.equity, "position": self.positions})


class Backtester:
    """Simulates strategy execution over historical bars.

    Args:
        initial_capital: Starting cash.
        commission_bps: Per-side commission in basis points of notional.
        slippage_bps: Per-side slippage in basis points, always adverse.
        periods_per_year: Bars per year, used to annualize metrics (252 daily
            equities, 365 daily crypto, 8760 hourly...).
        max_weight: Absolute cap on target weight; signals are clamped to
            ``[-max_weight, +max_weight]``.
    """

    def __init__(
        self,
        initial_capital: float = 100_000.0,
        *,
        commission_bps: float = 1.0,
        slippage_bps: float = 1.0,
        periods_per_year: int = 252,
        max_weight: float = 1.0,
    ) -> None:
        if initial_capital <= 0:
            raise ValueError(f"initial_capital must be > 0, got {initial_capital}")
        if commission_bps < 0 or slippage_bps < 0:
            raise ValueError("commission_bps and slippage_bps must be >= 0")
        if periods_per_year <= 0:
            raise ValueError(f"periods_per_year must be > 0, got {periods_per_year}")
        if max_weight <= 0:
            raise ValueError(f"max_weight must be > 0, got {max_weight}")

        self.initial_capital = float(initial_capital)
        self.commission_rate = commission_bps / 10_000.0
        self.slippage_rate = slippage_bps / 10_000.0
        self.periods_per_year = periods_per_year
        self.max_weight = float(max_weight)

    def run(self, bars: pd.DataFrame, signal_fn: SignalFn) -> BacktestResult:
        """Run ``signal_fn`` over ``bars`` and return the simulated result."""
        self._validate(bars)

        cash = self.initial_capital
        units = 0.0
        avg_price = 0.0  # all-in average cost basis of the open position
        entry_time: pd.Timestamp | None = None

        fills: list[Fill] = []
        trades: list[Trade] = []
        equity_curve: list[float] = []
        position_curve: list[float] = []

        n = len(bars)
        opens = bars["open"].to_numpy(dtype="float64")
        closes = bars["close"].to_numpy(dtype="float64")
        index = bars.index

        for i in range(n):
            # Mark to market on this bar's close.
            equity = cash + units * closes[i]
            equity_curve.append(equity)
            position_curve.append(units)

            # The last bar has no successor to fill against, so no decision.
            if i == n - 1:
                break

            weight = self._clamp(signal_fn(bars.iloc[: i + 1]))
            if weight is None:
                continue

            # Size against equity and price known at decision time.
            target_units = weight * equity / closes[i]
            delta = target_units - units
            if delta == 0:
                continue

            fill_price = self._fill_price(opens[i + 1], delta)
            fill_time = index[i + 1]
            cash -= delta * fill_price
            fills.append(Fill(fill_time, delta, fill_price))

            units, avg_price, entry_time, closed = _apply_fill(
                units, avg_price, entry_time, delta, fill_price, fill_time
            )
            trades.extend(closed)

        equity = pd.Series(equity_curve, index=index[: len(equity_curve)], name="equity")
        positions = pd.Series(position_curve, index=index[: len(position_curve)], name="position")

        result = BacktestResult(equity=equity, positions=positions, fills=fills, trades=trades)
        result.metrics = _metrics.summarize(
            equity, [t.pnl for t in trades], self.periods_per_year
        )
        return result

    # -- internals ---------------------------------------------------------

    def _fill_price(self, reference_price: float, delta: float) -> float:
        """Apply slippage and commission adversely to the reference price."""
        sign = 1.0 if delta > 0 else -1.0
        return reference_price * (1.0 + sign * self.slippage_rate) * (
            1.0 + sign * self.commission_rate
        )

    def _clamp(self, weight: float) -> float | None:
        """Clamp a target weight; ``None`` signals 'no decision this bar'."""
        value = float(weight)
        if value != value:  # NaN -- strategy has insufficient history
            return None
        return max(-self.max_weight, min(self.max_weight, value))

    @staticmethod
    def _validate(bars: pd.DataFrame) -> None:
        missing = [c for c in REQUIRED_COLUMNS if c not in bars.columns]
        if missing:
            raise KeyError(f"bars is missing required column(s): {missing}")
        if not isinstance(bars.index, pd.DatetimeIndex):
            raise TypeError("bars must be indexed by a DatetimeIndex")
        if not bars.index.is_monotonic_increasing:
            raise ValueError("bars index must be sorted ascending")
        if len(bars) < 2:
            raise ValueError("need at least 2 bars to run a backtest")
        if (bars[["open", "close"]] <= 0).to_numpy().any():
            raise ValueError("bars contain non-positive prices")


def _apply_fill(
    units: float,
    avg_price: float,
    entry_time: pd.Timestamp | None,
    delta: float,
    fill_price: float,
    fill_time: pd.Timestamp,
) -> tuple[float, float, pd.Timestamp | None, list[Trade]]:
    """Update position state for a fill, realizing PnL on any reduction.

    Returns the new ``(units, avg_price, entry_time, closed_trades)``.
    """
    closed: list[Trade] = []
    new_units = units + delta

    increasing = units == 0 or (units > 0) == (delta > 0)
    if increasing:
        if units == 0:
            entry_time = fill_time
        total = abs(units) + abs(delta)
        avg_price = (avg_price * abs(units) + fill_price * abs(delta)) / total
        return new_units, avg_price, entry_time, closed

    # Reducing, closing, or flipping: realize PnL on the portion closed.
    closed_units = min(abs(delta), abs(units))
    direction = "long" if units > 0 else "short"
    # Long profits when exit > entry; short profits when entry > exit.
    pnl = closed_units * (fill_price - avg_price) * (1.0 if units > 0 else -1.0)
    closed.append(
        Trade(
            entry_time=entry_time if entry_time is not None else fill_time,
            exit_time=fill_time,
            units=closed_units,
            entry_price=avg_price,
            exit_price=fill_price,
            direction=direction,
            pnl=pnl,
        )
    )

    if abs(delta) > abs(units):
        # Flipped through zero: the remainder opens a fresh position.
        return new_units, fill_price, fill_time, closed
    if new_units == 0:
        return 0.0, 0.0, None, closed
    return new_units, avg_price, entry_time, closed
