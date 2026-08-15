"""Phase 8: prop-firm registry, verification gating, comparison, compliance.

The governing test is that *nothing unverified can assert compliance*. Every
value in this build is operator-supplied or unknown, so the correct behaviour is
refusal — and these tests pin that refusal so it cannot be relaxed by accident.
"""

from datetime import date, datetime, time, timedelta, timezone

import pytest

from ai_trading.propfirm import (
    PRIMARY_AUTOMATION_TARGET,
    AutomationPolicy,
    REGISTRY,
    AutomationStance,
    ComplianceGate,
    ComplianceViolation,
    ConsistencyRule,
    DeploymentLocation,
    DrawdownTiming,
    ExecutionTopology,
    FirmProfile,
    LiveExecutionPrerequisites,
    MaxLossLimit,
    PositionLimits,
    PracticeDeclaration,
    ProhibitedPractice,
    PropFirmRegistry,
    RuleValue,
    StrategyRun,
    UnverifiedRuleError,
    VerificationStatus,
    compare_strategy_across_firms,
    unknown,
    user_supplied,
    verified,
)

UTC = timezone.utc


def topstep(size=50_000):
    return REGISTRY.get(f"topstep/trading_combine/{size}@v2026.06-unverified")


def apex(size=50_000):
    return REGISTRY.get(f"apex/eod_pa/{size}@v2026.06-unverified")


def mffu():
    return REGISTRY.get("mffu/rapid/0@v2026.06-unverified")


def alpha():
    return REGISTRY.get("alpha_futures/research_comparison_only/0@v2026.06-unverified")


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


def test_no_profile_in_this_build_is_adjudication_ready():
    """Firm documentation was unreachable, so nothing may adjudicate."""
    assert len(REGISTRY) > 0
    assert REGISTRY.adjudication_ready() == []


def test_every_profile_declares_its_unverified_status():
    for profile in REGISTRY.all():
        assert profile.verification_status is VerificationStatus.USER_SUPPLIED
        assert profile.retrieved_at is None
        assert "UNREACHABLE" in profile.source_url


# =========================================================================
# Topstep
# =========================================================================


@pytest.mark.parametrize(
    ("size", "target", "minis", "micros"),
    [(50_000, 3_000, 5, 50), (100_000, 6_000, 10, 100), (150_000, 9_000, 15, 150)],
)
def test_topstep_account_sizes_are_registered(size, target, minis, micros):
    profile = topstep(size)
    assert profile is not None
    assert profile.initial_balance.get() == size
    assert profile.profit_target.get() == target
    assert profile.position_limits.max_minis.get() == minis
    assert profile.position_limits.max_micros.get() == micros


def test_topstep_mll_is_its_own_rule_type_not_the_ftmo_defaults():
    """The Combine's hard rule is the MLL, not 5% daily / 10% total."""
    mll = topstep().max_loss_limit
    assert isinstance(mll, MaxLossLimit)
    for field in ("drawdown_type", "threshold", "calculation_method", "timing", "basis"):
        assert getattr(mll, field).is_unknown, f"{field} must not be guessed"


def test_topstep_mll_refuses_adjudication_while_unresolved():
    with pytest.raises(UnverifiedRuleError, match="calculation method decides"):
        topstep().max_loss_limit.require_for_adjudication()


def test_topstep_mll_unresolved_list_is_reported():
    unresolved = topstep().max_loss_limit.unresolved
    assert "mll_threshold" in unresolved
    assert "mll_calculation_method" in unresolved


def test_topstep_consistency_computes_the_ratio():
    result = topstep().consistency.evaluate(best_day_profit=1_000.0, total_profit=3_000.0)
    assert result["best_day_percentage"] == pytest.approx(1 / 3)


def test_topstep_consistency_withholds_the_decision_while_unverified():
    """A consistency call made against a guessed percentage is worse than none."""
    result = topstep().consistency.evaluate(2_000.0, 3_000.0)
    assert result["passes"] is None
    assert not result["threshold_verified"]


