"""Phase 8: prop-firm registry, verification gating, comparison, compliance.

The governing test is unchanged from the build in which nothing could be
verified: *nothing unverified may assert compliance*. What changed is the
inputs. Rules covered by the 2026-08-15 official source review now carry
``OFFICIAL_SOURCE_VERIFIED`` provenance and can back a decision; everything the
review did not cover still refuses, and these tests pin both halves so neither
can be relaxed by accident.
"""

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone

import pytest

from ai_trading.propfirm import (
    PRIMARY_AUTOMATION_TARGET,
    REGISTRY,
    RULESET_VERSION,
    SOURCES,
    VERIFIED_AT,
    AutomationStance,
    Capability,
    ComplianceGate,
    ComplianceViolation,
    ConsistencyRule,
    DailyLossLimitMode,
    DeploymentLocation,
    DrawdownTiming,
    EligibilityOutcome,
    ExecutionTopology,
    LiveExecutionPrerequisites,
    MaxLossLimit,
    MLLMode,
    PositionLimits,
    PracticeDeclaration,
    ProhibitedPractice,
    PropFirmRegistry,
    RuleValue,
    RulesetKey,
    Stage,
    StrategyRun,
    UnverifiedRuleError,
    VerificationLevel,
    VerificationMethod,
    VerificationStatus,
    compare_strategy_across_firms,
    not_applicable,
    official_verified,
    unknown,
    user_supplied,
    verified,
)

UTC = timezone.utc


def topstep(size=50_000):
    return REGISTRY.resolve("topstep", "trading_combine", Stage.EVALUATION, size)


def xfa(size=50_000, variant="standard"):
    return REGISTRY.resolve("topstep", f"express_funded_{variant}",
                            Stage.FUNDED_SIM, size)


def apex(size=50_000):
    return REGISTRY.resolve("apex", "eod_pa", Stage.FUNDED_SIM, size)


def mffu(size=50_000, stage=Stage.EVALUATION):
    return REGISTRY.resolve("mffu", "rapid", stage, size)


def alpha():
    return REGISTRY.resolve("alpha_futures", "research_comparison_only",
                            Stage.EVALUATION, 0)


def a_run(**kw):
    defaults = dict(
        strategy_id="s1", instrument="ES", starting_balance=50_000.0,
        final_balance=53_500.0, peak_equity=54_000.0,
        max_drawdown_currency=800.0, max_daily_loss_currency=400.0,
        trading_days=6, best_day_profit=1_000.0, total_profit=3_500.0,
        max_position_minis=3, max_position_micros=0,
        daily_losses=(-200.0, -400.0), is_automated=True,
    )
    return StrategyRun(**{**defaults, **kw})


# =========================================================================
# Verification gating -- the governing behaviour
# =========================================================================


def test_unknown_rule_cannot_carry_a_value():
    """Storing a guess beside an UNKNOWN label is how guesses become facts."""
    with pytest.raises(ValueError, match="cannot carry a value"):
        RuleValue(5000, VerificationStatus.UNKNOWN)


def test_user_supplied_rule_refuses_to_back_compliance():
    rule = user_supplied(3000, label="profit_target")
    assert rule.get() == 3000
    with pytest.raises(UnverifiedRuleError, match="user_supplied"):
        rule.require("pass adjudication")


def test_third_party_source_is_insufficient_on_its_own():
    """Third-party articles are the largest source of stale prop-firm numbers."""
    assert not VerificationStatus.THIRD_PARTY.sufficient_for_compliance


def test_only_official_verification_suffices():
    assert VerificationStatus.VERIFIED_OFFICIAL.sufficient_for_compliance
    assert VerificationStatus.OFFICIAL_SOURCE_VERIFIED.sufficient_for_compliance
    assert not VerificationStatus.USER_SUPPLIED.sufficient_for_compliance
    assert not VerificationStatus.UNKNOWN.sufficient_for_compliance


def test_verified_rule_is_usable():
    rule = verified(3000, "https://example.invalid/rules",
                    datetime.now(UTC), label="profit_target")
    assert rule.require() == 3000


def test_stale_verification_is_flagged():
    old = verified(3000, "https://example.invalid/rules",
                   datetime.now(UTC) - timedelta(days=200), label="x")
    assert old.source.is_stale


def test_human_review_is_distinct_from_a_machine_fetch():
    """Both back a decision; only one of them can be re-derived automatically."""
    reviewed = official_verified(2000, url="https://example.invalid",
                                 title="Doc", verified_at=date.today(), label="mll")
    fetched = verified(2000, "https://example.invalid", datetime.now(UTC), label="mll")

    assert reviewed.require() == fetched.require() == 2000
    assert reviewed.status is VerificationStatus.OFFICIAL_SOURCE_VERIFIED
    assert (reviewed.source.verification_method
            is VerificationMethod.OFFICIAL_SOURCE_REVIEW)
    assert fetched.source.verification_method is VerificationMethod.NONE


def test_human_review_goes_stale_on_its_own_clock():
    old = official_verified(1, url="u", title="t",
                            verified_at=date.today() - timedelta(days=200),
                            label="x")
    assert old.source.is_stale


# =========================================================================
# NOT_APPLICABLE vs UNKNOWN
# =========================================================================


def test_not_applicable_is_a_fact_and_unknown_is_a_gap():
    absent = not_applicable("daily_loss_limit", "this program has none")
    missing = unknown("daily_loss_limit", "nobody checked")

    assert not absent.is_applicable
    assert missing.is_applicable          # it exists; we just don't know it
    assert absent.is_verified             # fully specified, blocks nothing
    assert not missing.is_verified


