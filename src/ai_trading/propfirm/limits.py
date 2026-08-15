"""Loss-limit trackers: Maximum Loss Limit and Daily Loss Limit.

Topstep's Maximum Loss Limit is routinely described as "a trailing drawdown",
which is true and useless. Two questions decide whether an account survives, and
both are answered differently by different firms:

1. **What does the limit trail?** Topstep's MLL trails *end-of-day balance*. An
   intraday equity spike to +$1,200 that closes the day at +$300 moves the limit
   by $300, not $1,200. A tracker that trails the intraday high is materially
   harsher than the real rule and will fail accounts that would have survived.
2. **When is the limit enforced?** Continuously, on *equity* -- open unrealised
   loss counts. So the limit trails slowly (end of day) but bites immediately
   (any tick). Those are two different clocks on the same number, and modelling
   either one alone gets the rule wrong in a specific, predictable direction.

Hence the mode name :attr:`MLLMode.EOD_TRAILING_INTRADAY_ENFORCED`: *end-of-day
trailing threshold with intraday enforcement*. It is not a hybrid, it is the
rule.

The Daily Loss Limit is a different kind of object entirely and is modelled
separately for that reason. Where the MLL is an eligibility rule -- breach it and
the account is gone -- the DLL is a *risk control*: it flattens positions and
locks out the rest of the session, and the evaluation continues the next day.
Collapsing the two into one "loss limit" produces a simulator that fails accounts
the firm would merely have paused. :class:`LimitAction` keeps them distinct.

Nothing here submits orders. The trackers report what the firm's system would
do; acting on it is the caller's problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

__all__ = [
    "MLLMode", "LimitAction", "LimitEventType", "LimitEvent",
    "MaximumLossLimitTracker", "DailyLossLimitMode", "DailyLossLimitTracker",
    "AccountLimitMonitor", "EligibilityOutcome",
]


class MLLMode(str, Enum):
    """How a Maximum Loss Limit trails and when it is enforced."""

    #: Topstep. Threshold advances on end-of-day balance; enforced on every tick
    #: of equity, so unrealised loss can breach it.
    EOD_TRAILING_INTRADAY_ENFORCED = "eod_trailing_intraday_enforced"
    #: Threshold advances on end-of-day balance and is only tested at the close.
    #: Intraday excursions below the threshold do not fail the account.
    EOD_TRAILING_EOD_ENFORCED = "eod_trailing_eod_enforced"
    #: Threshold advances on the running intraday equity high. Harsher: a spike
    #: that gives back moves the limit up and leaves it there.
    INTRADAY_TRAILING = "intraday_trailing"
    #: Fixed floor that never moves.
    STATIC = "static"

    @property
    def trails_on_eod_balance(self) -> bool:
        return self in (MLLMode.EOD_TRAILING_INTRADAY_ENFORCED,
                        MLLMode.EOD_TRAILING_EOD_ENFORCED)

    @property
    def enforced_intraday(self) -> bool:
        return self in (MLLMode.EOD_TRAILING_INTRADAY_ENFORCED,
                        MLLMode.INTRADAY_TRAILING, MLLMode.STATIC)


class LimitAction(str, Enum):
    """What the firm's platform does when a limit is touched."""

    NONE = "none"
    #: Positions closed, no further trading until the next session. The
    #: evaluation is *not* over.
    FLATTEN_AND_LOCK_SESSION = "flatten_and_lock_session"
    #: Positions closed and the account is finished.
    LIQUIDATE_AND_FAIL = "liquidate_and_fail"


class LimitEventType(str, Enum):
    MLL_BREACH = "mll_breach"
    MLL_ADVANCE = "mll_advance"
    MLL_LOCKED = "mll_locked"
    DLL_HIT = "dll_hit"