def test_consistency_decides_once_the_threshold_is_verified():
    rule = ConsistencyRule(
        max_best_day_fraction=verified(0.50, "https://example.invalid",
                                       datetime.now(UTC), label="f"),
        applies_to=user_supplied("evaluation", label="a"),
    )
    assert rule.evaluate(1_000.0, 3_000.0)["passes"] is True     # 33% < 50%
    assert rule.evaluate(2_000.0, 3_000.0)["passes"] is False    # 67% >= 50%


def test_consistency_handles_no_profit():
    assert topstep().consistency.evaluate(0.0, 0.0)["best_day_percentage"] is None


def test_topstep_minimum_trading_days_is_two():
    assert topstep().min_trading_days.get() == 2


def test_topstep_trading_day_boundary_is_recorded_in_ct():
    profile = topstep()
    assert profile.trading_day_start.get() == time(17, 0)
    assert profile.trading_day_end.get() == time(15, 10)
    assert profile.timezone == "America/Chicago"


def test_topstep_flat_rule_recorded():
    profile = topstep()
    assert profile.forced_flat_time.get() == time(15, 10)
    assert profile.session_reopen.get() == time(17, 0)
    assert profile.overnight_allowed.get() is False


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
    limits = PositionLimits(max_minis=unknown("max_minis"), max_micros=unknown("max_micros"))
    assert limits.within_limit(minis=3) is None


def test_topstep_automation_policy_and_prohibited_practices():
    automation = topstep().automation
    assert automation.stance.get() is AutomationStance.ALLOWED
    assert automation.api_available.get() is True
    assert automation.api_provider.get() == "TopstepX/ProjectX"
    for practice in (ProhibitedPractice.SPOOFING,
                     ProhibitedPractice.STALE_FEED_EXPLOITATION,
                     ProhibitedPractice.CROSS_ACCOUNT_HEDGING,
                     ProhibitedPractice.MAX_SIZE_INTO_NEWS):
        assert practice in automation.prohibited_practices


def test_topstep_is_the_primary_automation_target():
    assert PRIMARY_AUTOMATION_TARGET == "topstep"


# =========================================================================
# Topology and compliance
# =========================================================================


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


def test_compliance_gate_blocks_on_unverified_rules_even_when_local():
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
# Apex
# =========================================================================


@pytest.mark.parametrize(
    ("size", "drawdown", "contracts"),
    [(25_000, 1_000, 2), (50_000, 2_000, 4), (100_000, 3_000, 6), (150_000, 4_000, 10)],
)
def test_apex_tiers_are_registered(size, drawdown, contracts):
    profile = apex(size)
    assert profile is not None
    assert profile.max_loss_limit.threshold.get() == drawdown
    assert profile.position_limits.max_minis.get() == contracts


def test_apex_drawdown_is_end_of_day_not_intraday_trailing():
    mll = apex().max_loss_limit
    assert mll.timing.get() is DrawdownTiming.END_OF_DAY
    assert "intraday trailing drawdown does not apply" in mll.calculation_method.get()


def test_apex_daily_loss_limit_is_deliberately_unresolved():
    """The operator explicitly instructed not to invent Apex DLL values."""
    rule = apex().daily_loss_limit
    assert rule.is_unknown
    assert "NOT to invent" in rule.source.note


def test_apex_profit_split_recorded():
    assert apex().profit_split.get() == pytest.approx(1.00)


def test_apex_scaling_is_tier_based():
    tiers = REGISTRY.by_firm("apex")
    sizes = [p.account_size for p in tiers]
    contracts = [p.position_limits.max_minis.get() for p in tiers]
    assert sizes == sorted(sizes)
    assert contracts == sorted(contracts)      # limits scale with account size


# =========================================================================
# MFFU
# =========================================================================


def test_mffu_rapid_metadata():
    profile = mffu()
    assert profile.profit_split.get() == pytest.approx(0.90)
    assert profile.payout_cadence.get() == "daily"
    assert profile.activation_fee.get() == 0
    assert profile.min_trading_days.get() == 2


def test_mffu_has_no_daily_loss_limit():
    assert mffu().daily_loss_limit.get() == 0     # 0 encodes "none"