def test_not_applicable_refuses_to_produce_a_value():
    """It has no value by definition -- returning 0 would be a silent guess."""
    absent = not_applicable("daily_loss_limit")
    with pytest.raises(UnverifiedRuleError, match="NOT_APPLICABLE"):
        absent.require()


def test_not_applicable_does_not_block_adjudication_readiness():
    profile = mffu(50_000)
    assert profile.daily_loss_limit.status is VerificationStatus.NOT_APPLICABLE
    assert "daily_loss_limit" not in profile.unresolved_rules


def test_combine_daily_loss_limit_is_absent_not_unknown():
    """The Combine ships without one; that is a rule, not a missing rule."""
    rule = topstep().daily_loss_limit
    assert rule.status is VerificationStatus.NOT_APPLICABLE
    assert not rule.is_unknown


def test_apex_daily_loss_limit_is_unknown_not_absent():
    """Apex enforces one; the amount was not verified. Opposite of the Combine."""
    rule = apex().daily_loss_limit
    assert rule.is_unknown
    assert "NOT to invent" in rule.source.note


# =========================================================================
# Rule hierarchy: firm -> program -> stage -> size -> version
# =========================================================================


def test_ruleset_key_names_every_component():
    key = topstep().ruleset_key
    assert key == RulesetKey("topstep", "trading_combine", Stage.EVALUATION,
                             50_000, RULESET_VERSION)
    assert str(key) == "topstep/trading_combine/evaluation/50000@v" + RULESET_VERSION


def test_registry_navigates_the_hierarchy():
    assert "topstep" in REGISTRY.firms()
    programs = REGISTRY.programs("topstep")
    assert {"trading_combine", "express_funded_standard",
            "express_funded_consistency", "live_funded"} <= set(programs)
    assert REGISTRY.stages("topstep", "trading_combine") == [Stage.EVALUATION]
    assert REGISTRY.account_sizes("topstep", "trading_combine",
                                  Stage.EVALUATION) == [50_000, 100_000, 150_000]


def test_the_same_program_can_hold_two_stages_with_different_rules():
    """MFFU Rapid exists at both stages and they are not the same ruleset."""
    assert set(REGISTRY.stages("mffu", "rapid")) == {Stage.EVALUATION,
                                                     Stage.FUNDED_SIM}
    assert mffu(50_000, Stage.EVALUATION) is not mffu(50_000, Stage.FUNDED_SIM)


def test_resolve_defaults_to_the_latest_version():
    profile = REGISTRY.resolve("topstep", "trading_combine", Stage.EVALUATION, 50_000)
    assert profile.ruleset_version == RULESET_VERSION


def test_resolve_as_of_does_not_apply_a_later_ruleset_retroactively():
    registry = PropFirmRegistry()
    old = replace(topstep(), ruleset_version="2026.01",
                  effective_from=date(2026, 1, 1))
    registry.register(old)
    registry.register(topstep())

    early = registry.resolve("topstep", "trading_combine", Stage.EVALUATION,
                             50_000, as_of=date(2026, 3, 1))
    assert early.ruleset_version == "2026.01"
    latest = registry.resolve("topstep", "trading_combine", Stage.EVALUATION, 50_000)
    assert latest.ruleset_version == RULESET_VERSION


def test_resolve_returns_none_for_an_address_that_does_not_exist():
    assert REGISTRY.resolve("topstep", "trading_combine",
                            Stage.LIVE_FUNDED, 50_000) is None


def test_published_rulesets_are_immutable():
    registry = PropFirmRegistry()
    profile = topstep()
    registry.register(profile)
    registry.register(profile)                      # identical is fine

    changed = replace(profile, profit_target=user_supplied(9999, label="profit_target"))
    with pytest.raises(ValueError, match="immutable"):
        registry.register(changed)


# =========================================================================
# Topstep Trading Combine parameters
# =========================================================================


@pytest.mark.parametrize(
    ("size", "target", "minis", "micros"),
    [(50_000, 3_000, 5, 50), (100_000, 6_000, 10, 100), (150_000, 9_000, 15, 150)],
)
def test_topstep_combine_parameters_are_verified(size, target, minis, micros):
    profile = topstep(size)
    assert profile.initial_balance.require() == size
    assert profile.profit_target.require() == target
    assert profile.position_limits.max_minis.require() == minis
    assert profile.position_limits.max_micros.require() == micros
    assert profile.min_trading_days.require() == 2


def test_micro_mini_ratio_normalizes_exposure():
    limits = topstep().position_limits
    assert limits.mini_equivalents(minis=0, micros=50) == pytest.approx(5.0)
    assert limits.mini_equivalents(minis=2, micros=30) == pytest.approx(5.0)


def test_position_limit_blocks_excess_exposure():
    limits = topstep(50_000).position_limits
    assert limits.within_limit(minis=5) is True
    assert limits.within_limit(minis=6) is False
    assert limits.within_limit(micros=50) is True
    assert limits.within_limit(micros=60) is False


def test_position_limit_is_undecidable_when_unverified():
    limits = PositionLimits(max_minis=unknown("max_minis"),
                            max_micros=unknown("max_micros"))
    assert limits.within_limit(minis=3) is None


# =========================================================================
# Topstep MLL -- EOD trailing threshold with intraday enforcement
# =========================================================================