class EligibilityOutcome(str, Enum):
    """Why an account is or is not eligible for the next stage.

    ``CONSISTENCY_NOT_MET`` is deliberately not a rule violation. Failing the
    consistency guideline does not end an evaluation -- it raises the profit
    target until the distribution of profit is acceptable. Reporting it as a
    violation would tell a trader their account is dead when it is merely
    slower, which is the kind of wrong answer that changes behaviour.
    """

    IN_PROGRESS = "in_progress"
    ELIGIBLE = "eligible"
    CONSISTENCY_NOT_MET = "consistency_not_met"
    RULE_VIOLATION = "rule_violation"
    UNDETERMINED = "undetermined"

    @property
    def is_failure(self) -> bool:
        return self is EligibilityOutcome.RULE_VIOLATION


@dataclass(frozen=True)
class LimitEvent:
    """Something a limit did, with the numbers that caused it."""

    event_type: LimitEventType
    at: datetime | date
    action: LimitAction
    limit_level: float
    observed: float
    detail: str = ""
    #: True when the observation only crossed the limit because of open
    #: unrealised P&L -- realised balance alone was still above it.
    caused_by_unrealized: bool = False

    def to_dict(self) -> dict:
        at = self.at
        return {
            "event_type": self.event_type.value,
            "at": at.isoformat(),
            "action": self.action.value,
            "limit_level": self.limit_level,
            "observed": self.observed,
            "detail": self.detail,
            "caused_by_unrealized": self.caused_by_unrealized,
        }


