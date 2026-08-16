"""Phase 9: prop-firm rules as versioned, time-dependent data.

The governing behaviour is that replaying a historical challenge uses the rules
that were in force *then*. Today's rules are never substituted, and there is no
lookup that omits the date.
"""

from datetime import date

import pytest

from ai_trading.propfirm.snapshots import (
    NoRulesetError,
    OverlappingSnapshotError,
    ResolvedRuleset,
    RuleSnapshot,
    RuleSnapshotError,
    RuleSnapshotStore,
    RuleVerificationLevel,
    get_ruleset,
)

def snapshot(field_name="mll_threshold", value=2_000, *, effective_from=date(2026, 1, 1),
             effective_to=None, status=RuleVerificationLevel.SOURCE_VERIFIED,
             version="2026.01", account_size=50_000, applicable=True,
             firm="topstep", program="trading_combine"):
    return RuleSnapshot(
        firm_id=firm, program_id=program, account_size=account_size,
        field_name=field_name, value=value,
        effective_from=effective_from, effective_to=effective_to,
        source_url="https://help.topstep.com/example",
        source_title="Trading Combine Parameters",
        retrieved_at=None, verified_at=effective_from,
        verification_method="official_source_review",
        verification_status=status, ruleset_version=version,
        applicable=applicable,
    )


def two_version_store():
    """The same field, changed mid-year. The core scenario."""
    store = RuleSnapshotStore()
    store.add(snapshot(value=2_000, effective_from=date(2026, 1, 1),
                       effective_to=date(2026, 7, 1), version="2026.01"))
    store.add(snapshot(value=2_500, effective_from=date(2026, 7, 1),
                       version="2026.07"))
    return store


# =========================================================================
# Verification levels -- five, uncollapsed
# =========================================================================


def test_five_levels_exist_and_are_ordered():
    levels = list(RuleVerificationLevel)
    assert [level.value for level in levels] == [
        "unknown", "user_supplied", "source_verified",
        "machine_verified", "runtime_verified",
    ]
    assert [level.rank for level in levels] == sorted(level.rank for level in levels)


def test_levels_compare_without_being_collapsed():
    assert RuleVerificationLevel.RUNTIME_VERIFIED > RuleVerificationLevel.MACHINE_VERIFIED
    assert RuleVerificationLevel.MACHINE_VERIFIED > RuleVerificationLevel.SOURCE_VERIFIED
    assert RuleVerificationLevel.SOURCE_VERIFIED > RuleVerificationLevel.USER_SUPPLIED
    # Comparable, but still five distinct values -- nothing maps onto anything.
    assert len({level.value for level in RuleVerificationLevel}) == 5


def test_the_top_three_levels_back_compliance():
    for level in (RuleVerificationLevel.SOURCE_VERIFIED,
                  RuleVerificationLevel.MACHINE_VERIFIED,
                  RuleVerificationLevel.RUNTIME_VERIFIED):
        assert level.sufficient_for_compliance
    for level in (RuleVerificationLevel.USER_SUPPLIED, RuleVerificationLevel.UNKNOWN):
        assert not level.sufficient_for_compliance


def test_source_verified_is_sufficient_but_not_reverifiable():
    """The distinction the levels exist to preserve.

    A human-read value backs a decision and still rots silently, because
    nothing re-reads the page.
    """
    level = RuleVerificationLevel.SOURCE_VERIFIED
    assert level.sufficient_for_compliance
    assert not level.is_reverifiable
    assert RuleVerificationLevel.MACHINE_VERIFIED.is_reverifiable


def test_unknown_snapshot_cannot_carry_a_value():
    with pytest.raises(ValueError, match="cannot carry a value"):
        snapshot(value=2_000, status=RuleVerificationLevel.UNKNOWN)


def test_a_not_applicable_snapshot_cannot_carry_a_value():
    with pytest.raises(ValueError, match="cannot carry a value"):
        snapshot(value=0, applicable=False)


def test_a_not_applicable_rule_counts_as_verified():
    """A rule that does not exist is fully specified, not a gap."""
    absent = snapshot("daily_loss_limit", None, applicable=False,
                      status=RuleVerificationLevel.SOURCE_VERIFIED)
    assert absent.is_verified
    with pytest.raises(RuleSnapshotError, match="not applicable"):
        absent.require()


def test_an_unverified_snapshot_refuses_to_produce_its_value():
    weak = snapshot(value=2_000, status=RuleVerificationLevel.USER_SUPPLIED)
    assert weak.value == 2_000
    with pytest.raises(RuleSnapshotError, match="user_supplied"):
        weak.require()


# =========================================================================
# Interval semantics
# =========================================================================


def test_an_interval_must_move_forward():
    with pytest.raises(ValueError, match="does not follow"):
        snapshot(effective_from=date(2026, 7, 1), effective_to=date(2026, 1, 1))