@pytest.mark.parametrize(("size", "mll"),
                         [(50_000, 2_000), (100_000, 3_000), (150_000, 4_500)])
def test_topstep_mll_amounts_are_verified(size, mll):
    limit = topstep(size).max_loss_limit
    assert limit.threshold.require() == mll
    assert limit.mode.require() is MLLMode.EOD_TRAILING_INTRADAY_ENFORCED
    assert limit.locks_at.require() == size


def test_topstep_mll_is_not_a_static_drawdown():
    limit = topstep().max_loss_limit
    assert limit.drawdown_type.require() == "eod_trailing"
    assert limit.mode.require().trails_on_eod_balance
    assert limit.mode.require().enforced_intraday


def test_topstep_mll_is_its_own_rule_type_not_the_ftmo_defaults():
    """The Combine's hard rule is the MLL, not 5% daily / 10% total."""
    limit = topstep().max_loss_limit
    assert isinstance(limit, MaxLossLimit)
    assert limit.fully_verified


def test_mll_starts_one_trailing_amount_below_the_balance():
    tracker = topstep(50_000).max_loss_limit.build_tracker(50_000)
    assert tracker.limit_level == 48_000
    assert not tracker.locked


def test_mll_trails_upward_on_end_of_day_balance():
    tracker = topstep(50_000).max_loss_limit.build_tracker(50_000)
    tracker.end_of_day(date(2026, 8, 17), 50_600)
    assert tracker.limit_level == 48_600


def test_mll_never_moves_downward():
    tracker = topstep(50_000).max_loss_limit.build_tracker(50_000)
    tracker.end_of_day(date(2026, 8, 17), 50_800)
    assert tracker.limit_level == 48_800
    tracker.end_of_day(date(2026, 8, 18), 50_200)      # gave some back
    assert tracker.limit_level == 48_800


def test_mll_does_not_trail_on_an_intraday_spike():
    """It trails end-of-day balance. A spike that gives back moves nothing.

    Modelling this as an intraday high-water trail is the most common way to get
    Topstep's rule wrong, and it fails accounts that would have survived.
    """
    tracker = topstep(50_000).max_loss_limit.build_tracker(50_000)
    tracker.mark(datetime(2026, 8, 17, 10, tzinfo=UTC), 51_200)
    assert tracker.limit_level == 48_000
    tracker.end_of_day(date(2026, 8, 17), 50_300)
    assert tracker.limit_level == 48_300


def test_mll_locks_once_it_reaches_the_starting_balance():
    tracker = topstep(50_000).max_loss_limit.build_tracker(50_000)
    tracker.end_of_day(date(2026, 8, 17), 52_000)      # would imply 50,000
    assert tracker.limit_level == 50_000
    assert tracker.locked

    tracker.end_of_day(date(2026, 8, 18), 56_000)      # further profit
    assert tracker.limit_level == 50_000               # frozen


def test_mll_never_exceeds_the_starting_balance():
    tracker = topstep(100_000).max_loss_limit.build_tracker(100_000)
    tracker.end_of_day(date(2026, 8, 17), 120_000)
    assert tracker.limit_level == 100_000


def test_mll_lock_emits_an_event():
    tracker = topstep(50_000).max_loss_limit.build_tracker(50_000)
    events = tracker.end_of_day(date(2026, 8, 17), 53_000)
    assert any(e.event_type.value == "mll_locked" for e in events)


def test_mll_violation_intraday_causes_immediate_liquidation():
    tracker = topstep(50_000).max_loss_limit.build_tracker(50_000)
    event = tracker.mark(datetime(2026, 8, 17, 11, tzinfo=UTC), 47_999)
    assert event is not None
    assert event.action.value == "liquidate_and_fail"
    assert tracker.breached


def test_unrealized_pnl_can_cause_an_mll_violation():
    """The account fails on an open loser even though closed balance is fine."""
    tracker = topstep(50_000).max_loss_limit.build_tracker(50_000)
    event = tracker.mark(datetime(2026, 8, 17, 11, tzinfo=UTC),
                         equity=47_900, realized_balance=49_500)
    assert event is not None
    assert event.caused_by_unrealized
    assert "unrealised" in event.detail


def test_touching_the_limit_exactly_is_a_violation():
    tracker = topstep(50_000).max_loss_limit.build_tracker(50_000)
    assert tracker.mark(datetime(2026, 8, 17, tzinfo=UTC), 48_000) is not None


def test_an_mll_breach_is_terminal():
    tracker = topstep(50_000).max_loss_limit.build_tracker(50_000)
    tracker.mark(datetime(2026, 8, 17, 11, tzinfo=UTC), 47_000)
    tracker.mark(datetime(2026, 8, 17, 12, tzinfo=UTC), 51_000)   # recovered
    assert tracker.breached
    tracker.end_of_day(date(2026, 8, 17), 51_000)
    assert tracker.limit_level == 48_000       # no trailing after death


def test_mll_headroom_reports_the_distance():
    tracker = topstep(50_000).max_loss_limit.build_tracker(50_000)
    assert tracker.headroom(49_000) == 1_000
    assert tracker.headroom(47_500) == -500


def test_mll_tracker_refuses_to_build_from_unverified_rules():
    """An XFA's MLL was not verified, so no tracker exists for it."""
    with pytest.raises(UnverifiedRuleError):
        xfa().max_loss_limit.build_tracker(50_000)


