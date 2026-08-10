"""Trading strategies (ICT, momentum, mean-reversion).

Each strategy consumes bar history up to the decision bar and emits a
:class:`~ai_trading.strategies.base.Signal` carrying an explicit rationale.
Strategies are directly callable, so they can be passed straight to
:class:`~ai_trading.backtest.engine.Backtester` (design-doc section 5).
"""

from .base import Side, Signal, Strategy
from .ict import ICTStrategy
from .mean_reversion import MeanReversion
from .momentum import MomentumBreakout
from .structure import Swing, Zone

__all__ = [
    "ICTStrategy",
    "MeanReversion",
    "MomentumBreakout",
    "Side",
    "Signal",
    "Strategy",
    "Swing",
    "Zone",
]