@dataclass
class MaximumLossLimitTracker:
    """Topstep-style Maximum Loss Limit.

    The invariants, all of which are asserted by the tests:

    * The limit starts at ``starting_balance - trailing_amount``.
    * It moves **up** only, and only on the trailing trigger for its mode.
    * It never exceeds ``starting_balance`` when ``locks_at_starting_balance``
      is set, and once it gets there it is frozen -- further profit no longer
      tightens the account.
    * Enforcement compares **equity**, not balance. An open loser can breach a
      limit that closed balance is comfortably above.
    * A breach is terminal. Subsequent marks do not un-breach it.
    """

    starting_balance: float
    trailing_amount: float
    mode: MLLMode = MLLMode.EOD_TRAILING_INTRADAY_ENFORCED
    locks_at_starting_balance: bool = True

    limit_level: float = field(init=False)
    locked: bool = field(init=False, default=False)
    breached: bool = field(init=False, default=False)
    breach_event: LimitEvent | None = field(init=False, default=None)
    high_water_balance: float = field(init=False)
    events: list[LimitEvent] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        if self.trailing_amount <= 0:
            raise ValueError("trailing_amount must be positive")
        if self.starting_balance <= 0:
            raise ValueError("starting_balance must be positive")
        self.limit_level = self.starting_balance - self.trailing_amount
        self.high_water_balance = self.starting_balance
        if self.locks_at_starting_balance and self.trailing_amount <= 0:
            self.locked = True

    # -- queries ---------------------------------------------------------
    @property
    def lock_level(self) -> float | None:
        """The level at which the limit stops trailing, if it does."""
        return self.starting_balance if self.locks_at_starting_balance else None

    def headroom(self, equity: float) -> float:
        """Distance from the limit. Negative means already through it."""
        return equity - self.limit_level

    def would_breach(self, equity: float) -> bool:
        return equity <= self.limit_level

    # -- intraday --------------------------------------------------------
    def mark(self, at: datetime, equity: float,
             realized_balance: float | None = None) -> LimitEvent | None:
        """Observe equity intraday.

        ``equity`` includes open unrealised P&L; ``realized_balance`` is closed
        balance only and is used solely to label *why* a breach happened.
        """
        if self.breached:
            return None

        if self.mode is MLLMode.INTRADAY_TRAILING:
            self._advance(equity, at, "intraday equity high")

        if not self.mode.enforced_intraday:
            return None
        return self._test(at, equity, realized_balance)

    # -- end of day ------------------------------------------------------
    def end_of_day(self, on: date, closing_balance: float,
                   closing_equity: float | None = None) -> list[LimitEvent]:
        """Close the trading day: enforce if required, then trail.

        Order matters. A day that closes below the limit has already failed, and
        trailing first would move the limit down-and-up around a breach that
        should have ended the account.
        """
        produced: list[LimitEvent] = []
        if self.breached:
            return produced

        if not self.mode.enforced_intraday:
            equity = closing_equity if closing_equity is not None else closing_balance
            event = self._test(on, equity, closing_balance)
            if event is not None:
                return [event]

        if self.mode.trails_on_eod_balance:
            event = self._advance(closing_balance, on, "end-of-day balance")
            if event is not None:
                produced.append(event)
                if self.locked and produced[-1].event_type is not LimitEventType.MLL_LOCKED:
                    produced.append(self._record(LimitEventType.MLL_LOCKED, on,
                                                 LimitAction.NONE, closing_balance,
                                                 "limit reached starting balance and froze"))
        return produced

    # -- internals -------------------------------------------------------
    def _advance(self, reference: float, at: datetime | date,
                 basis: str) -> LimitEvent | None:
        if self.locked:
            return None
        if reference > self.high_water_balance:
            self.high_water_balance = reference

        candidate = reference - self.trailing_amount
        cap = self.lock_level
        if cap is not None and candidate >= cap:
            candidate = cap
            newly_locked = True
        else:
            newly_locked = False

        if candidate <= self.limit_level:
            return None

        previous = self.limit_level
        self.limit_level = candidate
        if newly_locked:
            self.locked = True
            return self._record(
                LimitEventType.MLL_LOCKED, at, LimitAction.NONE, reference,
                f"limit advanced {previous:,.2f} -> {candidate:,.2f} on {basis} "
                "and locked at the starting balance",
            )
        return self._record(
            LimitEventType.MLL_ADVANCE, at, LimitAction.NONE, reference,
            f"limit advanced {previous:,.2f} -> {candidate:,.2f} on {basis}",
        )

    def _test(self, at: datetime | date, equity: float,
              realized_balance: float | None) -> LimitEvent | None:
        if equity > self.limit_level:
            return None
        unrealized_caused = (
            realized_balance is not None and realized_balance > self.limit_level
        )
        self.breached = True
        event = self._record(
            LimitEventType.MLL_BREACH, at, LimitAction.LIQUIDATE_AND_FAIL, equity,
            (f"equity {equity:,.2f} at or below Maximum Loss Limit "
             f"{self.limit_level:,.2f}"
             + (" -- caused by open unrealised loss; closed balance was still above"
                if unrealized_caused else "")),
            caused_by_unrealized=unrealized_caused,
        )
        self.breach_event = event
        return event

    def _record(self, event_type: LimitEventType, at: datetime | date,
                action: LimitAction, observed: float, detail: str,
                caused_by_unrealized: bool = False) -> LimitEvent:
        event = LimitEvent(event_type, at, action, self.limit_level, observed,
                           detail, caused_by_unrealized)
        self.events.append(event)
        return event

    def to_dict(self) -> dict:
        return {
            "starting_balance": self.starting_balance,
            "trailing_amount": self.trailing_amount,
            "mode": self.mode.value,
            "limit_level": self.limit_level,
            "locked": self.locked,
            "lock_level": self.lock_level,
            "breached": self.breached,
            "high_water_balance": self.high_water_balance,
            "events": [e.to_dict() for e in self.events],
        }


class DailyLossLimitMode(str, Enum):
    """Whether a Daily Loss Limit exists on this account, and who set it.

    The distinction is not cosmetic. A DLL the trader configured themselves can
    be changed or removed, so a simulation that assumes it is present overstates
    protection; a DLL bundled with the account cannot, so a simulation that
    ignores it overstates the achievable drawdown per day.
    """

    NONE = "none"
    #: Purchased as part of the account; fixed by the firm.
    PURCHASE_SET = "purchase_set"
    #: Set by the trader in the platform; may be absent or altered.
    PERSONAL_MANUAL = "personal_manual"

    @property
    def is_active(self) -> bool:
        return self is not DailyLossLimitMode.NONE