def test_mll_tracker_refuses_when_lock_behaviour_is_unknown():
    """Apex's amounts are verified; whether the threshold locks is not."""
    assert apex().max_loss_limit.threshold.require() == 2_000
    with pytest.raises(UnverifiedRuleError, match="mll_locks_at"):
        apex().max_loss_limit.build_tracker(50_000)


# =========================================================================
# Optional Daily Loss Limit
# =========================================================================


@pytest.mark.parametrize(("size", "dll"),
                         [(50_000, 1_000), (100_000, 2_000), (150_000, 3_000)])
def test_purchase_set_daily_loss_limit_amounts_are_verified(size, dll):
    assert topstep(size).purchase_set_daily_loss_limit.require() == dll


def test_combine_ships_without_a_daily_loss_limit():
    profile = topstep()
    assert profile.daily_loss_limit_mode.require() is DailyLossLimitMode.NONE
    monitor = profile.build_limit_monitor()
    assert not monitor.dll.active


def test_a_purchase_set_limit_can_be_elected():
    profile = topstep(100_000).with_daily_loss_limit(DailyLossLimitMode.PURCHASE_SET)
    assert profile.daily_loss_limit.require() == 2_000
    assert profile.daily_loss_limit_mode.require() is DailyLossLimitMode.PURCHASE_SET


def test_a_personal_limit_needs_the_traders_own_number():
    with pytest.raises(ValueError, match="needs the amount"):
        topstep().with_daily_loss_limit(DailyLossLimitMode.PERSONAL_MANUAL)


def test_a_personal_limit_is_recorded_as_user_supplied():
    """The firm never published it, so it cannot claim official provenance."""
    profile = topstep().with_daily_loss_limit(DailyLossLimitMode.PERSONAL_MANUAL, 400)
    assert profile.daily_loss_limit.status is VerificationStatus.USER_SUPPLIED
    assert profile.daily_loss_limit.get() == 400


def test_daily_loss_limit_flattens_and_locks_the_session():
    profile = topstep(50_000).with_daily_loss_limit(DailyLossLimitMode.PURCHASE_SET)
    monitor = profile.build_limit_monitor()
    monitor.start_session(date(2026, 8, 17), 50_000)

    events = monitor.mark(datetime(2026, 8, 17, 11, tzinfo=UTC), 48_900)
    assert len(events) == 1
    assert events[0].action.value == "flatten_and_lock_session"
    assert not monitor.can_trade


def test_a_daily_loss_limit_hit_is_not_an_account_failure():
    """The single most consequential distinction in this module."""
    profile = topstep(50_000).with_daily_loss_limit(DailyLossLimitMode.PURCHASE_SET)
    monitor = profile.build_limit_monitor()
    monitor.start_session(date(2026, 8, 17), 50_000)
    monitor.mark(datetime(2026, 8, 17, 11, tzinfo=UTC), 48_950)

    assert not monitor.failed
    assert not monitor.mll.breached


def test_the_evaluation_continues_the_next_session():
    profile = topstep(50_000).with_daily_loss_limit(DailyLossLimitMode.PURCHASE_SET)
    monitor = profile.build_limit_monitor()
    monitor.start_session(date(2026, 8, 17), 50_000)
    monitor.mark(datetime(2026, 8, 17, 11, tzinfo=UTC), 48_950)
    monitor.end_of_day(date(2026, 8, 17), 48_950)

    monitor.start_session(date(2026, 8, 18), 48_950)
    assert monitor.can_trade


def test_the_daily_limit_is_measured_from_the_session_open():
    profile = topstep(50_000).with_daily_loss_limit(DailyLossLimitMode.PURCHASE_SET)
    monitor = profile.build_limit_monitor()
    monitor.start_session(date(2026, 8, 18), 49_400)
    assert monitor.dll.floor == 48_400


def test_an_inactive_daily_limit_cannot_carry_an_amount():
    from ai_trading.propfirm import DailyLossLimitTracker

    with pytest.raises(ValueError, match="inactive limit"):
        DailyLossLimitTracker(amount=1_000, mode=DailyLossLimitMode.NONE)


def test_the_max_loss_limit_takes_precedence_over_the_daily_limit():
    """A dead account is not merely locked out for the session."""
    profile = topstep(50_000).with_daily_loss_limit(DailyLossLimitMode.PURCHASE_SET)
    monitor = profile.build_limit_monitor()
    monitor.start_session(date(2026, 8, 17), 50_000)

    events = monitor.mark(datetime(2026, 8, 17, 11, tzinfo=UTC), 47_500)
    assert [e.action.value for e in events] == ["liquidate_and_fail"]
    assert monitor.failed


# =========================================================================
# Consistency -- a target increase, not a failure
# =========================================================================


def test_topstep_consistency_threshold_is_verified_at_fifty_percent():
    assert topstep().consistency.max_best_day_fraction.require() == pytest.approx(0.50)


@pytest.mark.parametrize(("size", "best_day"),
                         [(50_000, 1_500), (100_000, 3_000), (150_000, 4_500)])
def test_recommended_maximum_best_day_is_verified(size, best_day):
    assert topstep(size).consistency.recommended_max_best_day.require() == best_day


def test_consistency_computes_the_ratio():
    result = topstep().consistency.evaluate(best_day_profit=1_000.0,
                                            total_profit=3_000.0)
    assert result.best_day_percentage == pytest.approx(1 / 3)


