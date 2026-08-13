"""Tests for swappable firm rulesets and the rules-complete adjudicator (P0.4)."""

from datetime import time

import pandas as pd
import pytest

from ai_trading.backtest import Outcome
from ai_trading.backtest.adjudicate import SessionRecord, adjudicate
from ai_trading.backtest.ruleset import (
    APEX_LIKE,
    FTMO_LIKE,
    TOPSTEP_LIKE,
    DeadlineBasis,
    EquityBasis,
    FirmRuleset,
    LockingTrailingDrawdown,
    MinDayRule,
    StaticDrawdown,
    TrailingDrawdown,
    session_days,
)

LOOSE = dict(max_daily_loss=1.0, min_trading_days=0, max_days=None)


def hourly(values, start="2024-01-01 00:00Z"):
    return pd.Series(
        [float(v) for v in values],
        index=pd.date_range(start, periods=len(values), freq="h"),
    )


def daily(values, start="2024-01-01"):
    return pd.Series(
        [float(v) for v in values],
        index=pd.date_range(start, periods=len(values), freq="D", tz="UTC"),
    )


# -- session boundaries ----------------------------------------------------


def test_cme_reset_rolls_evening_bars_into_the_next_session():
    """17:00 CT opens the NEXT trading day -- naive UTC midnight gets this wrong."""
    index = pd.to_datetime(
        ["2024-01-02 21:00Z", "2024-01-02 23:00Z", "2024-01-03 02:00Z", "2024-01-03 14:00Z"]
    )
    days = session_days(index, "America/Chicago", time(17, 0))

    assert str(days.iloc[0]) == "2024-01-02"  # 15:00 CT Tue -> Tue
    assert str(days.iloc[1]) == "2024-01-03"  # 17:00 CT Tue -> Wed
    assert str(days.iloc[2]) == "2024-01-03"  # 20:00 CT Tue -> Wed
    assert str(days.iloc[3]) == "2024-01-03"  # 08:00 CT Wed -> Wed


def test_midnight_reset_matches_plain_date_grouping():
    index = pd.to_datetime(["2024-01-02 01:00Z", "2024-01-02 23:00Z"])
    days = session_days(index, "UTC", time(0, 0))
    assert str(days.iloc[0]) == str(days.iloc[1]) == "2024-01-02"


def test_session_days_localizes_naive_timestamps_as_utc():
    naive = pd.date_range("2024-01-02", periods=2, freq="h")
    assert len(session_days(naive, "UTC", time(0, 0))) == 2


def test_session_days_rejects_non_datetime_index():
    with pytest.raises(TypeError, match="DatetimeIndex"):
        session_days(pd.Index([1, 2]), "UTC", time(0, 0))


def test_session_boundary_changes_the_verdict():
    """The same curve passes or fails depending on where the session breaks."""
    # All three bars fall inside UTC 2 Jan, but 23:30Z is 17:30 CST -- past the
    # CME reset, so it opens the 3 Jan session. Lumped together the drop is 6%
    # and breaches; split at the reset it is 3% then 3.1% and survives.
    equity = pd.Series(
        [100_000.0, 97_000.0, 94_000.0],
        index=pd.to_datetime(["2024-01-02 22:00Z", "2024-01-02 22:30Z", "2024-01-02 23:30Z"]),
    )
    cme = FirmRuleset(
        name="cme", max_daily_loss=0.05, max_drawdown=0.50, min_trading_days=0,
        max_days=None, session_reset=time(17, 0), timezone="America/Chicago",
    )
    utc = FirmRuleset(
        name="utc", max_daily_loss=0.05, max_drawdown=0.50, min_trading_days=0,
        max_days=None, session_reset=time(0, 0), timezone="UTC",
    )
    assert adjudicate(equity, utc).outcome is Outcome.FAILED_DAILY_LOSS
    assert adjudicate(equity, cme).outcome is not Outcome.FAILED_DAILY_LOSS


# -- drawdown policies (separate code paths) -------------------------------


def test_the_three_policies_give_three_different_references():
    args = dict(initial=100_000.0, peak=112_000.0, day_start=110_000.0)
    assert StaticDrawdown().reference(**args) == 100_000.0
    assert TrailingDrawdown().reference(**args) == 112_000.0
    assert LockingTrailingDrawdown(lock_at=1.06).reference(**args) == 106_000.0


def test_trailing_fails_where_static_survives():
    """Peak 108k then down to 96k: -4% from initial but -11% from peak."""
    equity = daily([100_000, 108_000, 96_000])
    static = FirmRuleset(name="s", max_drawdown=0.10, drawdown_policy=StaticDrawdown(), **LOOSE)
    trailing = FirmRuleset(name="t", max_drawdown=0.10, drawdown_policy=TrailingDrawdown(), **LOOSE)

    assert adjudicate(equity, static).outcome is not Outcome.FAILED_DRAWDOWN
    assert adjudicate(equity, trailing).outcome is Outcome.FAILED_DRAWDOWN


def test_locking_trailing_stops_ratcheting_once_locked():
    """Above the lock level the reference freezes, so giving back profit is safe."""
    policy = LockingTrailingDrawdown(lock_at=1.06)
    # Peak well past the lock: reference stays at the lock, not the peak.
    assert policy.reference(initial=100_000, peak=130_000, day_start=125_000) == 106_000.0
    # Below the lock it still trails.
    assert policy.reference(initial=100_000, peak=103_000, day_start=103_000) == 103_000.0


