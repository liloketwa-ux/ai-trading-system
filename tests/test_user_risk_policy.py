"""User risk objectives, and their strict separation from firm requirements.

Two properties carry most of the weight. A daily target must never cause a
trade — being behind is not a reason to do anything. And a user ceiling must
never loosen a firm limit, only tighten within it.
"""

import pytest

from ai_trading.propfirm import REGISTRY, Stage
from ai_trading.risk import (
    DailyTargetMode,
    DailyTargetState,
    FeasibilityVerdict,
    RiskConstraint,
    RiskLayer,
    StrategyQualityTier,
    TargetReachedAction,
    UserPolicyError,
    UserRiskPolicy,
    assess_target_feasibility,
    compute_daily_metrics,
    resolve_risk,
)


def firm_constraint(limit_pct=0.8, name="firm_mll_capacity"):
    return RiskConstraint(RiskLayer.FIRM_HARD_LIMIT, name, limit_pct,
                          "remaining Maximum Loss Limit headroom")


# =========================================================================
# 1. The 10% daily target
# =========================================================================


def test_the_default_daily_target_is_ten_percent():
    assert UserRiskPolicy().daily_target_pct == 10.0


def test_the_default_mode_is_enforced_for_evaluation_sim():
    assert (UserRiskPolicy().daily_target_mode
            is DailyTargetMode.ENFORCED_FOR_EVALUATION_SIM)


@pytest.mark.parametrize(("equity", "expected"),
                         [(50_000, 5_000), (100_000, 10_000),
                          (150_000, 15_000), (25_000, 2_500)])
def test_the_target_scales_with_account_size(equity, expected):
    assert UserRiskPolicy().daily_target_amount(equity) == expected


def test_the_target_uses_starting_equity_not_current():
    """A target computed from a rising balance recedes as the day goes well."""
    policy = UserRiskPolicy()
    state = DailyTargetState(policy, starting_daily_equity=50_000,
                             realized_pnl=3_000)
    assert state.daily_target_amount == 5_000        # not 10% of 53,000


def test_a_non_positive_starting_equity_is_refused():
    with pytest.raises(UserPolicyError, match="must be positive"):
        UserRiskPolicy().daily_target_amount(0)


def test_all_three_target_modes_exist():
    assert {m.value for m in DailyTargetMode} == {
        "optional", "enforced_for_evaluation_sim", "inactive"}


def test_an_inactive_target_is_never_reached():
    policy = UserRiskPolicy(daily_target_mode=DailyTargetMode.INACTIVE)
    state = DailyTargetState(policy, 50_000, realized_pnl=99_000)
    assert not state.daily_target_reached


def test_an_optional_target_is_tracked_but_gates_nothing():
    policy = UserRiskPolicy(daily_target_mode=DailyTargetMode.OPTIONAL)
    state = DailyTargetState(policy, 50_000, realized_pnl=9_000)
    assert state.daily_target_reached
    allowed, reason = state.may_open_new_trade()
    assert allowed
    assert "gates nothing" in reason


# =========================================================================
# 2. The target never forces a trade
# =========================================================================


def test_risk_resolution_cannot_see_target_progress():
    """The structural guarantee: there is no argument to pass.

    A code path that raises risk because the day is behind target cannot be
    written against this API, rather than merely being discouraged.
    """
    import inspect

    parameters = set(inspect.signature(resolve_risk).parameters)
    assert parameters == {"constraints"}
    for forbidden in ("target", "progress", "pnl", "shortfall", "remaining"):
        assert not any(forbidden in name for name in parameters)


def test_being_behind_target_does_not_change_allowed_risk():
    policy = UserRiskPolicy()
    constraints = [firm_constraint(), policy.ceiling_constraint()]
    behind = resolve_risk(constraints)

    state = DailyTargetState(policy, 50_000, realized_pnl=-2_000)
    assert not state.daily_target_reached
    assert resolve_risk(constraints).allowed_pct == behind.allowed_pct