def test_a_compliant_distribution_is_eligible():
    result = topstep().consistency.evaluate(1_000.0, 3_000.0)
    assert result.outcome is EligibilityOutcome.ELIGIBLE
    assert result.passes is True


def test_exceeding_the_guideline_is_not_a_rule_violation():
    result = topstep().consistency.evaluate(2_000.0, 3_000.0)
    assert result.outcome is EligibilityOutcome.CONSISTENCY_NOT_MET
    assert not result.outcome.is_failure


def test_exceeding_the_guideline_raises_the_profit_target():
    """67% on one day means the total has to grow, not that the account dies."""
    result = topstep().consistency.evaluate(2_000.0, 3_000.0)
    assert result.required_total_profit == pytest.approx(4_000.0)
    assert "profit target rises" in result.reason


def test_the_raised_target_makes_the_best_day_compliant():
    rule = topstep().consistency
    result = rule.evaluate(2_000.0, 3_000.0)
    retried = rule.evaluate(2_000.0, result.required_total_profit + 1)
    assert retried.outcome is EligibilityOutcome.ELIGIBLE


def test_consistency_withholds_the_decision_while_unverified():
    """A consistency call made against a guessed percentage is worse than none."""
    rule = ConsistencyRule(
        max_best_day_fraction=unknown("max_best_day_fraction"),
        applies_to=user_supplied("evaluation", label="a"),
    )
    result = rule.evaluate(2_000.0, 3_000.0)
    assert result.outcome is EligibilityOutcome.UNDETERMINED
    assert result.passes is None


def test_consistency_handles_no_profit():
    assert topstep().consistency.evaluate(0.0, 0.0).best_day_percentage is None


def test_mffu_consistency_also_does_not_fail_the_account():
    rule = mffu(50_000).consistency
    assert rule.max_best_day_fraction.require() == pytest.approx(0.50)
    assert "does not fail the account" in rule.target_increase_effect.require()
    assert (rule.evaluate(2_000.0, 3_000.0).outcome
            is EligibilityOutcome.CONSISTENCY_NOT_MET)


# =========================================================================
# XFA vs Combine objectives, and payout caps
# =========================================================================


def test_an_express_funded_account_has_no_evaluation_profit_target():
    rule = xfa().profit_target
    assert rule.status is VerificationStatus.NOT_APPLICABLE
    assert "not inherited" in rule.source.note


def test_combine_objectives_are_not_inherited_by_the_xfa():
    """The Combine's MLL must not silently become the XFA's."""
    assert topstep(50_000).max_loss_limit.threshold.require() == 2_000
    assert xfa(50_000).max_loss_limit.threshold.is_unknown


def test_the_xfa_has_no_minimum_trading_days():
    assert xfa().min_trading_days.status is VerificationStatus.NOT_APPLICABLE


@pytest.mark.parametrize(("size", "standard", "consistency"),
                         [(50_000, 2_000, 3_000), (100_000, 3_000, 4_000),
                          (150_000, 5_000, 6_000)])
def test_payout_caps_are_verified_per_structure(size, standard, consistency):
    assert xfa(size, "standard").payout_policy.xfa.first_payout_cap.require() == standard
    assert (xfa(size, "consistency").payout_policy.xfa.first_payout_cap.require()
            == consistency)


def test_payout_caps_are_stored_apart_from_risk_rules():
    """A payout cap is not a profit target and must not be enforced as one."""
    profile = xfa(50_000, "standard")
    assert profile.payout_policy is not None
    assert "not a Combine profit target" in profile.payout_policy.xfa.note
    assert "first_payout_cap" not in profile.all_rules


def test_payout_caps_do_not_affect_eligibility():
    outcome = compare_strategy_across_firms(a_run(), [xfa()])[xfa().key]
    assert all("payout" not in reason for reason in outcome.failure_reasons)


def test_the_xfa_profit_split_is_verified():
    assert xfa().profit_split.require() == pytest.approx(0.90)


def test_the_two_xfa_variants_are_separate_programs():
    assert xfa(50_000, "standard").key != xfa(50_000, "consistency").key


def test_live_funded_is_registered_but_unverified():
    profile = REGISTRY.resolve("topstep", "live_funded", Stage.LIVE_FUNDED, 50_000)
    assert profile is not None
    assert profile.max_loss_limit.threshold.is_unknown
    assert "LIVE CAPITAL" in profile.notes


# =========================================================================
# Apex -- EOD vs intraday drawdown semantics
# =========================================================================


@pytest.mark.parametrize(
    ("size", "drawdown", "contracts"),
    [(25_000, 1_000, 2), (50_000, 2_000, 4), (100_000, 3_000, 6), (150_000, 4_000, 10)],
)
def test_apex_tiers_are_verified(size, drawdown, contracts):
    profile = apex(size)
    assert profile.max_loss_limit.threshold.require() == drawdown
    assert profile.position_limits.max_minis.require() == contracts


def test_apex_drawdown_is_computed_end_of_day_not_intraday_trailing():
    limit = apex().max_loss_limit
    assert limit.timing.require() is DrawdownTiming.END_OF_DAY
    assert "intraday trailing drawdown does not apply" in limit.calculation_method.require()


def test_apex_threshold_is_still_enforced_during_the_session():
    """Computed once a day, enforced continuously -- two different clocks."""
    mode = apex().max_loss_limit.mode.require()
    assert mode.trails_on_eod_balance
    assert mode.enforced_intraday