def test_effective_to_is_exclusive():
    """The changeover day belongs to the new version, and only to it."""
    old = snapshot(value=2_000, effective_from=date(2026, 1, 1),
                   effective_to=date(2026, 7, 1))
    new = snapshot(value=2_500, effective_from=date(2026, 7, 1))

    assert old.covers(date(2026, 6, 30))
    assert not old.covers(date(2026, 7, 1))
    assert new.covers(date(2026, 7, 1))


def test_an_open_ended_snapshot_covers_the_far_future():
    current = snapshot(effective_from=date(2026, 1, 1))
    assert current.is_open_ended
    assert current.covers(date(2099, 1, 1))


def test_overlapping_snapshots_are_refused_at_write_time():
    store = RuleSnapshotStore()
    store.add(snapshot(effective_from=date(2026, 1, 1), effective_to=date(2026, 7, 1)))
    with pytest.raises(OverlappingSnapshotError, match="already has a snapshot"):
        store.add(snapshot(value=2_500, effective_from=date(2026, 6, 1)))


def test_two_open_ended_snapshots_for_one_field_are_refused():
    store = RuleSnapshotStore()
    store.add(snapshot(effective_from=date(2026, 1, 1)))
    with pytest.raises(OverlappingSnapshotError):
        store.add(snapshot(value=2_500, effective_from=date(2026, 7, 1)))


def test_different_fields_do_not_collide():
    store = RuleSnapshotStore()
    store.add(snapshot("mll_threshold", 2_000))
    store.add(snapshot("profit_target", 3_000))
    assert len(store) == 2


def test_different_account_sizes_do_not_collide():
    store = RuleSnapshotStore()
    store.add(snapshot(account_size=50_000))
    store.add(snapshot(value=3_000, account_size=100_000))
    assert len(store) == 2


def test_supersede_closes_the_old_interval_and_opens_the_new():
    store = RuleSnapshotStore()
    store.add(snapshot(value=2_000, effective_from=date(2026, 1, 1)))
    store.supersede(snapshot(value=2_500, version="2026.07"), on=date(2026, 7, 1))

    history = store.history("topstep", "trading_combine", 50_000, "mll_threshold")
    assert [h.value for h in history] == [2_000, 2_500]
    assert history[0].effective_to == date(2026, 7, 1)
    assert history[1].is_open_ended


def test_supersede_leaves_no_gap_in_the_timeline():
    store = RuleSnapshotStore()
    store.add(snapshot(value=2_000, effective_from=date(2026, 1, 1)))
    store.supersede(snapshot(value=2_500), on=date(2026, 7, 1))

    for day in (date(2026, 6, 30), date(2026, 7, 1)):
        resolved = store.get_ruleset("topstep", "trading_combine", 50_000, day)
        assert "mll_threshold" in resolved


def test_supersede_refuses_a_date_before_the_current_snapshot_starts():
    store = RuleSnapshotStore()
    store.add(snapshot(effective_from=date(2026, 7, 1)))
    with pytest.raises(ValueError, match="only takes effect"):
        store.supersede(snapshot(value=2_500), on=date(2026, 3, 1))


# =========================================================================
# As-of lookup -- the governing behaviour
# =========================================================================


def test_the_same_firm_resolves_differently_on_two_dates():
    """The whole point: two historical versions, two answers."""
    store = two_version_store()

    march = store.get_ruleset("topstep", "trading_combine", 50_000, date(2026, 3, 15))
    september = store.get_ruleset("topstep", "trading_combine", 50_000,
                                  date(2026, 9, 15))

    assert march["mll_threshold"].value == 2_000
    assert september["mll_threshold"].value == 2_500


def test_a_historical_replay_never_receives_current_rules():
    """A March challenge is adjudicated against March's numbers."""
    store = two_version_store()
    resolved = store.get_ruleset("topstep", "trading_combine", 50_000,
                                 date(2026, 3, 15))

    assert resolved["mll_threshold"].value == 2_000
    assert resolved["mll_threshold"].ruleset_version == "2026.01"
    assert resolved.as_of == date(2026, 3, 15)


def test_the_resolved_ruleset_carries_the_date_it_answers_for():
    """So no report can print a ruleset without saying when it applied."""
    resolved = two_version_store().get_ruleset("topstep", "trading_combine",
                                               50_000, date(2026, 3, 15))
    assert isinstance(resolved, ResolvedRuleset)
    assert resolved.to_dict()["as_of"] == "2026-03-15"


def test_lookup_refuses_a_missing_as_of():
    store = two_version_store()
    with pytest.raises(TypeError):
        store.get_ruleset("topstep", "trading_combine", 50_000, None)


def test_lookup_has_no_default_date():
    """There is no overload that quietly means 'today'."""
    store = two_version_store()
    with pytest.raises(TypeError):
        store.get_ruleset("topstep", "trading_combine", 50_000)   # type: ignore[call-arg]


