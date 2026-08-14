"""Backtest result objects.

Performance is reported next to the execution assumptions that produced it.
``ambiguous_bar_count`` in particular belongs on the headline record: it
measures how much of the result rests on the stop-wins convention rather than
on observed order of execution.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

__all__ = ["TradeRecord", "BacktestResult", "summarize"]


@dataclass(frozen=True)
class TradeRecord:
    """One completed round trip."""

    instrument: str
    direction: int
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    contracts: float
    pnl: float
    costs: float
    outcome: str                     # "stop" | "target" | "timeout"
    mae: float = 0.0
    mfe: float = 0.0
    ambiguous_bar: bool = False
    hypothesis_id: str = ""
    r_multiple: float | None = None

    @property
    def won(self) -> bool:
        return self.pnl > 0

    @property
    def duration(self):
        return self.exit_time - self.entry_time


@dataclass
class BacktestResult:
    """Research evidence from one simulation run."""

    run_id: str
    instrument: str
    config: dict[str, Any]
    trade_count: int
    win_rate: float
    average_win: float
    average_loss: float
    expectancy: float
    profit_factor: float
    sharpe: float
    sortino: float
    max_drawdown: float
    max_daily_drawdown: float
    longest_losing_streak: int
    average_r: float | None
    mae: float
    mfe: float
    turnover: float
    total_costs: float
    gross_return: float
    net_return: float
    ambiguous_bar_count: int
    ambiguous_trade_fraction: float
    execution_delay_ms: float
    slippage_model: str
    commission_model: str
    events_processed: int
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: list = field(default_factory=list)

    @property
    def simulation_capable(self) -> bool:
        """Whether the run produced enough trades to say anything at all."""
        return self.trade_count >= 30

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id, "instrument": self.instrument,
            "trade_count": self.trade_count, "win_rate": self.win_rate,
            "average_win": self.average_win, "average_loss": self.average_loss,
            "expectancy": self.expectancy, "profit_factor": self.profit_factor,
            "sharpe": self.sharpe, "sortino": self.sortino,
            "max_drawdown": self.max_drawdown,
            "max_daily_drawdown": self.max_daily_drawdown,
            "longest_losing_streak": self.longest_losing_streak,
            "average_r": self.average_r, "mae": self.mae, "mfe": self.mfe,
            "turnover": self.turnover, "total_costs": self.total_costs,
            "gross_return": self.gross_return, "net_return": self.net_return,
            "ambiguous_bar_count": self.ambiguous_bar_count,
            "ambiguous_trade_fraction": self.ambiguous_trade_fraction,
            "execution_delay_ms": self.execution_delay_ms,
            "slippage_model": self.slippage_model,
            "commission_model": self.commission_model,
            "events_processed": self.events_processed,
            "config": self.config,
        }

    def render(self) -> str:
        return "\n".join([
            f"Run              : {self.run_id}  ({self.instrument})",
            f"Trades           : {self.trade_count}  win rate {self.win_rate:.1%}",
            f"Expectancy       : {self.expectancy:+.2f} per trade",
            f"Profit factor    : {self.profit_factor:.2f}",
            f"Net return       : {self.net_return:+.2%}  (gross {self.gross_return:+.2%})",
            f"Total costs      : {self.total_costs:,.2f}",
            f"Max drawdown     : {self.max_drawdown:.2%}  (daily {self.max_daily_drawdown:.2%})",
            f"Sharpe / Sortino : {self.sharpe:.2f} / {self.sortino:.2f}",
            f"Longest losses   : {self.longest_losing_streak}",
            f"Average R        : {self.average_r if self.average_r is None else round(self.average_r, 3)}",
            f"Ambiguous bars   : {self.ambiguous_bar_count} "
            f"({self.ambiguous_trade_fraction:.1%} of trades)",
            f"Execution        : {self.execution_delay_ms:.0f}ms delay, "
            f"{self.slippage_model} slippage, {self.commission_model}",
        ])


def summarize(*, config, spec, account, trades, fills, ambiguous_bar_count,
              events_processed) -> BacktestResult:
    """Build the result record from raw simulation state."""
    wins = [t.pnl for t in trades if t.won]
    losses = [t.pnl for t in trades if not t.won]
    pnls = [t.pnl for t in trades]

    streak = longest = 0
    for trade in trades:
        streak = 0 if trade.won else streak + 1
        longest = max(longest, streak)

    equity = [p.equity for p in account.curve]
    returns = np.diff(equity) / np.array(equity[:-1]) if len(equity) > 1 else np.array([])
    finite = returns[np.isfinite(returns)] if returns.size else returns

    def annualized(values, downside_only=False):
        if values.size < 2:
            return float("nan")
        target = values[values < 0] if downside_only else values
        denominator = (
            math.sqrt((target ** 2).mean()) if downside_only and target.size
            else values.std(ddof=1)
        )
        if not denominator or not np.isfinite(denominator):
            return float("nan")
        return float(values.mean() / denominator * math.sqrt(252))

    gross_pnl = sum(pnls) + account.total_costs
    r_values = [t.r_multiple for t in trades if t.r_multiple is not None]

    return BacktestResult(
        run_id=config.run_id,
        instrument=spec.symbol,
        config=config.to_dict(),
        trade_count=len(trades),
        win_rate=len(wins) / len(trades) if trades else float("nan"),
        average_win=float(np.mean(wins)) if wins else 0.0,
        average_loss=float(np.mean(losses)) if losses else 0.0,
        expectancy=float(np.mean(pnls)) if pnls else 0.0,
        profit_factor=(
            sum(wins) / abs(sum(losses)) if losses and sum(losses) else
            (float("inf") if wins else float("nan"))
        ),
        sharpe=annualized(finite),
        sortino=annualized(finite, downside_only=True),
        max_drawdown=account.max_drawdown,
        max_daily_drawdown=account.max_daily_drawdown,
        longest_losing_streak=longest,
        average_r=float(np.mean(r_values)) if r_values else None,
        mae=float(np.mean([t.mae for t in trades])) if trades else 0.0,
        mfe=float(np.mean([t.mfe for t in trades])) if trades else 0.0,
        turnover=sum(abs(f.quantity) for f in fills),
        total_costs=account.total_costs,
        gross_return=gross_pnl / account.starting_balance,
        net_return=account.net_return,
        ambiguous_bar_count=ambiguous_bar_count,
        ambiguous_trade_fraction=(
            sum(1 for t in trades if t.ambiguous_bar) / len(trades) if trades else 0.0
        ),
        execution_delay_ms=config.execution.latency.total_seconds() * 1000,
        slippage_model=config.execution.slippage.name,
        commission_model=f"{config.execution.commission_per_contract}/contract",
        events_processed=events_processed,
        trades=list(trades),
        equity_curve=list(account.curve),
    )