def test_apex_and_topstep_differ_in_amount_not_shape():
    assert (apex(50_000).max_loss_limit.mode.require()
            is topstep(50_000).max_loss_limit.mode.require())
    assert apex(150_000).max_loss_limit.threshold.require() == 4_000
    assert topstep(150_000).max_loss_limit.threshold.require() == 4_500


def test_apex_profit_split_recorded():
    assert apex().profit_split.require() == pytest.approx(1.00)


def test_apex_evaluation_values_are_not_invented():
    """The instruction was explicit: do not invent missing evaluation values."""
    assert REGISTRY.resolve("apex", "eod_pa", Stage.EVALUATION, 50_000) is None


def test_apex_scaling_is_tier_based():
    contracts = [apex(size).position_limits.max_minis.require()
                 for size in (25_000, 50_000, 100_000, 150_000)]
    assert contracts == sorted(contracts)


# =========================================================================
# MFFU -- evaluation EOD vs funded stage
# =========================================================================


@pytest.mark.parametrize(
    ("size", "target", "max_loss"),
    [(25_000, 1_500, 1_000), (50_000, 3_000, 2_000),
     (100_000, 6_000, 3_000), (150_000, 9_000, 4_500)],
)
def test_mffu_rapid_evaluation_values_are_verified(size, target, max_loss):
    profile = mffu(size)
    assert profile.profit_target.require() == target
    assert profile.max_loss_limit.threshold.require() == max_loss


def test_mffu_rapid_evaluation_drawdown_is_end_of_day():
    assert mffu().max_loss_limit.timing.require() is DrawdownTiming.END_OF_DAY
    assert mffu().max_loss_limit.drawdown_type.require() == "eod"


def test_mffu_rapid_has_no_daily_loss_limit_at_any_size():
    for size in (25_000, 50_000, 100_000, 150_000):
        assert mffu(size).daily_loss_limit.status is VerificationStatus.NOT_APPLICABLE
        assert mffu(size).daily_loss_limit_mode.require() is DailyLossLimitMode.NONE


def test_mffu_minimum_trading_days_is_two():
    assert mffu().min_trading_days.require() == 2


def test_mffu_funded_stage_rules_are_stored_separately():
    evaluation = mffu(50_000, Stage.EVALUATION)
    funded = mffu(50_000, Stage.FUNDED_SIM)
    assert evaluation.key != funded.key
    assert evaluation.max_loss_limit.threshold.require() == 2_000
    assert funded.max_loss_limit.threshold.is_unknown


def test_mffu_funded_drawdown_type_is_not_assumed_to_match_the_evaluation():
    """The evaluation is EOD; nothing says the funded stage is."""
    funded = mffu(50_000, Stage.FUNDED_SIM)
    assert funded.max_loss_limit.drawdown_type.is_unknown
    assert ("must not be assumed to match"
            in funded.max_loss_limit.drawdown_type.source.note)


def test_mffu_evaluation_enforcement_timing_is_not_assumed():
    """EOD is verified; whether it also bites intraday is not."""
    assert mffu().max_loss_limit.mode.is_unknown
    with pytest.raises(UnverifiedRuleError):
        mffu().build_limit_monitor()


# =========================================================================
# Adjudication readiness
# =========================================================================


def test_readiness_is_three_way():
    assert topstep().verification_level is VerificationLevel.PARTIALLY_VERIFIED
    assert alpha().verification_level is VerificationLevel.UNVERIFIED


def test_a_partially_verified_profile_is_not_adjudication_ready():
    assert topstep() not in REGISTRY.adjudication_ready()


def test_readiness_is_scoped_per_capability():
    """Requiring every field would refuse capabilities whose inputs are sourced."""
    profile = topstep()
    assert (profile.readiness(Capability.LOSS_LIMIT_TRACKING)
            is VerificationLevel.ADJUDICATION_READY)
    assert (profile.readiness(Capability.SESSION_BOUNDARY_ENFORCEMENT)
            is VerificationLevel.UNVERIFIED)


def test_the_session_boundary_is_the_one_thing_holding_the_combine_back():
    missing = topstep().missing_for(Capability.FULL_ADJUDICATION)
    assert set(missing) == {"trading_day_start", "trading_day_end",
                            "forced_flat_time", "session_reopen",
                            "overnight_allowed"}


def test_the_session_boundary_records_why_it_is_unverified():
    assert "pending verification" in topstep().trading_day_start.source.note


def test_topstep_trading_day_values_are_still_recorded_in_ct():
    profile = topstep()
    assert profile.trading_day_start.get() == time(17, 0)
    assert profile.trading_day_end.get() == time(15, 10)
    assert profile.forced_flat_time.get() == time(15, 10)
    assert profile.session_reopen.get() == time(17, 0)
    assert profile.timezone == "America/Chicago"


def test_an_unsupported_capability_refuses_explicitly():
    with pytest.raises(UnverifiedRuleError, match="session_boundary_enforcement"):
        topstep().require_capability(Capability.SESSION_BOUNDARY_ENFORCEMENT)


def test_capabilities_a_program_does_not_have_are_not_required():
    """A Standard Express Funded account has no consistency rule at all."""
    profile = xfa(50_000, "standard")
    assert profile.required_fields(Capability.CONSISTENCY_EVALUATION) == ()
    assert profile.supports(Capability.CONSISTENCY_EVALUATION)


def test_profile_refuses_adjudication_when_partially_verified():
    with pytest.raises(UnverifiedRuleError, match="unverified rule"):
        topstep().require_adjudication_ready()