def test_locking_trailing_rejects_a_lock_at_or_below_initial():
    with pytest.raises(ValueError, match="lock_at"):
        LockingTrailingDrawdown(lock_at=1.0)


def test_trailing_reference_never_falls_below_initial():
    assert TrailingDrawdown().reference(initial=100_000, peak=90_000, day_start=90_000) == 100_000.0


# -- equity basis ----------------------------------------------------------


def test_intraday_basis_breaches_where_closing_balance_does_not():
    """A dip that recovers by the close fails intraday firms but not balance firms."""
    equity = hourly([100_000, 94_000, 99_500])
    common = dict(name="x", max_daily_loss=0.05, max_drawdown=0.50, min_trading_days=0, max_days=None)

    intraday = FirmRuleset(equity_basis=EquityBasis.INTRADAY, **common)
    closing = FirmRuleset(equity_basis=EquityBasis.CLOSING_BALANCE, **common)

    assert adjudicate(equity, intraday).outcome is Outcome.FAILED_DAILY_LOSS
    assert adjudicate(equity, closing).outcome is not Outcome.FAILED_DAILY_LOSS


# -- day counting ----------------------------------------------------------


def test_min_day_rule_requires_the_configured_trade_count():
    rule = MinDayRule(min_trades=2)
    assert not rule.counts(trades=1, volume_fraction=1.0)
    assert rule.counts(trades=2, volume_fraction=0.0)


def test_min_day_rule_requires_the_configured_volume():
    rule = MinDayRule(min_trades=1, min_volume_fraction=0.5)
    assert not rule.counts(trades=5, volume_fraction=0.1)
    assert rule.counts(trades=1, volume_fraction=0.6)


def test_token_trades_do_not_satisfy_a_volume_based_minimum():
    """The rule firms use to stop applicants ticking the box with one-lot trades."""
    equity = daily([100_000, 105_000, 111_000, 111_000])
    ruleset = FirmRuleset(
        name="v", profit_target=0.10, min_trading_days=3,
        min_day_rule=MinDayRule(min_trades=1, min_volume_fraction=0.5),
        max_daily_loss=1.0, max_drawdown=1.0, max_days=None,
    )
    token = {d.date(): SessionRecord(trades=1, volume_fraction=0.01) for d in equity.index}
    assert not adjudicate(equity, ruleset, sessions=token).passed

    real = {d.date(): SessionRecord(trades=3, volume_fraction=2.0) for d in equity.index}
    assert adjudicate(equity, ruleset, sessions=real).passed


# -- deadline basis --------------------------------------------------------


def test_calendar_deadline_counts_untraded_days():
    """A gap of flat days burns a calendar deadline but not a trading one."""
    equity = pd.Series(
        [100_000.0, 100_000.0, 100_000.0, 100_000.0],
        index=pd.to_datetime(["2024-01-01", "2024-01-10", "2024-01-20", "2024-01-30"]).tz_localize("UTC"),
    )
    calendar = FirmRuleset(name="c", max_days=15, deadline_basis=DeadlineBasis.CALENDAR,
                           max_daily_loss=1.0, max_drawdown=1.0, min_trading_days=0)
    trading = FirmRuleset(name="t", max_days=15, deadline_basis=DeadlineBasis.TRADING,
                          max_daily_loss=1.0, max_drawdown=1.0, min_trading_days=0)

    assert adjudicate(equity, calendar).outcome is Outcome.FAILED_TIMEOUT
    assert adjudicate(equity, trading).outcome is not Outcome.FAILED_TIMEOUT


# -- ruleset config --------------------------------------------------------


def test_presets_describe_themselves():
    for preset in (FTMO_LIKE, TOPSTEP_LIKE, APEX_LIKE):
        assert preset.name in preset.describe()
        assert isinstance(preset.max_days, (int, type(None)))


def test_futures_presets_use_the_cme_session_reset():
    for preset in (TOPSTEP_LIKE, APEX_LIKE):
        assert preset.session_reset == time(17, 0)
        assert preset.timezone == "America/Chicago"


def test_automated_trading_permission_defaults_to_unverified():
    """None means 'not confirmed with the firm' -- a breach voids a pass."""
    assert FirmRuleset().allows_automated_trading is None


def test_unknown_timezone_is_rejected_loudly():
    with pytest.raises(ValueError, match="unknown timezone"):
        FirmRuleset(timezone="Mars/Olympus_Mons")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"profit_target": 0.0},
        {"max_daily_loss": 0.0},
        {"max_daily_loss": 1.5},
        {"max_drawdown": 0.0},
        {"min_trading_days": -1},
        {"max_days": 0},
    ],
)
def test_ruleset_rejects_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        FirmRuleset(**kwargs)


def test_adjudicate_rejects_bad_input():
    with pytest.raises(ValueError, match="empty"):
        adjudicate(pd.Series([], dtype="float64"), FTMO_LIKE)
    with pytest.raises(TypeError, match="DatetimeIndex"):
        adjudicate(pd.Series([1.0, 2.0]), FTMO_LIKE)
