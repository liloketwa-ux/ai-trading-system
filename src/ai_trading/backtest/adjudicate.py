"""Adjudication against a :class:`FirmRuleset` (P0.4).

This is the rules-complete evaluator. :func:`~ai_trading.backtest.challenge.
evaluate_challenge` remains for the simple case; this one honours session
boundaries, equity basis, drawdown policy, day-counting, and deadline basis.

Order of adjudication within a session is deliberate and matches how firms
apply it: **breaches first, then the target**. An account that reaches its goal
and blows the daily limit in the same session has failed.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .challenge import ChallengeResult, Outcome
from .ruleset import DeadlineBasis, EquityBasis, FirmRuleset, session_days

__all__ = ["SessionRecord", "adjudicate"]


@dataclass(frozen=True)
class SessionRecord:
    """Per-session activity used for day counting."""

    trades: int = 0
    volume_fraction: float = 0.0


def adjudicate(
    equity: pd.Series,
    ruleset: FirmRuleset,
    *,
    initial_balance: float | None = None,
    sessions: dict[object, SessionRecord] | None = None,
) -> ChallengeResult:
    """Adjudicate an equity curve against a full firm rule set.

    Args:
        equity: Account equity indexed by a ``DatetimeIndex``. Under
            ``EquityBasis.INTRADAY`` the intra-session minimum is what breaches;
            under ``CLOSING_BALANCE`` only session closes are considered.
        ruleset: The firm's rules.
        initial_balance: Starting balance; defaults to the first equity value.
        sessions: Optional per-session activity keyed by session date, used to
            apply ``min_day_rule``. Without it, any session whose equity moved
            counts as a trading day.

    Returns:
        A :class:`ChallengeResult`.
    """
    equity = equity.dropna()
    if equity.empty:
        raise ValueError("equity series is empty")
    if not isinstance(equity.index, pd.DatetimeIndex):
        raise TypeError("equity must be indexed by a DatetimeIndex")
    if not equity.index.is_monotonic_increasing:
        raise ValueError("equity index must be sorted ascending")

    initial = float(initial_balance if initial_balance is not None else equity.iloc[0])
    if initial <= 0:
        raise ValueError(f"initial_balance must be > 0, got {initial}")

    day_key = session_days(equity.index, ruleset.timezone, ruleset.session_reset)
    target = initial * (1.0 + ruleset.profit_target)

    day_start = initial
    peak = initial
    trading_days = 0
    sessions_seen = 0
    worst_daily = 0.0
    worst_dd = 0.0
    first_day = None

    for day, values in equity.groupby(day_key.to_numpy()):
        if first_day is None:
            first_day = day
        sessions_seen += 1
        close = float(values.iloc[-1])

        # Intraday firms breach on the session low; closing-balance firms only
        # ever see the close, which is a materially more forgiving rule.
        low = float(values.min()) if ruleset.equity_basis is EquityBasis.INTRADAY else close
        high = float(values.max()) if ruleset.equity_basis is EquityBasis.INTRADAY else close

        record = (sessions or {}).get(day)
        if record is not None:
            counted = ruleset.min_day_rule.counts(record.trades, record.volume_fraction)
        else:
            counted = low != day_start or close != day_start
        if counted:
            trading_days += 1

        elapsed = _elapsed(first_day, day, sessions_seen, trading_days, ruleset)

        daily_loss = max(0.0, 1.0 - low / day_start) if day_start > 0 else 0.0
        worst_daily = max(worst_daily, daily_loss)

        reference = ruleset.drawdown_policy.reference(
            initial=initial, peak=peak, day_start=day_start
        )
        drawdown = max(0.0, 1.0 - low / reference) if reference > 0 else 0.0
        worst_dd = max(worst_dd, drawdown)

        if daily_loss >= ruleset.max_daily_loss:
            return _fail(
                Outcome.FAILED_DAILY_LOSS, elapsed, trading_days, low, peak, initial,
                worst_daily, worst_dd, day,
                f"daily loss {daily_loss:.2%} reached limit {ruleset.max_daily_loss:.2%}",
            )
        if drawdown >= ruleset.max_drawdown:
            return _fail(
                Outcome.FAILED_DRAWDOWN, elapsed, trading_days, low, peak, initial,
                worst_daily, worst_dd, day,
                f"{ruleset.drawdown_policy.describe()} drawdown {drawdown:.2%} reached "
                f"limit {ruleset.max_drawdown:.2%}",
            )

        peak = max(peak, high)

        if close >= target and trading_days >= ruleset.min_trading_days:
            return ChallengeResult(
                Outcome.PASSED, elapsed, trading_days, close, peak,
                close / initial - 1.0, worst_daily, worst_dd, None,
                f"target {ruleset.profit_target:.2%} met on {ruleset.deadline_basis.value} "
                f"day {elapsed}",
            )

        if ruleset.max_days is not None and elapsed >= ruleset.max_days:
            return _fail(
                Outcome.FAILED_TIMEOUT, elapsed, trading_days, close, peak, initial,
                worst_daily, worst_dd, day,
                f"deadline of {ruleset.max_days} {ruleset.deadline_basis.value} days reached",
            )

        day_start = close

    final = float(equity.iloc[-1])
    return ChallengeResult(
        Outcome.INCOMPLETE, _elapsed(first_day, day, sessions_seen, trading_days, ruleset),
        trading_days, final, peak, final / initial - 1.0, worst_daily, worst_dd, None,
        "ran out of data before passing or failing",
    )


def _elapsed(first_day, day, sessions_seen: int, trading_days: int, ruleset: FirmRuleset) -> int:
    """Days elapsed under the ruleset's deadline basis."""
    if ruleset.deadline_basis is DeadlineBasis.TRADING:
        return trading_days
    return (pd.Timestamp(day) - pd.Timestamp(first_day)).days + 1


def _fail(outcome, elapsed, trading_days, equity_at, peak, initial, worst_daily, worst_dd, day, detail):
    return ChallengeResult(
        outcome, elapsed, trading_days, equity_at, peak,
        equity_at / initial - 1.0, worst_daily, worst_dd, pd.Timestamp(day), detail,
    )
