"""Prop-firm evaluation ("challenge") rules and pass/fail adjudication.

Funded-account evaluations impose a rule set that is much harsher than a plain
return target: hit a profit goal *without* ever breaching a daily loss limit or
a maximum drawdown, across a minimum number of trading days. The asymmetry is
the point — one bad day ends the attempt regardless of how good the equity
curve looked beforehand.

Two details dominate whether an account survives, and firms differ on both:

* **Daily loss** is measured from the *start-of-day* equity, so a loss limit
  resets each day and a strategy can bleed indefinitely without breaching it.
* **Maximum drawdown** is either *static* (measured from the initial balance)
  or *trailing* (measured from peak equity). Trailing is materially harder: it
  ratchets up behind a winning account and never gives ground back.

.. note::
   Adjudication is only as granular as the equity series it is given. Daily
   bars show one equity point per day, so an intraday spike through a limit
   that recovered by the close is invisible. Real evaluations measure equity
   continuously, including floating PnL, so a pass computed from daily closes
   is optimistic. Feed intrabar equity if you need a faithful answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd

__all__ = ["DrawdownType", "Outcome", "ChallengeRules", "ChallengeResult", "evaluate_challenge"]


class DrawdownType(str, Enum):
    STATIC = "static"  # measured from the initial balance
    TRAILING = "trailing"  # measured from peak equity


class Outcome(str, Enum):
    PASSED = "passed"
    FAILED_DAILY_LOSS = "failed_daily_loss"
    FAILED_DRAWDOWN = "failed_drawdown"
    FAILED_TIMEOUT = "failed_timeout"
    INCOMPLETE = "incomplete"  # ran out of data before passing or failing

    @property
    def is_pass(self) -> bool:
        return self is Outcome.PASSED


@dataclass(frozen=True)
class ChallengeRules:
    """A funded-account evaluation rule set.

    Defaults follow the common two-step-evaluation shape (10% target, 5% daily
    loss, 10% maximum drawdown). Always check the firm's own terms — these
    numbers, and especially the drawdown type, vary between firms.

    Attributes:
        profit_target: Gain over the initial balance required to pass.
        max_daily_loss: Loss from start-of-day equity that fails the account.
        max_drawdown: Loss from the reference point that fails the account.
        drawdown_type: Whether drawdown is measured from the initial balance
            or from peak equity.
        min_trading_days: Days with activity required before a pass counts.
        max_days: Calendar-day deadline, or ``None`` for unlimited.
    """

    profit_target: float = 0.10
    max_daily_loss: float = 0.05
    max_drawdown: float = 0.10
    drawdown_type: DrawdownType = DrawdownType.STATIC
    min_trading_days: int = 4
    max_days: int | None = 30

    def __post_init__(self) -> None:
        if self.profit_target <= 0:
            raise ValueError(f"profit_target must be > 0, got {self.profit_target}")
        if not 0.0 < self.max_daily_loss <= 1.0:
            raise ValueError(f"max_daily_loss must be in (0, 1], got {self.max_daily_loss}")
        if not 0.0 < self.max_drawdown <= 1.0:
            raise ValueError(f"max_drawdown must be in (0, 1], got {self.max_drawdown}")
        if self.min_trading_days < 0:
            raise ValueError(f"min_trading_days must be >= 0, got {self.min_trading_days}")
        if self.max_days is not None and self.max_days < 1:
            raise ValueError(f"max_days must be >= 1, got {self.max_days}")


@dataclass(frozen=True)
class ChallengeResult:
    """Adjudication of one evaluation attempt."""

    outcome: Outcome
    days_elapsed: int
    trading_days: int
    final_equity: float
    peak_equity: float
    return_pct: float
    worst_daily_loss: float
    worst_drawdown: float
    breach_date: pd.Timestamp | None = None
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.outcome.is_pass

    def __bool__(self) -> bool:
        return self.passed

    def summary(self) -> str:
        return (
            f"{self.outcome.value}: {self.return_pct:+.2%} over {self.days_elapsed} days "
            f"({self.trading_days} trading), worst day {self.worst_daily_loss:.2%}, "
            f"worst drawdown {self.worst_drawdown:.2%}"
            + (f" -- {self.detail}" if self.detail else "")
        )


def evaluate_challenge(
    equity: pd.Series,
    rules: ChallengeRules | None = None,
    *,
    initial_balance: float | None = None,
) -> ChallengeResult:
    """Adjudicate an equity curve against an evaluation rule set.

    The curve is walked forward one day at a time. Within each day, breaches
    are checked before the profit target, so an account that hits its target
    and blows its daily loss limit on the same day fails — which is how firms
    adjudicate it.

    Args:
        equity: Account equity indexed by a ``DatetimeIndex``. May be finer
            than daily; it is grouped by calendar day.
        rules: The rule set. Defaults to :class:`ChallengeRules`.
        initial_balance: Starting balance. Defaults to the first equity value.

    Returns:
        A :class:`ChallengeResult`, which is truthy when the attempt passed.
    """
    rules = rules or ChallengeRules()
    equity = equity.dropna()
    if equity.empty:
        raise ValueError("equity series is empty")
    if not isinstance(equity.index, pd.DatetimeIndex):
        raise TypeError("equity must be indexed by a DatetimeIndex")
    if not equity.index.is_monotonic_increasing:
        raise ValueError("equity index must be sorted ascending")

    start_balance = float(initial_balance if initial_balance is not None else equity.iloc[0])
    if start_balance <= 0:
        raise ValueError(f"initial_balance must be > 0, got {start_balance}")

    target_equity = start_balance * (1.0 + rules.profit_target)
    by_day = equity.groupby(equity.index.normalize())

    day_start = start_balance
    peak = start_balance
    trading_days = 0
    days_elapsed = 0
    worst_daily_loss = 0.0
    worst_drawdown = 0.0
    first_day = equity.index[0].normalize()

    for day, values in by_day:
        days_elapsed = (day - first_day).days + 1
        day_low = float(values.min())
        day_close = float(values.iloc[-1])

        # A day counts as traded once equity moves at all.
        if day_low != day_start or day_close != day_start:
            trading_days += 1

        daily_loss = max(0.0, 1.0 - day_low / day_start) if day_start > 0 else 0.0
        worst_daily_loss = max(worst_daily_loss, daily_loss)

        reference = start_balance if rules.drawdown_type is DrawdownType.STATIC else max(peak, day_start)
        drawdown = max(0.0, 1.0 - day_low / reference) if reference > 0 else 0.0
        worst_drawdown = max(worst_drawdown, drawdown)

        # Breaches first: a target hit on a day that also breached does not save it.
        if daily_loss >= rules.max_daily_loss:
            return ChallengeResult(
                Outcome.FAILED_DAILY_LOSS, days_elapsed, trading_days, day_low, peak,
                day_low / start_balance - 1.0, worst_daily_loss, worst_drawdown, day,
                f"daily loss {daily_loss:.2%} reached limit {rules.max_daily_loss:.2%}",
            )
        if drawdown >= rules.max_drawdown:
            return ChallengeResult(
                Outcome.FAILED_DRAWDOWN, days_elapsed, trading_days, day_low, peak,
                day_low / start_balance - 1.0, worst_daily_loss, worst_drawdown, day,
                f"{rules.drawdown_type.value} drawdown {drawdown:.2%} reached limit "
                f"{rules.max_drawdown:.2%}",
            )

        peak = max(peak, float(values.max()))

        if day_close >= target_equity and trading_days >= rules.min_trading_days:
            return ChallengeResult(
                Outcome.PASSED, days_elapsed, trading_days, day_close, peak,
                day_close / start_balance - 1.0, worst_daily_loss, worst_drawdown, None,
                f"target {rules.profit_target:.2%} met on day {days_elapsed}",
            )

        if rules.max_days is not None and days_elapsed >= rules.max_days:
            return ChallengeResult(
                Outcome.FAILED_TIMEOUT, days_elapsed, trading_days, day_close, peak,
                day_close / start_balance - 1.0, worst_daily_loss, worst_drawdown, day,
                f"deadline of {rules.max_days} days reached without hitting target",
            )

        day_start = day_close

    final = float(equity.iloc[-1])
    return ChallengeResult(
        Outcome.INCOMPLETE, days_elapsed, trading_days, final, peak,
        final / start_balance - 1.0, worst_daily_loss, worst_drawdown, None,
        "ran out of data before passing or failing",
    )