def test_registry_can_list_by_verification_level():
    assert alpha() in REGISTRY.by_verification_level(VerificationLevel.UNVERIFIED)


# =========================================================================
# Source provenance
# =========================================================================


def test_every_verified_field_records_its_source():
    for record in topstep().field_provenance():
        if record.status != "official_source_verified":
            continue
        assert record.source_url.startswith("https://")
        assert record.source_title
        assert record.verified_at == VERIFIED_AT
        assert record.verification_method == "official_source_review"
        assert record.ruleset_version == RULESET_VERSION


def test_provenance_covers_unverified_fields_too():
    """An audit that omits the gaps cannot answer the question it exists for."""
    names = {r.field_name for r in topstep().field_provenance()}
    assert "trading_day_start" in names
    assert names == set(topstep().all_rules)


def test_provenance_distinguishes_the_source_document_per_field():
    records = {r.field_name: r for r in topstep().field_provenance()}
    assert records["mll_threshold"].source_url == SOURCES["topstep_mll"][1]
    assert records["profit_target"].source_url == SOURCES["topstep_combine"][1]
    assert records["max_best_day_fraction"].source_url == SOURCES["topstep_consistency"][1]


def test_provenance_survives_serialization():
    payload = topstep().to_dict()
    entry = next(r for r in payload["field_provenance"]
                 if r["field_name"] == "mll_threshold")
    assert entry["value"] == 2_000
    assert entry["verified_at"] == VERIFIED_AT.isoformat()


def test_no_profile_claims_a_machine_fetch():
    """Nothing here was fetched by this code, and the record must not imply it."""
    for profile in REGISTRY.all():
        assert profile.retrieved_at is None
        for record in profile.field_provenance():
            assert record.status != VerificationStatus.VERIFIED_OFFICIAL.value
            assert record.retrieved_at is None


def test_every_profile_carries_required_version_metadata():
    for profile in REGISTRY.all():
        payload = profile.to_dict()
        for name in ("firm_id", "program_id", "stage", "account_size",
                     "ruleset_version", "effective_from", "source_url",
                     "retrieved_at", "verification_status", "verification_level"):
            assert name in payload


# =========================================================================
# Automation policy, topology and compliance
# =========================================================================


def test_topstep_automation_policy_is_verified_as_allowed():
    automation = topstep().automation
    assert automation.stance.require() is AutomationStance.ALLOWED
    assert automation.api_available.require() is True
    assert automation.api_provider.require() == "TopstepX"


def test_ai_itself_is_not_prohibited():
    """The rule targets exploitation, not automation; conflating them would
    refuse a permitted activity."""
    assert topstep().automation.permits_full_automation is True


def test_prohibited_practices_are_recorded():
    automation = topstep().automation
    for practice in (ProhibitedPractice.SPOOFING,
                     ProhibitedPractice.STALE_FEED_EXPLOITATION,
                     ProhibitedPractice.CROSS_ACCOUNT_HEDGING,
                     ProhibitedPractice.MAX_SIZE_INTO_NEWS):
        assert practice in automation.prohibited_practices


def test_local_execution_is_required_and_vps_prohibited():
    automation = topstep().automation
    assert automation.requires_local_execution.require() is True
    assert automation.prohibits_vps.require() is True


def test_topstep_is_the_primary_automation_target():
    assert PRIMARY_AUTOMATION_TARGET == "topstep"


def test_cloud_deployment_is_refused_where_remote_servers_are_prohibited():
    """A rules breach voids an account regardless of the equity curve."""
    permitted, reason = ExecutionTopology(DeploymentLocation.CLOUD).check(topstep())
    assert not permitted
    assert "personal device" in reason


def test_vps_deployment_is_refused():
    permitted, _ = ExecutionTopology(DeploymentLocation.VPS).check(topstep())
    assert not permitted


def test_local_deployment_passes_the_topology_check():
    permitted, reason = ExecutionTopology(DeploymentLocation.LOCAL_DEVICE).check(topstep())
    assert permitted
    assert "local_device" in reason


def test_unknown_vps_policy_fails_closed():
    permitted, reason = ExecutionTopology(DeploymentLocation.LOCAL_DEVICE).check(apex())
    assert not permitted
    assert "unverified" in reason


def test_compliance_gate_blocks_on_the_unverified_session_boundary():
    permitted, blockers = ComplianceGate(
        topstep(), ExecutionTopology(DeploymentLocation.LOCAL_DEVICE)
    ).evaluate()
    assert not permitted
    assert any("unverified" in b for b in blockers)


def test_compliance_gate_blocks_declared_prohibited_practices():
    gate = ComplianceGate(
        topstep(), ExecutionTopology(DeploymentLocation.LOCAL_DEVICE),
        PracticeDeclaration(uses_stale_feed=True, places_and_cancels_rapidly=True),
    )
    _, blockers = gate.evaluate()
    assert any("stale_feed_exploitation" in b for b in blockers)
    assert any("spoofing" in b for b in blockers)


def test_compliance_gate_raises_on_require():
    with pytest.raises(ComplianceViolation, match="execution refused"):
        ComplianceGate(topstep(), ExecutionTopology(DeploymentLocation.CLOUD)).require()


def test_live_prerequisites_start_unmet():
    prerequisites = LiveExecutionPrerequisites()
    assert not prerequisites.ready
    assert len(prerequisites.outstanding) == 6
    with pytest.raises(ComplianceViolation, match="prerequisites outstanding"):
        prerequisites.require()