def test_being_behind_target_still_permits_no_trade():
    """NO TRADE is correct at any P&L when no setup exists."""
    state = DailyTargetState(UserRiskPolicy(), 50_000, realized_pnl=0.0,
                             trades_taken=0, no_valid_setup=True)
    allowed, reason = state.may_open_new_trade()
    assert allowed                      # permitted...
    assert "does not require it" in reason   # ...and not required
    assert state.no_valid_setup


def test_a_no_trade_day_with_an_unmet_target_is_not_an_error():
    state = DailyTargetState(UserRiskPolicy(), 50_000, no_valid_setup=True)
    payload = state.to_dict()
    assert payload["trades_taken"] == 0
    assert payload["daily_target_reached"] is False
    assert payload["no_valid_setup"] is True


def test_the_only_false_from_may_open_is_target_reached():
    """Never 'you must trade'; only 'you have done enough'."""
    policy = UserRiskPolicy()
    for pnl in (-5_000, 0, 1_000, 4_999):
        allowed, _ = DailyTargetState(policy, 50_000, realized_pnl=pnl).may_open_new_trade()
        assert allowed


# =========================================================================
# 3. Target reached
# =========================================================================


def test_reaching_the_target_is_recorded():
    state = DailyTargetState(UserRiskPolicy(), 50_000, realized_pnl=5_000)
    assert state.daily_target_reached
    assert state.status == "DAILY_TARGET_REACHED"


def test_unrealized_pnl_counts_toward_the_target():
    state = DailyTargetState(UserRiskPolicy(), 50_000, realized_pnl=3_000,
                             unrealized_pnl=2_100)
    assert state.daily_target_reached


def test_the_target_can_be_reached_intraday():
    state = DailyTargetState(UserRiskPolicy(), 50_000, realized_pnl=0.0,
                             unrealized_pnl=5_500)
    assert state.daily_target_reached
    allowed, _ = state.may_open_new_trade()
    assert not allowed


def test_the_default_action_is_stop_new_trades():
    assert UserRiskPolicy().on_target_reached is TargetReachedAction.STOP_NEW_TRADES


def test_stop_new_trades_blocks_further_positions():
    state = DailyTargetState(UserRiskPolicy(), 50_000, realized_pnl=5_100)
    allowed, reason = state.may_open_new_trade()
    assert not allowed
    assert "STOP_NEW_TRADES" in reason


def test_continue_trading_is_configurable_for_firm_requirements():
    """Some programs require continued activity; a user policy cannot override."""
    policy = UserRiskPolicy(on_target_reached=TargetReachedAction.CONTINUE_TRADING)
    allowed, reason = DailyTargetState(policy, 50_000,
                                       realized_pnl=6_000).may_open_new_trade()
    assert allowed
    assert "DAILY_TARGET_REACHED" in reason


def test_target_not_reached_leaves_trading_permitted():
    state = DailyTargetState(UserRiskPolicy(), 50_000, realized_pnl=4_000)
    assert not state.daily_target_reached
    assert state.may_open_new_trade()[0]


# =========================================================================
# 4-6. The 2% ceiling, and the default beneath it
# =========================================================================


def test_the_default_user_ceiling_is_two_percent():
    assert UserRiskPolicy().max_risk_per_trade_pct == 2.0


def test_the_default_working_risk_is_well_below_the_ceiling():
    """A system that defaults to its own ceiling has no headroom left."""
    policy = UserRiskPolicy()
    assert policy.baseline_risk_per_trade_pct < policy.max_risk_per_trade_pct
    assert policy.baseline_risk_per_trade_pct == 0.25


def test_the_two_fields_are_separate():
    policy = UserRiskPolicy()
    payload = policy.to_dict()
    assert payload["max_risk_per_trade_pct"] == 2.0
    assert payload["baseline_risk_per_trade_pct"] == 0.25


