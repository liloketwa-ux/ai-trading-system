"""Loss-limit tracker mechanics, independent of any firm's ruleset.

The firm tests pin Topstep's rule. These pin the machinery underneath it: the
other trailing modes, the enforcement clocks, and the boundary conditions that
decide an account. The two alternate modes exist because other firms use them,
and an untested branch in a loss-limit tracker is the kind of thing that is
discovered by an account failing.
"""

from datetime import date, datetime, timezone

import pytest

from ai_trading.propfirm import (
    AccountLimitMonitor,
    DailyLossLimitMode,
    DailyLossLimitTracker,
    LimitAction,
    LimitEventType,
    MaximumLossLimitTracker,
    MLLMode,
)

UTC = timezone.utc


def tracker(mode=MLLMode.EOD_TRAILING_INTRADAY_ENFORCED, balance=50_000,
            amount=2_000, locks=True):
    return MaximumLossLimitTracker(starting_balance=balance,
                                   trailing_amount=amount, mode=mode,
                                   locks_at_starting_balance=locks)


def at(hour=10):
    return datetime(2026, 8, 17, hour, tzinfo=UTC)


# =========================================================================
# Construction
# =========================================================================


def test_a_zero_trailing_amount_is_rejected():
    with pytest.raises(ValueError, match="trailing_amount"):
        tracker(amount=0)


def test_a_negative_starting_balance_is_rejected():
    with pytest.raises(ValueError, match="starting_balance"):
        tracker(balance=-1)


def test_mode_declares_its_two_clocks():
    eod = MLLMode.EOD_TRAILING_INTRADAY_ENFORCED
    assert eod.trails_on_eod_balance and eod.enforced_intraday

    intraday = MLLMode.INTRADAY_TRAILING
    assert not intraday.trails_on_eod_balance and intraday.enforced_intraday

    static = MLLMode.STATIC
    assert not static.trails_on_eod_balance and static.enforced_intraday


# =========================================================================
# STATIC
# =========================================================================


def test_a_static_limit_never_moves():
    static = tracker(mode=MLLMode.STATIC)
    static.end_of_day(date(2026, 8, 17), 58_000)
    assert static.limit_level == 48_000


def test_a_static_limit_is_still_enforced_intraday():
    static = tracker(mode=MLLMode.STATIC)
    assert static.mark(at(), 47_900) is not None


# =========================================================================
# INTRADAY_TRAILING -- the harsher variant
# =========================================================================


def test_intraday_trailing_moves_on_the_running_equity_high():
    harsh = tracker(mode=MLLMode.INTRADAY_TRAILING)
    harsh.mark(at(10), 51_200)
    assert harsh.limit_level == 49_200


def test_intraday_trailing_keeps_the_gain_after_a_giveback():
    """A spike that is handed back still tightens the account permanently.

    This is what makes intraday trailing materially harsher than Topstep's
    end-of-day rule, and why the two must not be modelled as one thing.
    """
    harsh = tracker(mode=MLLMode.INTRADAY_TRAILING)
    harsh.mark(at(10), 51_200)
    harsh.mark(at(11), 50_100)
    assert harsh.limit_level == 49_200

    gentle = tracker(mode=MLLMode.EOD_TRAILING_INTRADAY_ENFORCED)
    gentle.mark(at(10), 51_200)
    gentle.mark(at(11), 50_100)
    assert gentle.limit_level == 48_000


def test_intraday_trailing_can_kill_an_account_the_eod_rule_would_not():
    equity_path = [51_500, 49_400]
    harsh, gentle = (tracker(mode=MLLMode.INTRADAY_TRAILING),
                     tracker(mode=MLLMode.EOD_TRAILING_INTRADAY_ENFORCED))
    for hour, equity in enumerate(equity_path, start=10):
        harsh.mark(at(hour), equity)
        gentle.mark(at(hour), equity)
    assert harsh.breached          # limit rose to 49,500
    assert not gentle.breached     # limit still 48,000


# =========================================================================
# EOD_TRAILING_EOD_ENFORCED
# =========================================================================


def test_eod_enforcement_ignores_an_intraday_excursion():
    """Below the limit at noon, above it at the close: the account survives."""
    eod = tracker(mode=MLLMode.EOD_TRAILING_EOD_ENFORCED)
    assert eod.mark(at(12), 47_000) is None
    assert not eod.breached

    eod.end_of_day(date(2026, 8, 17), 49_000)
    assert not eod.breached


def test_eod_enforcement_fails_an_account_that_closes_below_the_limit():
    eod = tracker(mode=MLLMode.EOD_TRAILING_EOD_ENFORCED)
    events = eod.end_of_day(date(2026, 8, 17), 47_500)
    assert eod.breached
    assert events[0].action is LimitAction.LIQUIDATE_AND_FAIL


def test_a_day_that_closes_below_the_limit_does_not_also_trail():
    """Enforcing before trailing keeps a dead account from moving its own floor."""
    eod = tracker(mode=MLLMode.EOD_TRAILING_EOD_ENFORCED)
    eod.end_of_day(date(2026, 8, 17), 47_500)
    assert eod.limit_level == 48_000


# =========================================================================
# Trailing arithmetic
# =========================================================================