def test_mffu_drawdown_is_end_of_day():
    assert mffu().max_loss_limit.timing.get() is DrawdownTiming.END_OF_DAY


def test_mffu_consistency_is_fifty_percent():
    assert mffu().consistency.max_best_day_fraction.get() == pytest.approx(0.50)


def test_mffu_account_size_specifics_are_unresolved():
    """Not assumed, per instruction -- size-specific values were not verified."""
    profile = mffu()
    assert profile.initial_balance.is_unknown
    assert profile.max_loss_limit.threshold.is_unknown
    assert profile.position_limits.max_minis.is_unknown


# =========================================================================
# Alpha Futures
# =========================================================================


def test_alpha_prohibits_full_automation():
    automation = alpha().automation
    assert automation.stance.get() is AutomationStance.SEMI_ONLY
    assert automation.permits_full_automation is False


def test_alpha_semi_automation_is_recorded_as_allowed():
    assert "semi-automation allowed" in alpha().automation.stance.source.note


def test_alpha_is_not_a_live_automation_target():
    assert "RESEARCH COMPARISON ONLY" in alpha().notes
    assert PRIMARY_AUTOMATION_TARGET != "alpha_futures"


def test_automated_run_fails_alpha_compatibility():
    outcome = compare_strategy_across_firms(a_run(is_automated=True), [alpha()])
    result = next(iter(outcome.values()))
    assert result.automation_compatible is False
    assert any("automation stance" in r for r in result.failure_reasons)


def test_manual_run_is_compatible_with_alpha():
    outcome = compare_strategy_across_firms(a_run(is_automated=False), [alpha()])
    assert next(iter(outcome.values())).automation_compatible is True


# =========================================================================
# Registry versioning
# =========================================================================


def test_published_rulesets_are_immutable():
    registry = PropFirmRegistry()
    profile = topstep()
    registry.register(profile)
    registry.register(profile)                      # identical is fine

    from dataclasses import replace
    changed = replace(profile, profit_target=user_supplied(9999, label="profit_target"))
    with pytest.raises(ValueError, match="immutable"):
        registry.register(changed)


def test_every_profile_carries_required_version_metadata():
    for profile in REGISTRY.all():
        payload = profile.to_dict()
        for field in ("firm_id", "program_id", "account_size", "ruleset_version",
                      "effective_from", "source_url", "retrieved_at",
                      "verification_status"):
            assert field in payload


def test_registry_lists_firms_separately():
    assert set(REGISTRY.firms()) == {"topstep", "apex", "mffu", "alpha_futures"}


# =========================================================================
# Comparison engine
# =========================================================================


def test_comparison_reports_each_firm_separately():
    profiles = [topstep(50_000), apex(50_000), mffu()]
    outcomes = compare_strategy_across_firms(a_run(), profiles)
    assert len(outcomes) == 3
    assert all(isinstance(v.firm_key, str) for v in outcomes.values())


def test_comparison_never_aggregates_into_one_score():
    outcomes = compare_strategy_across_firms(a_run(), [topstep(), apex()])
    assert not hasattr(outcomes, "overall_score")
    assert set(outcomes) == {topstep().key, apex().key}


def test_comparison_withholds_a_verdict_while_rules_are_unverified():
    outcome = compare_strategy_across_firms(a_run(), [topstep()])[topstep().key]
    assert outcome.passed is None
    assert not outcome.decidable
    assert outcome.undecidable_reasons


def test_comparison_still_reports_measurements():
    """Measurements are always available; only the verdict is withheld."""
    outcome = compare_strategy_across_firms(a_run(), [topstep()])[topstep().key]
    assert outcome.profit_target_distance is not None
    assert outcome.consistency["best_day_percentage"] is not None
    assert outcome.trading_day_result["trading_days"] == 6


