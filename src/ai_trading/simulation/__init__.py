"""Event-driven backtest simulation (Phase 6).

Produces research evidence under explicit execution assumptions. It does not
produce a trading strategy, and no live execution path exists.
"""

from .config import BacktestConfig
from .contracts import CONTRACTS, ContractSpec, roll_date
from .engine import BacktestEngine, PointInTimeState, SimStrategy, TradeCandidate
from .events import EventType, SimEvent, make_bar_event
from .execution import (
    ExecutionConfig,
    ExecutionSimulator,
    Fill,
    FixedTickSlippage,
    OrderSide,
    OrderState,
    OrderType,
    PercentageSlippage,
    SimOrder,
    SlippageModel,
    SpreadSlippage,
    VolatilityAdjustedSlippage,
)
from .portfolio import Account, EquityPoint, Position
from .results import BacktestResult, TradeRecord

__all__ = [
    "CONTRACTS", "Account", "BacktestConfig", "BacktestEngine", "BacktestResult",
    "ContractSpec", "EquityPoint", "EventType", "ExecutionConfig",
    "ExecutionSimulator", "Fill", "FixedTickSlippage", "OrderSide", "OrderState",
    "OrderType", "PercentageSlippage", "PointInTimeState", "Position", "SimEvent",
    "SimOrder", "SimStrategy", "SlippageModel", "SpreadSlippage", "TradeCandidate",
    "TradeRecord", "VolatilityAdjustedSlippage", "make_bar_event", "roll_date",
]