def test_a_baseline_above_the_ceiling_is_refused():
    with pytest.raises(UserPolicyError, match="the ceiling is a cap"):
        UserRiskPolicy(baseline_risk_per_trade_pct=3.0)


def test_the_ceiling_binds_when_it_is_the_lowest():
    resolved = resolve_risk([
        UserRiskPolicy().ceiling_constraint(),
        RiskConstraint(RiskLayer.STRATEGY_BUDGET, "strategy", 5.0),
    ])
    assert resolved.allowed_pct == 2.0
    assert resolved.binding_layer is RiskLayer.USER_MAX_RISK


# =========================================================================
# 5. Risk hierarchy
# =========================================================================


def test_a_lower_firm_limit_overrides_the_user_ceiling():
    """The governing rule: 2% never loosens an actual firm limit."""
    resolved = resolve_risk([
        firm_constraint(0.5),
        UserRiskPolicy().ceiling_constraint(),
    ])
    assert resolved.allowed_pct == 0.5
    assert resolved.binding_layer is RiskLayer.FIRM_HARD_LIMIT


def test_a_lower_strategy_limit_overrides_the_user_ceiling():
    resolved = resolve_risk([
        UserRiskPolicy().ceiling_constraint(),
        RiskConstraint(RiskLayer.STRATEGY_BUDGET, "strategy_budget", 0.3),
    ])
    assert resolved.allowed_pct == 0.3
    assert resolved.binding_layer is RiskLayer.STRATEGY_BUDGET


def test_the_minimum_wins_across_every_layer():
    constraints = [
        firm_constraint(1.5),
        RiskConstraint(RiskLayer.SYSTEM_RISK_LIMIT, "system", 1.2),
        UserRiskPolicy().ceiling_constraint(),
        RiskConstraint(RiskLayer.STRATEGY_BUDGET, "strategy", 0.9),
        RiskConstraint(RiskLayer.TRADE_RISK, "volatility", 0.4),
    ]
    resolved = resolve_risk(constraints)
    assert resolved.allowed_pct == 0.4
    assert resolved.allowed_pct <= min(c.limit_pct for c in constraints)


def test_no_layer_can_raise_the_result_above_a_higher_layer():
    firm = firm_constraint(0.6)
    resolved = resolve_risk([
        firm,
        RiskConstraint(RiskLayer.STRATEGY_BUDGET, "greedy_strategy", 99.0),
        RiskConstraint(RiskLayer.TRADE_RISK, "greedy_trade", 99.0),
    ])
    assert resolved.allowed_pct == 0.6


def test_ties_are_attributed_to_the_higher_authority():
    resolved = resolve_risk([
        UserRiskPolicy().ceiling_constraint(),
        firm_constraint(2.0),
    ])
    assert resolved.binding_layer is RiskLayer.FIRM_HARD_LIMIT


def test_a_zero_firm_capacity_blocks_the_trade_entirely():
    resolved = resolve_risk([firm_constraint(0.0, "mll_exhausted"),
                             UserRiskPolicy().ceiling_constraint()])
    assert resolved.is_zero


def test_resolution_needs_at_least_one_constraint():
    with pytest.raises(UserPolicyError, match="missing limit"):
        resolve_risk([])


def test_layers_are_ordered_by_authority():
    order = [RiskLayer.FIRM_HARD_LIMIT, RiskLayer.SYSTEM_RISK_LIMIT,
             RiskLayer.USER_MAX_RISK, RiskLayer.STRATEGY_BUDGET,
             RiskLayer.TRADE_RISK]
    assert [layer.authority for layer in order] == [0, 1, 2, 3, 4]


def test_the_explanation_names_the_binding_constraint():
    resolved = resolve_risk([firm_constraint(0.5), UserRiskPolicy().ceiling_constraint()])
    assert "firm_mll_capacity" in resolved.explain()
    assert "firm_hard_limit" in resolved.explain()


# =========================================================================
# 7. Strategy quality tiers
# =========================================================================