@dataclass
class DailyLossLimitTracker:
    """Session risk control, not an eligibility rule.

    Hitting it flattens the book and locks out the rest of the session. The
    account is still alive and the evaluation continues tomorrow -- which is why
    the action is :attr:`LimitAction.FLATTEN_AND_LOCK_SESSION` and never
    ``LIQUIDATE_AND_FAIL``.
    """

    amount: float | None
    mode: DailyLossLimitMode = DailyLossLimitMode.NONE

    session_start_balance: float | None = field(init=False, default=None)
    locked_out: bool = field(init=False, default=False)
    current_day: date | None = field(init=False, default=None)
    events: list[LimitEvent] = field(init=False, default_factory=list)
    days_hit: list[date] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        if self.mode.is_active and (self.amount is None or self.amount <= 0):
            raise ValueError(
                f"daily loss limit mode {self.mode.value} requires a positive amount"
            )
        if not self.mode.is_active and self.amount is not None:
            raise ValueError(
                "a DailyLossLimitMode.NONE tracker cannot carry an amount -- "
                "an inactive limit with a number attached is the shape of a bug"
            )

    @property
    def active(self) -> bool:
        return self.mode.is_active

    def start_session(self, on: date, opening_balance: float) -> None:
        self.current_day = on
        self.session_start_balance = opening_balance
        self.locked_out = False

    @property
    def floor(self) -> float | None:
        if not self.active or self.session_start_balance is None:
            return None
        return self.session_start_balance - float(self.amount)

    def mark(self, at: datetime, equity: float) -> LimitEvent | None:
        if not self.active or self.locked_out:
            return None
        floor = self.floor
        if floor is None:
            raise RuntimeError(
                "DailyLossLimitTracker.mark called before start_session -- the "
                "limit is measured from the session's opening balance, so there "
                "is no floor to compare against yet"
            )
        if equity > floor:
            return None
        self.locked_out = True
        if self.current_day is not None:
            self.days_hit.append(self.current_day)
        event = LimitEvent(
            LimitEventType.DLL_HIT, at, LimitAction.FLATTEN_AND_LOCK_SESSION,
            floor, equity,
            (f"equity {equity:,.2f} at or below daily loss limit {floor:,.2f} "
             f"({self.mode.value}); positions flattened and the session is locked. "
             "This is not an eligibility failure."),
        )
        self.events.append(event)
        return event

    def to_dict(self) -> dict:
        return {
            "mode": self.mode.value,
            "amount": self.amount,
            "active": self.active,
            "floor": self.floor,
            "locked_out": self.locked_out,
            "days_hit": [d.isoformat() for d in self.days_hit],
            "events": [e.to_dict() for e in self.events],
        }


@dataclass
class AccountLimitMonitor:
    """Runs both limits against the same equity stream.

    Precedence is fixed and not configurable: the MLL is tested first, because
    an account that has breached it is finished regardless of whether the daily
    limit would also have fired. Reporting a session lock-out on a dead account
    would be a friendlier answer than the truth.
    """

    mll: MaximumLossLimitTracker
    dll: DailyLossLimitTracker

    events: list[LimitEvent] = field(init=False, default_factory=list)

    @property
    def failed(self) -> bool:
        return self.mll.breached

    @property
    def can_trade(self) -> bool:
        return not self.mll.breached and not self.dll.locked_out

    def start_session(self, on: date, opening_balance: float) -> None:
        self.dll.start_session(on, opening_balance)

    def mark(self, at: datetime, equity: float,
             realized_balance: float | None = None) -> list[LimitEvent]:
        produced: list[LimitEvent] = []
        breach = self.mll.mark(at, equity, realized_balance)
        if breach is not None:
            produced.append(breach)
            self.events.extend(produced)
            return produced
        if self.mll.breached:
            return produced
        hit = self.dll.mark(at, equity)
        if hit is not None:
            produced.append(hit)
        self.events.extend(produced)
        return produced

    def end_of_day(self, on: date, closing_balance: float,
                   closing_equity: float | None = None) -> list[LimitEvent]:
        produced = self.mll.end_of_day(on, closing_balance, closing_equity)
        self.events.extend(produced)
        return produced

    def to_dict(self) -> dict:
        return {
            "failed": self.failed,
            "can_trade": self.can_trade,
            "mll": self.mll.to_dict(),
            "dll": self.dll.to_dict(),
        }