def test_the_limit_advances_by_exactly_the_balance_gain():
    eod = tracker()
    eod.end_of_day(date(2026, 8, 17), 50_437.50)
    assert eod.limit_level == pytest.approx(48_437.50)


def test_an_unprofitable_day_leaves_the_limit_alone():
    eod = tracker()
    assert eod.end_of_day(date(2026, 8, 17), 49_100) == []
    assert eod.limit_level == 48_000


def test_advancing_emits_an_event_with_both_levels():
    eod = tracker()
    events = eod.end_of_day(date(2026, 8, 17), 50_500)
    assert events[0].event_type is LimitEventType.MLL_ADVANCE
    assert "48,000.00 -> 48,500.00" in events[0].detail


def test_a_non_locking_limit_trails_past_the_starting_balance():
    """Not every firm freezes the threshold, so the behaviour is configurable."""
    unlocked = tracker(locks=False)
    unlocked.end_of_day(date(2026, 8, 17), 55_000)
    assert unlocked.limit_level == 53_000
    assert not unlocked.locked
    assert unlocked.lock_level is None


def test_the_high_water_balance_is_tracked_separately_from_the_limit():
    eod = tracker()
    eod.end_of_day(date(2026, 8, 17), 53_000)      # limit locks at 50,000
    eod.end_of_day(date(2026, 8, 18), 57_000)
    assert eod.limit_level == 50_000
    assert eod.high_water_balance == 53_000        # frozen with the limit


def test_would_breach_answers_without_mutating():
    eod = tracker()
    assert eod.would_breach(48_000)
    assert not eod.would_breach(48_000.01)
    assert not eod.breached


# =========================================================================
# Daily loss limit
# =========================================================================


def test_an_inactive_limit_never_fires():
    dll = DailyLossLimitTracker(amount=None, mode=DailyLossLimitMode.NONE)
    dll.start_session(date(2026, 8, 17), 50_000)
    assert dll.mark(at(), 10) is None
    assert dll.floor is None


def test_an_active_limit_needs_an_amount():
    with pytest.raises(ValueError, match="requires a positive amount"):
        DailyLossLimitTracker(amount=None, mode=DailyLossLimitMode.PURCHASE_SET)


def test_marking_before_the_session_opens_is_an_error():
    """The floor is measured from the opening balance; there isn't one yet."""
    dll = DailyLossLimitTracker(1_000, DailyLossLimitMode.PURCHASE_SET)
    with pytest.raises(RuntimeError, match="before start_session"):
        dll.mark(at(), 49_000)


def test_the_limit_fires_once_per_session():
    dll = DailyLossLimitTracker(1_000, DailyLossLimitMode.PURCHASE_SET)
    dll.start_session(date(2026, 8, 17), 50_000)
    assert dll.mark(at(10), 48_900) is not None
    assert dll.mark(at(11), 48_000) is None      # already locked out


def test_each_session_gets_a_fresh_floor_and_a_fresh_lockout():
    dll = DailyLossLimitTracker(1_000, DailyLossLimitMode.PURCHASE_SET)
    dll.start_session(date(2026, 8, 17), 50_000)
    dll.mark(at(10), 48_900)
    dll.start_session(date(2026, 8, 18), 48_900)

    assert not dll.locked_out
    assert dll.floor == 47_900
    assert dll.days_hit == [date(2026, 8, 17)]


def test_the_lockout_action_is_never_a_failure():
    dll = DailyLossLimitTracker(1_000, DailyLossLimitMode.PURCHASE_SET)
    dll.start_session(date(2026, 8, 17), 50_000)
    event = dll.mark(at(), 48_500)
    assert event.action is LimitAction.FLATTEN_AND_LOCK_SESSION
    assert "not an eligibility failure" in event.detail


# =========================================================================
# Combined monitor
# =========================================================================


def monitor(dll_amount=1_000):
    return AccountLimitMonitor(
        mll=tracker(),
        dll=DailyLossLimitTracker(dll_amount, DailyLossLimitMode.PURCHASE_SET),
    )


def test_a_locked_out_session_still_permits_the_evaluation_to_continue():
    combined = monitor()
    combined.start_session(date(2026, 8, 17), 50_000)
    combined.mark(at(10), 48_900)
    assert not combined.can_trade
    assert not combined.failed


def test_a_dead_account_reports_a_single_terminal_event():
    combined = monitor()
    combined.start_session(date(2026, 8, 17), 50_000)
    events = combined.mark(at(10), 47_000)
    assert len(events) == 1
    assert events[0].event_type is LimitEventType.MLL_BREACH


def test_a_dead_account_stops_producing_events():
    combined = monitor()
    combined.start_session(date(2026, 8, 17), 50_000)
    combined.mark(at(10), 47_000)
    assert combined.mark(at(11), 46_000) == []


def test_the_monitor_serializes_both_limits():
    combined = monitor()
    combined.start_session(date(2026, 8, 17), 50_000)
    combined.mark(at(10), 49_500)
    payload = combined.to_dict()
    assert payload["mll"]["limit_level"] == 48_000
    assert payload["dll"]["floor"] == 49_000
    assert payload["can_trade"]