def test_the_five_tiers_exist_and_are_ordered():
    assert [t.rank for t in StrategyQualityTier] == sorted(
        t.rank for t in StrategyQualityTier)
    assert len(list(StrategyQualityTier)) == 5


def test_failing_tiers_receive_no_live_risk():
    for tier in (StrategyQualityTier.OUT_OF_SAMPLE_FAILURE,
                 StrategyQualityTier.INSUFFICIENT_SAMPLE):
        assert not tier.permits_live_risk
        assert tier.budget_pct(2.0) == 0.0


def test_a_promising_strategy_is_paper_only():
    tier = StrategyQualityTier.PROMISING
    assert tier.permits_paper_only
    assert not tier.permits_live_risk
    assert tier.budget_pct(2.0) == 0.0


def test_surviving_robustness_earns_small_controlled_risk():
    tier = StrategyQualityTier.SURVIVES_ROBUSTNESS
    assert tier.permits_live_risk
    assert 0 < tier.budget_pct(2.0) < 2.0


def test_a_robust_candidate_may_use_the_full_ceiling():
    assert StrategyQualityTier.ROBUST_CANDIDATE.budget_pct(2.0) == 2.0


def test_tier_budgets_are_fractions_of_the_user_ceiling():
    """Not arbitrary percentages: they inherit the user's own justification."""
    tier = StrategyQualityTier.SURVIVES_ROBUSTNESS
    assert tier.budget_pct(1.0) == tier.budget_pct(2.0) / 2.0


def test_a_failing_tier_zeroes_the_resolved_risk():
    resolved = resolve_risk([
        firm_constraint(1.0),
        UserRiskPolicy().ceiling_constraint(),
        RiskConstraint(RiskLayer.STRATEGY_BUDGET, "tier",
                       StrategyQualityTier.OUT_OF_SAMPLE_FAILURE.budget_pct(2.0)),
    ])
    assert resolved.is_zero


# =========================================================================
# 8. Daily target tracking
# =========================================================================


def test_every_tracking_field_is_reported():
    state = DailyTargetState(UserRiskPolicy(), 50_000, realized_pnl=2_000)
    payload = state.to_dict()
    for name in ("daily_target_amount", "daily_target_progress",
                 "daily_target_progress_pct", "daily_target_remaining",
                 "daily_target_reached"):
        assert name in payload


def test_progress_percentage_is_against_the_target_not_equity():
    state = DailyTargetState(UserRiskPolicy(), 50_000, realized_pnl=2_500)
    assert state.daily_target_progress_pct == pytest.approx(50.0)
    assert state.return_pct == pytest.approx(5.0)


def test_remaining_never_goes_negative():
    state = DailyTargetState(UserRiskPolicy(), 50_000, realized_pnl=9_000)
    assert state.daily_target_remaining == 0.0


# =========================================================================
# 9. Separation from firm rules
# =========================================================================


def test_the_user_policy_is_labelled_as_a_user_policy():
    assert UserRiskPolicy().to_dict()["kind"] == "user_policy"
    assert UserRiskPolicy().is_user_policy


def test_the_user_policy_carries_no_firm_fields():
    """Never merged: five distinct concepts, five distinct homes."""
    payload = UserRiskPolicy().to_dict()
    for firm_field in ("profit_target", "daily_loss_limit", "mll_threshold",
                       "max_loss_limit", "firm_id"):
        assert firm_field not in payload


def test_the_firm_profile_carries_no_user_fields():
    profile = REGISTRY.resolve("topstep", "trading_combine", Stage.EVALUATION,
                               50_000)
    assert "daily_target_pct" not in profile.all_rules
    assert "max_risk_per_trade_pct" not in profile.all_rules


def test_the_user_target_and_the_firm_target_are_different_numbers():
    """10% of 50,000 daily is not the firm's 3,000 evaluation objective."""
    profile = REGISTRY.resolve("topstep", "trading_combine", Stage.EVALUATION,
                               50_000)
    firm_target = profile.profit_target.require()
    user_daily = UserRiskPolicy().daily_target_amount(50_000)
    assert firm_target == 3_000
    assert user_daily == 5_000
    assert firm_target != user_daily