def fully_verified_profile(threshold=2_000):
    """A profile with every rule officially verified, for the decidable path."""
    now = datetime.now(UTC)
    url = "https://example.invalid/official"

    def v(value, label):
        return verified(value, url, now, label=label)

    return FirmProfile(
        firm_id="testfirm", program_id="prog", account_size=50_000,
        ruleset_version="1", effective_from=date(2026, 1, 1), source_url=url,
        retrieved_at=date(2026, 1, 1),
        verification_status=VerificationStatus.VERIFIED_OFFICIAL,
        initial_balance=v(50_000, "initial_balance"),
        profit_target=v(3_000, "profit_target"),
        max_loss_limit=MaxLossLimit(
            drawdown_type=v("static", "mll_drawdown_type"),
            threshold=v(threshold, "mll_threshold"),
            calculation_method=v("static from initial balance", "mll_calculation_method"),
            timing=v(DrawdownTiming.END_OF_DAY, "mll_timing"),
            basis=v("balance", "mll_basis"),
            locks_at=v(0, "mll_locks_at"),
        ),
        position_limits=PositionLimits(
            max_minis=v(5, "max_minis"), max_micros=v(50, "max_micros"),
            micro_to_mini_ratio=v(10, "micro_to_mini_ratio"),
        ),
        automation=AutomationPolicy(
            stance=v(AutomationStance.ALLOWED, "automation_stance"),
            api_available=v(True, "api_available"),
            api_provider=v("x", "api_provider"),
            requires_local_execution=v(True, "requires_local_execution"),
            prohibits_vps=v(True, "prohibits_vps"),
        ),
        consistency=ConsistencyRule(
            max_best_day_fraction=v(0.50, "max_best_day_fraction"),
            applies_to=v("evaluation", "consistency_applies_to"),
            target_increase_effect=v("none", "target_increase_effect"),
        ),
        daily_loss_limit=v(1_000, "daily_loss_limit"),
        min_trading_days=v(2, "min_trading_days"),
        trading_day_start=v(time(17, 0), "trading_day_start"),
        trading_day_end=v(time(15, 10), "trading_day_end"),
        forced_flat_time=v(time(15, 10), "forced_flat_time"),
        session_reopen=v(time(17, 0), "session_reopen"),
        overnight_allowed=v(False, "overnight_allowed"),
        profit_split=v(0.9, "profit_split"),
        payout_cadence=v("daily", "payout_cadence"),
        activation_fee=v(0, "activation_fee"),
    )


def test_comparison_decides_when_every_rule_is_verified():
    profile = fully_verified_profile()
    assert profile.fully_verified

    outcome = compare_strategy_across_firms(a_run(), [profile])[profile.key]
    assert outcome.decidable
    assert outcome.passed is True
    assert not outcome.failure_reasons


def test_verified_profile_fails_a_run_that_breaches_drawdown():
    profile = fully_verified_profile(threshold=500)
    outcome = compare_strategy_across_firms(
        a_run(max_drawdown_currency=800.0), [profile]
    )[profile.key]
    assert outcome.decidable
    assert outcome.passed is False
    assert any("max drawdown" in r for r in outcome.failure_reasons)


def test_verified_profile_fails_a_run_breaching_consistency():
    profile = fully_verified_profile()
    outcome = compare_strategy_across_firms(
        a_run(best_day_profit=3_000.0, total_profit=3_500.0), [profile]
    )[profile.key]
    assert outcome.passed is False
    assert any("consistency" in r for r in outcome.failure_reasons)


def test_verified_profile_fails_a_run_exceeding_position_limits():
    profile = fully_verified_profile()
    outcome = compare_strategy_across_firms(
        a_run(max_position_minis=9), [profile]
    )[profile.key]
    assert outcome.passed is False
    assert outcome.position_limit_violations == 1


def test_verified_profile_fails_a_run_short_of_trading_days():
    profile = fully_verified_profile()
    outcome = compare_strategy_across_firms(a_run(trading_days=1), [profile])[profile.key]
    assert outcome.passed is False
    assert any("trading days" in r for r in outcome.failure_reasons)


def test_profile_refuses_adjudication_when_unverified():
    with pytest.raises(UnverifiedRuleError, match="unverified rule"):
        topstep().require_adjudication_ready()
