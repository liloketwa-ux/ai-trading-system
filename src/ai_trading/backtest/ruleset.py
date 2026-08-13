"""Firm rule sets: swappable evaluation rules with no logic in the config (P0.4).

Every dimension a prop firm can vary lives here as data, so a new firm is a new
:class:`FirmRuleset` value rather than an edit to the adjudicator.

Four things firms genuinely differ on, each of which changes pass rates
materially:

* **Drawdown policy** — static from the initial balance, trailing behind peak
  equity, or trailing that *locks* once the account clears a threshold (the
  common futures-firm rule). These are separate classes, not a flag, because
  they are different rules and sharing a code path is how they get conflated.
* **Session boundary** — a "day" is the firm's trading session, not UTC
  midnight. Futures firms reset at 17:00 America/Chicago; using naive UTC dates
  puts the loss limit in the middle of the session and silently mis-adjudicates
  every overnight move.
* **Equity basis** — intraday equity including open positions (what most firms
  breach on) versus closed balance only (much more forgiving).
* **Counting rules** — what makes a day count toward the minimum, and whether
  the deadline runs in calendar or trading days.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import time
from enum import Enum
from zoneinfo import ZoneInfo

import pandas as pd

__all__ = [
    "EquityBasis",
    "DeadlineBasis",
    "MinDayRule",
    "DrawdownPolicy",
    "StaticDrawdown",
    "TrailingDrawdown",
    "LockingTrailingDrawdown",
    "FirmRuleset",
    "session_days",
    "FTMO_LIKE",
    "TOPSTEP_LIKE",
    "APEX_LIKE",
]


class EquityBasis(str, Enum):
    """What the firm measures for breaches."""

    INTRADAY = "intraday"  # marked equity, open positions included
    CLOSING_BALANCE = "closing_balance"  # realized balance at session close only


class DeadlineBasis(str, Enum):
    CALENDAR = "calendar"
    TRADING = "trading"


@dataclass(frozen=True)
class MinDayRule:
    """What makes a session count toward the minimum-trading-days requirement.

    Attributes:
        min_trades: Trades required in a session for it to count.
        min_volume_fraction: Traded notional, as a fraction of equity, required
            in a session for it to count. Firms use this to stop applicants
            satisfying the minimum with token one-lot trades.
    """

    min_trades: int = 1
    min_volume_fraction: float = 0.0

    def counts(self, trades: int, volume_fraction: float) -> bool:
        return trades >= self.min_trades and volume_fraction >= self.min_volume_fraction


# -- drawdown policies -----------------------------------------------------


class DrawdownPolicy(ABC):
    """Determines the reference equity a drawdown is measured from."""

    name: str = "base"

    @abstractmethod
    def reference(self, *, initial: float, peak: float, day_start: float) -> float:
        """Equity level the drawdown limit is measured down from."""

    def describe(self) -> str:
        return self.name


@dataclass(frozen=True)
class StaticDrawdown(DrawdownPolicy):
    """Measured from the initial balance. Profit builds a permanent cushion."""

    name: str = "static"

    def reference(self, *, initial: float, peak: float, day_start: float) -> float:
        return initial


@dataclass(frozen=True)
class TrailingDrawdown(DrawdownPolicy):
    """Measured from peak equity. Ratchets up and never gives ground back.

    Structurally harsher than static: grinding equity upward tightens the leash,
    so a long sequence of small gains offers no protection at all.
    """

    name: str = "trailing"

    def reference(self, *, initial: float, peak: float, day_start: float) -> float:
        return max(peak, initial)


@dataclass(frozen=True)
class LockingTrailingDrawdown(DrawdownPolicy):
    """Trails behind peak until the account clears a threshold, then locks.

    The common futures-firm rule: the limit follows the peak while the account
    is proving itself, and once equity has cleared ``lock_at`` (as a fraction of
    the initial balance) the reference freezes at that level, so the trader can
    no longer be stopped out by giving back profit above it.
    """

    lock_at: float = 1.06
    name: str = "locking_trailing"

    def __post_init__(self) -> None:
        if self.lock_at <= 1.0:
            raise ValueError(f"lock_at must be > 1.0, got {self.lock_at}")

    def reference(self, *, initial: float, peak: float, day_start: float) -> float:
        lock_level = initial * self.lock_at
        return lock_level if peak >= lock_level else max(peak, initial)

    def describe(self) -> str:
        return f"{self.name} (locks at {self.lock_at:.2%} of initial)"


# -- the ruleset -----------------------------------------------------------


@dataclass(frozen=True)
class FirmRuleset:
    """A complete, swappable evaluation rule set.

    Attributes:
        name: Human label for reports.
        profit_target: Gain over the initial balance required to pass.
        max_daily_loss: Loss from start-of-session equity that fails the account.
        max_drawdown: Loss from the policy's reference that fails the account.
        drawdown_policy: How the drawdown reference is determined.
        equity_basis: Whether breaches are judged on intraday or closing equity.
        session_reset: Local time at which a new trading session begins.
        timezone: IANA zone the reset time is expressed in.
        min_trading_days: Sessions that must count before a pass is allowed.
        min_day_rule: What makes a session count.
        max_days: Deadline, or ``None`` for unlimited.
        deadline_basis: Whether the deadline runs in calendar or trading days.
        allows_automated_trading: Whether the firm permits algorithmic trading.
            ``None`` means unverified — a rules breach voids a pass regardless
            of the equity curve, so this must be confirmed with the firm.
    """

    name: str = "generic"
    profit_target: float = 0.10
    max_daily_loss: float = 0.05
    max_drawdown: float = 0.10
    drawdown_policy: DrawdownPolicy = field(default_factory=StaticDrawdown)
    equity_basis: EquityBasis = EquityBasis.INTRADAY
    session_reset: time = time(0, 0)
    timezone: str = "UTC"
    min_trading_days: int = 4
    min_day_rule: MinDayRule = field(default_factory=MinDayRule)
    max_days: int | None = 30
    deadline_basis: DeadlineBasis = DeadlineBasis.CALENDAR
    allows_automated_trading: bool | None = None

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
        try:
            ZoneInfo(self.timezone)
        except Exception as exc:  # noqa: BLE001 - re-raised with context, never swallowed
            raise ValueError(f"unknown timezone {self.timezone!r}") from exc

    def describe(self) -> str:
        deadline = f"{self.max_days} {self.deadline_basis.value} days" if self.max_days else "no deadline"
        return (
            f"{self.name}: +{self.profit_target:.0%} target, -{self.max_daily_loss:.0%} daily, "
            f"-{self.max_drawdown:.0%} {self.drawdown_policy.describe()}, "
            f"{self.min_trading_days} min days, {deadline}, "
            f"session resets {self.session_reset:%H:%M} {self.timezone}, "
            f"{self.equity_basis.value} equity"
        )


def session_days(index: pd.DatetimeIndex, timezone: str, reset: time) -> pd.Series:
    """Map timestamps to the firm's trading-session date.

    A bar at or after the reset time belongs to the *next* session, which is why
    a futures session opening 17:00 Sunday CT is Monday's trading day. Naive UTC
    midnight grouping gets this wrong for every instrument that trades overnight.

    Args:
        index: Timestamps. Naive input is assumed to be UTC.
        timezone: IANA zone the reset time is expressed in.
        reset: Local session start time.

    Returns:
        A series of session dates aligned to ``index``.
    """
    if not isinstance(index, pd.DatetimeIndex):
        raise TypeError("index must be a DatetimeIndex")

    localized = index.tz_localize("UTC") if index.tz is None else index
    local = localized.tz_convert(ZoneInfo(timezone))

    dates = pd.Series(local.date, index=index)
    # A bar at or past the reset belongs to the session that just opened.
    rolls_forward = pd.Series(
        [t >= reset for t in local.time], index=index
    ) & pd.Series([reset != time(0, 0)] * len(index), index=index)
    return pd.Series(
        [d + pd.Timedelta(days=1) if roll else d for d, roll in zip(dates, rolls_forward)],
        index=index,
    )


# -- presets ---------------------------------------------------------------

#: Two-step FX/CFD evaluation shape. Static drawdown, UTC-ish daily reset.
FTMO_LIKE = FirmRuleset(
    name="ftmo_like",
    profit_target=0.10,
    max_daily_loss=0.05,
    max_drawdown=0.10,
    drawdown_policy=StaticDrawdown(),
    session_reset=time(0, 0),
    timezone="Europe/Prague",
    min_trading_days=4,
    max_days=30,
)

#: Futures-firm shape: trailing drawdown that locks, CME session reset.
TOPSTEP_LIKE = FirmRuleset(
    name="topstep_like",
    profit_target=0.06,
    max_daily_loss=0.02,
    max_drawdown=0.04,
    drawdown_policy=LockingTrailingDrawdown(lock_at=1.06),
    session_reset=time(17, 0),
    timezone="America/Chicago",
    min_trading_days=2,
    max_days=None,
)

#: Futures-firm shape with pure trailing drawdown and no lock.
APEX_LIKE = FirmRuleset(
    name="apex_like",
    profit_target=0.06,
    max_daily_loss=1.0,  # no daily loss limit on some Apex accounts
    max_drawdown=0.03,
    drawdown_policy=TrailingDrawdown(),
    session_reset=time(17, 0),
    timezone="America/Chicago",
    min_trading_days=7,
    max_days=None,
)