def test_live_prerequisites_ready_only_when_all_met():
    prerequisites = LiveExecutionPrerequisites(
        strategy_has_out_of_sample_evidence=True, firm_rules_verified=True,
        compliance_policy_verified=True, api_behaviour_tested=True,
        execution_reliability_tested=True, local_execution_operational=True,
    )
    assert prerequisites.ready
    prerequisites.require()


def test_no_firm_api_client_ships():
    """The provider is an interface; no implementation exists."""
    from ai_trading.propfirm.execution import FirmExecutionProvider

    with pytest.raises(TypeError):
        FirmExecutionProvider()


# =========================================================================
# Alpha Futures
# =========================================================================


def test_alpha_stays_semi_automation_only():
    automation = alpha().automation
    assert automation.stance.get() is AutomationStance.SEMI_ONLY
    assert automation.permits_full_automation is False


def test_alpha_semi_automation_is_recorded_as_allowed():
    assert "semi-automation" in alpha().automation.stance.source.note


def test_alpha_is_not_a_live_automation_target():
    assert "RESEARCH COMPARISON ONLY" in alpha().notes
    assert PRIMARY_AUTOMATION_TARGET != "alpha_futures"


def test_alpha_automation_stance_is_not_verified():
    """Semi-automation is retained pending verification, not asserted."""
    assert not alpha().automation.stance.is_verified


def test_automated_run_fails_alpha_compatibility():
    outcome = compare_strategy_across_firms(a_run(is_automated=True), [alpha()])
    result = next(iter(outcome.values()))
    assert result.automation_compatible is False
    assert any("automation stance" in r for r in result.failure_reasons)


def test_manual_run_is_compatible_with_alpha():
    outcome = compare_strategy_across_firms(a_run(is_automated=False), [alpha()])
    assert next(iter(outcome.values())).automation_compatible is True


# =========================================================================
# Comparison engine
# =========================================================================


def test_registry_lists_firms_separately():
    assert set(REGISTRY.firms()) == {"topstep", "apex", "mffu", "alpha_futures"}


def test_comparison_reports_each_firm_separately():
    profiles = [topstep(50_000), apex(50_000), mffu(50_000)]
    outcomes = compare_strategy_across_firms(a_run(), profiles)
    assert len(outcomes) == 3
    assert all(isinstance(v.firm_key, str) for v in outcomes.values())


def test_comparison_never_aggregates_into_one_score():
    outcomes = compare_strategy_across_firms(a_run(), [topstep(), apex()])
    assert not hasattr(outcomes, "overall_score")
    assert set(outcomes) == {topstep().key, apex().key}


def test_comparison_withholds_a_verdict_while_a_rule_is_unverified():
    outcome = compare_strategy_across_firms(a_run(), [apex()])[apex().key]
    assert outcome.passed is None
    assert not outcome.decidable
    assert outcome.undecidable_reasons


def test_comparison_still_reports_measurements():
    """Measurements are always available; only the verdict is withheld."""
    outcome = compare_strategy_across_firms(a_run(), [apex()])[apex().key]
    assert outcome.max_drawdown_currency == 800.0


def test_comparison_records_rules_that_do_not_apply():
    outcome = compare_strategy_across_firms(a_run(), [mffu()])[mffu().key]
    assert "daily_loss_limit" in outcome.not_applicable_rules


def test_the_combine_is_decidable_on_its_verified_rules():
    outcome = compare_strategy_across_firms(a_run(), [topstep()])[topstep().key]
    assert outcome.decidable
    assert outcome.eligibility is EligibilityOutcome.ELIGIBLE


def test_a_consistency_shortfall_is_reported_separately_from_a_violation():
    outcome = compare_strategy_across_firms(
        a_run(best_day_profit=3_000.0, total_profit=3_500.0), [topstep()]
    )[topstep().key]
    assert outcome.eligibility is EligibilityOutcome.CONSISTENCY_NOT_MET
    assert outcome.failure_reasons == []
    assert outcome.adjusted_profit_target == pytest.approx(6_000.0)


def test_a_drawdown_breach_is_a_violation():
    outcome = compare_strategy_across_firms(
        a_run(max_drawdown_currency=2_500.0), [topstep()]
    )[topstep().key]
    assert outcome.eligibility is EligibilityOutcome.RULE_VIOLATION
    assert any("max drawdown" in r for r in outcome.failure_reasons)


def test_a_position_limit_breach_is_a_violation():
    outcome = compare_strategy_across_firms(
        a_run(max_position_minis=9), [topstep()]
    )[topstep().key]
    assert outcome.passed is False
    assert outcome.position_limit_violations == 1


def test_a_short_evaluation_is_a_violation():
    outcome = compare_strategy_across_firms(a_run(trading_days=1),
                                            [topstep()])[topstep().key]
    assert outcome.passed is False
    assert any("trading days" in r for r in outcome.failure_reasons)


def test_daily_loss_hits_are_counted_as_lockouts_not_failures():
    profile = topstep().with_daily_loss_limit(DailyLossLimitMode.PURCHASE_SET)
    outcome = compare_strategy_across_firms(
        a_run(daily_losses=(-1_200.0, -300.0)), [profile]
    )[profile.key]
    assert outcome.daily_loss_lockouts == 1
    assert outcome.failure_reasons == []
    assert outcome.eligibility is EligibilityOutcome.ELIGIBLE
