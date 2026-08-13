"""Backtesting engine and performance metrics (design-doc section 6).

The engine is structurally lookahead-safe: decisions for bar ``i`` see only
bars up to ``i`` and fill at bar ``i + 1``'s open.
"""

from .adjudicate import SessionRecord, adjudicate
from .challenge import (
    ChallengeResult,
    ChallengeRules,
    DrawdownType,
    Outcome,
    evaluate_challenge,
)
from .engine import BacktestResult, Backtester, Fill, SignalFn, Trade
from .ruleset import (
    APEX_LIKE,
    FTMO_LIKE,
    TOPSTEP_LIKE,
    DeadlineBasis,
    DrawdownPolicy,
    EquityBasis,
    FirmRuleset,
    LockingTrailingDrawdown,
    MinDayRule,
    StaticDrawdown,
    TrailingDrawdown,
    session_days,
)
from .metrics import (
    cagr,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
    summarize,
    win_rate,
)

__all__ = [
    "APEX_LIKE",
    "FTMO_LIKE",
    "TOPSTEP_LIKE",
    "BacktestResult",
    "Backtester",
    "ChallengeResult",
    "ChallengeRules",
    "DrawdownType",
    "Outcome",
    "DeadlineBasis",
    "DrawdownPolicy",
    "EquityBasis",
    "FirmRuleset",
    "LockingTrailingDrawdown",
    "MinDayRule",
    "SessionRecord",
    "StaticDrawdown",
    "TrailingDrawdown",
    "adjudicate",
    "evaluate_challenge",
    "session_days",
    "Fill",
    "SignalFn",
    "Trade",
    "cagr",
    "max_drawdown",
    "profit_factor",
    "sharpe_ratio",
    "sortino_ratio",
    "summarize",
    "win_rate",
]