def test_a_date_before_any_snapshot_refuses_rather_than_falling_back():
    store = two_version_store()
    with pytest.raises(NoRulesetError, match="Current rules are not substituted"):
        store.get_ruleset("topstep", "trading_combine", 50_000, date(2025, 5, 1))


def test_an_unknown_account_refuses():
    store = two_version_store()
    with pytest.raises(NoRulesetError):
        store.get_ruleset("topstep", "trading_combine", 999_999, date(2026, 3, 1))


def test_the_module_level_function_matches_the_method():
    store = two_version_store()
    a = get_ruleset(store, "topstep", "trading_combine", 50_000, date(2026, 3, 1))
    b = store.get_ruleset("topstep", "trading_combine", 50_000, date(2026, 3, 1))
    assert a.to_dict() == b.to_dict()


def test_fields_changing_on_different_dates_report_both_versions():
    store = RuleSnapshotStore()
    store.add(snapshot("mll_threshold", 2_000, effective_from=date(2026, 1, 1),
                       effective_to=date(2026, 7, 1), version="2026.01"))
    store.add(snapshot("mll_threshold", 2_500, effective_from=date(2026, 7, 1),
                       version="2026.07"))
    store.add(snapshot("profit_target", 3_000, effective_from=date(2026, 1, 1),
                       version="2026.01"))

    resolved = store.get_ruleset("topstep", "trading_combine", 50_000,
                                 date(2026, 9, 1))
    assert resolved.ruleset_versions == ["2026.01", "2026.07"]


def test_history_lists_every_version_oldest_first():
    history = two_version_store().history("topstep", "trading_combine", 50_000,
                                          "mll_threshold")
    assert [h.ruleset_version for h in history] == ["2026.01", "2026.07"]


def test_coverage_reports_an_open_end_as_none():
    start, end = two_version_store().coverage("topstep", "trading_combine", 50_000)
    assert start == date(2026, 1, 1)
    assert end is None


# =========================================================================
# Resolved ruleset behaviour
# =========================================================================


def test_resolved_ruleset_requires_verified_values():
    store = RuleSnapshotStore()
    store.add(snapshot("mll_threshold", 2_000))
    store.add(snapshot("activation_fee", 149,
                       status=RuleVerificationLevel.USER_SUPPLIED))
    resolved = store.get_ruleset("topstep", "trading_combine", 50_000,
                                 date(2026, 3, 1))

    assert resolved.require("mll_threshold") == 2_000
    with pytest.raises(RuleSnapshotError, match="user_supplied"):
        resolved.require("activation_fee")


def test_resolved_ruleset_requires_a_field_that_exists():
    resolved = two_version_store().get_ruleset("topstep", "trading_combine",
                                               50_000, date(2026, 3, 1))
    with pytest.raises(RuleSnapshotError, match="no snapshot in force"):
        resolved.require("nonexistent_field")


def test_resolved_ruleset_reports_unverified_fields():
    store = RuleSnapshotStore()
    store.add(snapshot("mll_threshold", 2_000))
    store.add(snapshot("activation_fee", 149,
                       status=RuleVerificationLevel.USER_SUPPLIED))
    resolved = store.get_ruleset("topstep", "trading_combine", 50_000,
                                 date(2026, 3, 1))

    assert resolved.unverified_fields == ["activation_fee"]
    assert not resolved.fully_verified


def test_resolved_ruleset_filters_by_verification_level():
    store = RuleSnapshotStore()
    store.add(snapshot("mll_threshold", 2_000,
                       status=RuleVerificationLevel.RUNTIME_VERIFIED))
    store.add(snapshot("profit_target", 3_000,
                       status=RuleVerificationLevel.SOURCE_VERIFIED))
    resolved = store.get_ruleset("topstep", "trading_combine", 50_000,
                                 date(2026, 3, 1))

    assert resolved.at_least(RuleVerificationLevel.RUNTIME_VERIFIED) == ["mll_threshold"]
    assert resolved.at_least(RuleVerificationLevel.SOURCE_VERIFIED) == [
        "mll_threshold", "profit_target"]


def test_resolved_ruleset_behaves_like_a_mapping():
    resolved = two_version_store().get_ruleset("topstep", "trading_combine",
                                               50_000, date(2026, 3, 1))
    assert "mll_threshold" in resolved
    assert len(resolved) == 1
    assert list(resolved) == ["mll_threshold"]
    assert resolved.value("mll_threshold") == 2_000
    assert resolved.value("absent", "fallback") == "fallback"


def test_snapshot_serializes_every_required_field():
    payload = snapshot().to_dict()
    for name in ("firm_id", "program_id", "account_size", "field_name", "value",
                 "effective_from", "effective_to", "source_url", "source_title",
                 "retrieved_at", "verified_at", "verification_method",
                 "verification_status", "ruleset_version"):
        assert name in payload