def test_a_user_ceiling_cannot_be_expressed_as_a_firm_constraint():
    """Constraints carry their layer, so provenance cannot be laundered."""
    ceiling = UserRiskPolicy().ceiling_constraint()
    assert ceiling.layer is RiskLayer.USER_MAX_RISK
    assert "never loosens" in ceiling.reason


# =========================================================================
# 11. Feasibility diagnostic
# =========================================================================


def test_an_unreachable_target_is_flagged():
    """10% daily against a 2.1% p95 is informational, not a licence to size up."""
    returns = [0.4, 0.9, -0.6, 1.2, 2.1, -1.1, 0.3] * 8
    result = assess_target_feasibility(UserRiskPolicy(), returns)
    assert result.verdict is FeasibilityVerdict.TARGET_MAY_BE_INFEASIBLE
    assert "do not increase position size" in result.note


def test_a_reachable_target_is_not_flagged():
    policy = UserRiskPolicy(daily_target_pct=1.0)
    returns = [0.4, 0.9, -0.6, 1.2, 2.1, -1.1, 0.3] * 8
    result = assess_target_feasibility(policy, returns)
    assert result.verdict is FeasibilityVerdict.TARGET_PLAUSIBLE


def test_feasibility_needs_enough_history():
    result = assess_target_feasibility(UserRiskPolicy(), [1.0, 2.0, 3.0])
    assert result.verdict is FeasibilityVerdict.INSUFFICIENT_HISTORY


def test_the_feasibility_warning_carries_no_sizing_instruction():
    returns = [0.4, 0.9, -0.6, 1.2, 2.1, -1.1, 0.3] * 8
    payload = assess_target_feasibility(UserRiskPolicy(), returns).to_dict()
    assert "recommended_risk" not in payload
    assert "suggested_size" not in payload


# =========================================================================
# 10. Research metrics
# =========================================================================


def test_daily_metrics_report_more_than_target_hit_rate():
    returns = [1.2, -0.8, 11.0, 0.3, -2.4, 0.9, 12.5, -1.1, 0.2, 0.6]
    metrics = compute_daily_metrics(returns, target_pct=10.0,
                                    days_with_no_trade=3,
                                    days_with_overtrade_attempts=0)
    payload = metrics.to_dict()
    for name in ("percentage_of_days_target_reached", "median_daily_return_pct",
                 "mean_daily_return_pct", "daily_target_hit_rate",
                 "days_with_no_trade", "days_with_overtrade_attempts",
                 "maximum_daily_return_pct", "maximum_daily_loss_pct",
                 "expectancy_pct", "max_drawdown_pct",
                 "daily_return_volatility_pct", "longest_losing_streak",
                 "tail_loss_p95_pct"):
        assert name in payload


def test_the_target_hit_rate_is_computed_correctly():
    returns = [11.0, 12.5, 1.0, 2.0, -3.0]
    metrics = compute_daily_metrics(returns, target_pct=10.0)
    assert metrics.percentage_of_days_target_reached == pytest.approx(40.0)
    assert metrics.daily_target_hit_rate == pytest.approx(0.4)


def test_losing_streaks_are_measured():
    metrics = compute_daily_metrics([-1.0, -2.0, -0.5, 3.0, -1.0],
                                    target_pct=10.0)
    assert metrics.longest_losing_streak == 3


def test_no_trade_days_are_reported_separately():
    metrics = compute_daily_metrics([0.0] * 10, target_pct=10.0,
                                    days_with_no_trade=4)
    assert metrics.days_with_no_trade == 4
    assert metrics.percentage_of_days_target_reached == 0.0


def test_metrics_need_data():
    with pytest.raises(UserPolicyError, match="no daily returns"):
        compute_daily_metrics([], target_pct=10.0)
