"""Strategy and signal interfaces.

A strategy serves two consumers with one implementation:

* the **live path**, which wants a rich :class:`Signal` carrying an explicit
  human-readable rationale (this is what ends up in an alert or a chart label);
* the **backtester**, which wants a single float target weight.

:meth:`Strategy.evaluate` produces the former; :meth:`Strategy.target_weight`
adapts it to the latter, and ``__call__`` makes any strategy directly usable as
a :data:`~ai_trading.backtest.engine.SignalFn`.

Strategies receive *history* — bars up to and including the decision bar — and
must never assume anything about bars beyond it. The backtester enforces this
by construction, but strategies are also written to hold under direct use.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

import pandas as pd

Side = Literal["long", "short", "flat"]

__all__ = ["Side", "Signal", "Strategy"]


@dataclass(frozen=True)
class Signal:
    """A trade signal with an explicit, human-readable rationale.

    ``weight`` is the signed target position as a fraction of equity and is the
    source of truth for direction; ``side`` is derived from its sign so the two
    can never disagree.
    """

    symbol: str
    weight: float
    rationale: str
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not -1.0 <= self.weight <= 1.0:
            raise ValueError(f"weight must be in [-1, 1], got {self.weight}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")
        if not self.rationale.strip():
            raise ValueError("rationale must not be empty")

    @property
    def side(self) -> Side:
        if self.weight > 0:
            return "long"
        if self.weight < 0:
            return "short"
        return "flat"


class Strategy(ABC):
    """Base class for all strategies.

    Subclasses implement :meth:`evaluate`. ``warmup`` declares how many bars of
    history are needed before the strategy can decide anything; below it the
    strategy abstains (``NaN`` target weight), which the backtester reads as
    "no decision this bar" rather than "go flat".

    .. note::
       Some strategies track their own position to implement asymmetric entry
       and exit rules. Those are **stateful**, and assume they are called once
       per bar in chronological order — which is exactly how
       :class:`~ai_trading.backtest.engine.Backtester` drives them. Calling
       such a strategy twice for the same bar, or out of order, corrupts that
       state; call ``reset()`` before reusing an instance for a second run.
       Stateless strategies (such as :class:`~ai_trading.strategies.ict.ICTStrategy`)
       have no such constraint.
    """

    name: str = "base"
    symbol: str = ""

    #: Bars of history required before the strategy will emit a decision.
    warmup: int = 1

    @abstractmethod
    def evaluate(self, history: pd.DataFrame) -> Signal | None:
        """Return a signal for the decision bar, or ``None`` to hold flat.

        ``history`` ends at the decision bar. Implementations must not index
        past its final row.
        """
        raise NotImplementedError

    def target_weight(self, history: pd.DataFrame) -> float:
        """Adapt :meth:`evaluate` to the backtester's float interface.

        Returns ``NaN`` during warmup (abstain), ``0.0`` when the strategy
        returns no signal (deliberately flat), else the signal's weight.
        """
        if len(history) < self.warmup:
            return float("nan")
        signal = self.evaluate(history)
        return 0.0 if signal is None else signal.weight

    def __call__(self, history: pd.DataFrame) -> float:
        return self.target_weight(history)
