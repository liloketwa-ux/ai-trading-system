"""Phase 7: walk-forward validation, purge/embargo, robustness, verdicts.

The contamination tests are the point. Each deliberately tries to leak test-period
information into training and asserts the machinery refuses it.
"""

from datetime import datetime, timedelta, timezone

import pytest

from ai_trading.validation import (
    COST_MULTIPLIERS,
    DELAY_BARS,
    AdjustmentMethod,
    Candidate,
    CandidateLockError,
    CandidateRegistry,
    CandidateReport,
    ComponentStatus,
    ContinuityError,
    ContractSeries,
    EconomicConfidenceError,
    FundingAccrual,
    InstrumentReport,
    PerturbationAxis,
    PerturbationPoint,
    PnLBreakdown,
    RobustnessCriteria,
    RobustnessMatrix,
    RollEvent,
    RollMethod,
    RollPolicy,
    SensitivityCurve,
    Verdict,
    WalkForwardConfig,
    Window,
    WindowResult,
    breakeven_multiplier,
    generate_windows,
    grade,
    is_contaminated,
    purge_and_embargo,
    run_trade_removal,
)

UTC = timezone.utc
T0 = datetime(2024, 1, 1, tzinfo=UTC)
DAY = timedelta(days=1)
HOUR = timedelta(hours=1)


def wf_config(**kw):
    defaults = dict(train=timedelta(days=60), validation=timedelta(days=20),
                    test=timedelta(days=20), step=timedelta(days=20),
                    label_horizon=timedelta(hours=4), embargo=timedelta(days=1))
    return WalkForwardConfig(**{**defaults, **kw})


def a_window(**kw):
    defaults = dict(
        index=0, train_start=T0, train_end=T0 + 60 * DAY,
        validation_start=T0 + 60 * DAY, validation_end=T0 + 80 * DAY,
        test_start=T0 + 80 * DAY, test_end=T0 + 100 * DAY,
        label_horizon=timedelta(hours=4), embargo=timedelta(days=1),
    )
    return Window(**{**defaults, **kw})


# -- rolling windows -------------------------------------------------------


def test_windows_roll_forward_by_step():
    windows = generate_windows(wf_config(), T0, T0 + 365 * DAY)
    assert len(windows) > 5
    for previous, current in zip(windows, windows[1:]):
        assert current.train_start == previous.train_start + timedelta(days=20)


def test_windows_are_ordered_train_validation_test():
    for window in generate_windows(wf_config(), T0, T0 + 365 * DAY):
        assert window.train_end <= window.validation_start
        assert window.validation_end <= window.test_start
        assert window.test_start < window.test_end


def test_no_truncated_final_window():
    """A short final fold would be graded on less data than the others."""
    windows = generate_windows(wf_config(), T0, T0 + 200 * DAY)
    for window in windows:
        assert window.test_end <= T0 + 200 * DAY
        assert window.test_end - window.test_start == timedelta(days=20)


def test_window_records_full_lineage():
    windows = generate_windows(
        wf_config(), T0, T0 + 365 * DAY,
        dataset_version="d1", hypothesis_version="ICT-001",
        feature_versions={"atr": "1"}, execution_model_version="1",
        cost_model_version="1",
    )
    payload = windows[0].to_dict()
    for field in ("dataset_version", "hypothesis_version", "feature_versions",
                  "execution_model_version", "cost_model_version", "train",
                  "validation", "test", "embargo_s", "label_horizon_s"):
        assert field in payload


def test_overlapping_windows_rejected():
    with pytest.raises(ValueError, match="validation overlaps train"):
        a_window(validation_start=T0 + 30 * DAY)


def test_empty_window_rejected():
    with pytest.raises(ValueError, match="empty test range"):
        a_window(test_start=T0 + 100 * DAY, test_end=T0 + 100 * DAY)


@pytest.mark.parametrize("bad", [{"train": timedelta(0)}, {"step": timedelta(-1)}])
def test_invalid_walk_forward_config_rejected(bad):
    with pytest.raises(ValueError):
        wf_config(**bad)


# -- purge -----------------------------------------------------------------


def test_purge_removes_labels_reaching_into_the_test_period():
    """ATTACK: a training label still resolving inside the test window."""
    window = a_window(
        validation_start=T0 + 60 * DAY, validation_end=T0 + 60 * DAY + HOUR,
        test_start=T0 + 60 * DAY + HOUR, test_end=T0 + 80 * DAY,
        label_horizon=timedelta(hours=4), embargo=timedelta(0),
    )
    # Observations in the last 4 hours of training have labels reaching past test_start.
    times = [window.train_end - timedelta(hours=h) for h in range(1, 8)]
    kept, report = purge_and_embargo(times, window)

    assert report.purged_label_overlap > 0
    for moment in kept:
        assert moment + window.label_horizon <= window.test_start


def test_purge_keeps_observations_whose_labels_resolve_in_time():
    window = a_window(embargo=timedelta(0))
    safe = [T0 + timedelta(days=d) for d in range(1, 50)]
    kept, report = purge_and_embargo(safe, window)
    assert report.kept == len(safe)
    assert report.purged_label_overlap == 0


def test_purge_reports_what_it_removed():
    window = a_window(embargo=timedelta(0))
    times = [T0 - DAY, T0 + DAY, T0 + 200 * DAY]
    _, report = purge_and_embargo(times, window)
    assert report.submitted == 3
    assert report.purged_out_of_range == 2      # before train_start and after train_end
    assert 0.0 <= report.removed_fraction <= 1.0


def test_longer_label_horizon_purges_more():
    short = a_window(
        validation_end=T0 + 60 * DAY + HOUR, test_start=T0 + 60 * DAY + HOUR,
        validation_start=T0 + 60 * DAY, label_horizon=timedelta(hours=1),
        embargo=timedelta(0),
    )
    long = a_window(
        validation_end=T0 + 60 * DAY + HOUR, test_start=T0 + 60 * DAY + HOUR,
        validation_start=T0 + 60 * DAY, label_horizon=timedelta(hours=12),
        embargo=timedelta(0),
    )
    times = [long.train_end - timedelta(hours=h) for h in range(1, 24)]
    assert purge_and_embargo(times, long)[1].purged_label_overlap > \
        purge_and_embargo(times, short)[1].purged_label_overlap


# -- embargo ---------------------------------------------------------------


def test_embargo_is_actually_enforced():
    """ATTACK: an observation just inside the embargo gap."""
    window = a_window(
        validation_start=T0 + 60 * DAY, validation_end=T0 + 60 * DAY,
        test_start=T0 + 60 * DAY, test_end=T0 + 80 * DAY,
        label_horizon=timedelta(0), embargo=timedelta(days=2),
    )
    inside_gap = window.test_start - timedelta(days=1)
    outside_gap = window.test_start - timedelta(days=5)

    kept, report = purge_and_embargo([inside_gap, outside_gap], window)
    assert inside_gap not in kept
    assert outside_gap in kept
    assert report.purged_embargo == 1


def test_zero_embargo_keeps_the_boundary_observation():
    window = a_window(
        validation_start=T0 + 60 * DAY, validation_end=T0 + 60 * DAY,
        test_start=T0 + 60 * DAY, test_end=T0 + 80 * DAY,
        label_horizon=timedelta(0), embargo=timedelta(0),
    )
    boundary = window.test_start - timedelta(minutes=1)
    kept, _ = purge_and_embargo([boundary], window)
    assert boundary in kept


def test_larger_embargo_removes_more():
    def purged(days):
        window = a_window(
            validation_start=T0 + 60 * DAY, validation_end=T0 + 60 * DAY,
            test_start=T0 + 60 * DAY, test_end=T0 + 80 * DAY,
            label_horizon=timedelta(0), embargo=timedelta(days=days),
        )
        times = [window.test_start - timedelta(days=d) for d in range(1, 12)]
        return purge_and_embargo(times, window)[1].purged_embargo

    assert purged(10) > purged(2) > purged(0)


def test_purge_cutoff_combines_horizon_and_embargo():
    window = a_window(label_horizon=timedelta(hours=4), embargo=timedelta(days=1))
    assert window.purge_cutoff == window.test_start - timedelta(hours=4) - timedelta(days=1)


def test_is_contaminated_matches_the_purge_filter():
    window = a_window(
        validation_start=T0 + 60 * DAY, validation_end=T0 + 60 * DAY,
        test_start=T0 + 60 * DAY, test_end=T0 + 80 * DAY,
        label_horizon=timedelta(hours=4), embargo=timedelta(days=1),
    )
    times = [window.train_end - timedelta(hours=h) for h in range(0, 60, 6)]
    kept, _ = purge_and_embargo(times, window)
    for moment in times:
        if window.train_start <= moment < window.train_end:
            assert is_contaminated(moment, window) == (moment not in kept)


# -- candidate immutability ------------------------------------------------


def a_candidate(**kw):
    defaults = dict(
        candidate_id="CAND-001", hypothesis_id="ICT-001", research_version="r1",
        feature_definitions={"atr": "1"}, thresholds={"displacement_atr": 1.25},
        label_key="forward_return_1h:v1", execution_model_version="1",
        cost_model_version="1", dataset_version="d1",
    )
    return Candidate(**{**defaults, **kw})


def test_candidate_fingerprint_is_content_addressed():
    assert a_candidate().fingerprint == a_candidate().fingerprint
    assert a_candidate(thresholds={"displacement_atr": 1.5}).fingerprint != \
        a_candidate().fingerprint


def test_registry_rejects_silent_retuning():
    """ATTACK: change a threshold mid walk-forward under the same id."""
    registry = CandidateRegistry()
    registry.register(a_candidate())
    with pytest.raises(CandidateLockError, match="silently retuning"):
        registry.register(a_candidate(thresholds={"displacement_atr": 2.0}))


def test_reregistering_identical_candidate_is_allowed():
    registry = CandidateRegistry()
    registry.register(a_candidate())
    registry.register(a_candidate())
    assert len(registry) == 1


def test_retuning_requires_a_new_candidate_id():
    with pytest.raises(CandidateLockError, match="new candidate_id"):
        a_candidate().retuned(thresholds={"displacement_atr": 2.0})


def test_retuned_candidate_is_a_new_object_with_a_new_fingerprint():
    original = a_candidate()
    retuned = original.retuned(candidate_id="CAND-002",
                               thresholds={"displacement_atr": 2.0})
    assert retuned.candidate_id == "CAND-002"
    assert retuned.fingerprint != original.fingerprint
    assert original.thresholds == {"displacement_atr": 1.25}   # untouched


def test_candidate_lineage_is_complete():
    lineage = a_candidate().lineage()
    for field in ("dataset_version", "feature_versions", "label_version",
                  "backtest_version", "execution_model_version",
                  "cost_model_version", "random_seed", "code_commit",
                  "protocol_version", "fingerprint"):
        assert field in lineage


def test_candidate_requires_a_fixed_label():
    with pytest.raises(ValueError, match="fixed label"):
        a_candidate(label_key="")


# -- robustness ------------------------------------------------------------


def curve(axis, values):
    return SensitivityCurve(axis, [
        PerturbationPoint(axis, magnitude, expectancy, expectancy / 100, 200, 0.1)
        for magnitude, expectancy in values
    ])


def test_sensitivity_curve_finds_the_break_point():
    c = curve(PerturbationAxis.COST, [(1.0, 50.0), (1.25, 20.0), (1.5, -5.0), (2.0, -40.0)])
    assert c.breaks_at == 1.5
    assert not c.survives_all


def test_curve_surviving_all_multipliers():
    c = curve(PerturbationAxis.COST, [(m, 50.0) for m in COST_MULTIPLIERS])
    assert c.survives_all
    assert c.breaks_at is None


def test_breakeven_multiplier_interpolates():
    c = curve(PerturbationAxis.COST, [(1.0, 10.0), (2.0, -10.0)])
    assert breakeven_multiplier(c) == pytest.approx(1.5)


def test_degradation_is_reported():
    c = curve(PerturbationAxis.COST, [(1.0, 100.0), (3.0, 25.0)])
    assert c.degradation() == pytest.approx(-0.75)


def test_delay_axis_is_expressed_in_bars():
    """Not milliseconds -- bar data cannot support that precision."""
    assert DELAY_BARS == (0, 1, 2, 3)
    c = curve(PerturbationAxis.DELAY_BARS, [(0, 30.0), (1, 5.0), (2, -10.0)])
    assert c.breaks_at == 2


def test_trade_removal_flags_outlier_dependence():
    """One huge winner carrying a pile of small losers."""
    pnls = [500.0] + [-8.0] * 40
    results = {r.label: r for r in run_trade_removal(pnls)}
    assert results["remove_best_1"].expectancy < 0
    assert not results["remove_best_1"].survives


def test_trade_removal_leaves_a_broad_edge_intact():
    pnls = [12.0] * 30 + [-8.0] * 20
    results = {r.label: r for r in run_trade_removal(pnls)}
    assert results["remove_best_1"].survives
    assert abs(results["remove_best_1"].relative_change) < 0.2


def test_trade_removal_covers_the_required_variants():
    labels = {r.label for r in run_trade_removal([float(i) for i in range(-30, 30)])}
    assert {"remove_best_1", "remove_best_5", "remove_best_10",
            "remove_worst_1", "remove_worst_5", "remove_top_5pct_wins"} <= labels


def test_trade_removal_on_empty_input():
    assert run_trade_removal([]) == []


def test_matrix_reports_outlier_dependence():
    matrix = RobustnessMatrix("CAND-001", "ES",
                              trade_removal=run_trade_removal([500.0] + [-8.0] * 40))
    assert matrix.outlier_dependent


# -- contract rolls --------------------------------------------------------


def test_default_policy_refuses_continuous_history_claims():
    """Where adjustment is not implemented, the claim fails closed."""
    policy = RollPolicy()
    assert not policy.supports_continuous_history
    with pytest.raises(ContinuityError, match="must not be described as continuous"):
        policy.assert_continuous_claim()


def test_adjusted_policy_permits_the_claim():
    policy = RollPolicy(method=RollMethod.VOLUME,
                        adjustment=AdjustmentMethod.BACK_ADJUSTED)
    assert policy.supports_continuous_history
    policy.assert_continuous_claim()


def test_cannot_adjust_an_unrolled_series():
    with pytest.raises(ValueError, match="not being rolled"):
        RollPolicy(method=RollMethod.NONE, adjustment=AdjustmentMethod.BACK_ADJUSTED)


def test_roll_event_reports_gap_and_ratio():
    event = RollEvent(T0, "ESH24", "ESM24", 4800.0, 4830.0)
    assert event.gap == pytest.approx(30.0)
    assert event.ratio == pytest.approx(4830 / 4800)


def test_single_contract_series_is_labelled_as_such():
    series = ContractSeries("ES", RollPolicy(), contract_versions=["ESH24"])
    assert series.is_single_contract
    assert "no stitching" in series.describe()


def test_multi_contract_series_records_the_policy():
    series = ContractSeries(
        "ES", RollPolicy(RollMethod.CALENDAR, AdjustmentMethod.BACK_ADJUSTED),
        contract_versions=["ESH24", "ESM24"],
        rolls=[RollEvent(T0, "ESH24", "ESM24", 4800.0, 4830.0)],
    )
    payload = series.to_dict()
    assert payload["policy"]["roll_method"] == "calendar"
    assert payload["policy"]["adjustment_method"] == "back_adjusted"
    assert len(payload["rolls"]) == 1


# -- funding ---------------------------------------------------------------


def test_net_refused_while_funding_is_unavailable():
    """A net figure missing funding flatters the strategy."""
    breakdown = PnLBreakdown(price_pnl=1000.0, trading_fees=50.0,
                             funding_status=ComponentStatus.UNAVAILABLE)
    assert not breakdown.economically_confident
    with pytest.raises(EconomicConfidenceError, match="funding"):
        breakdown.net()


def test_net_allowed_once_funding_is_measured():
    breakdown = PnLBreakdown(price_pnl=1000.0, trading_fees=50.0,
                             funding_status=ComponentStatus.UNAVAILABLE)
    breakdown.add_funding(FundingAccrual(T0, 0.0001, 100_000.0, 1))
    assert breakdown.economically_confident
    assert breakdown.net() == pytest.approx(1000.0 - 50.0 - 10.0)


def test_funding_sign_follows_position_direction():
    long_pays = FundingAccrual(T0, 0.0001, 100_000.0, 1)
    short_receives = FundingAccrual(T0, 0.0001, 100_000.0, -1)
    assert long_pays.amount < 0
    assert short_receives.amount > 0


def test_not_applicable_funding_does_not_block_a_net_claim():
    """Futures have no perpetual funding; that is not a missing component."""
    breakdown = PnLBreakdown(price_pnl=500.0, trading_fees=20.0)
    assert breakdown.economically_confident
    assert breakdown.net() == pytest.approx(480.0)


def test_breakdown_separates_components():
    breakdown = PnLBreakdown(price_pnl=1000.0, trading_fees=30.0, spread_cost=20.0,
                             slippage_cost=15.0)
    payload = breakdown.to_dict()
    for component in ("price_pnl", "trading_fees", "spread_cost", "slippage_cost",
                      "funding", "borrow_cost"):
        assert component in payload["components"]


# -- verdicts --------------------------------------------------------------


def windows_with(expectancies, trades=40, net=0.02, dd=0.05):
    return [
        WindowResult(i, 0.0, 0.0, e, trades, net if e > 0 else -abs(net), dd)
        for i, e in enumerate(expectancies)
    ]


def healthy_matrix():
    matrix = RobustnessMatrix("CAND-001", "ES",
                              trade_removal=run_trade_removal([12.0] * 40 + [-8.0] * 25))
    matrix.add(curve(PerturbationAxis.COST, [(m, 40.0) for m in COST_MULTIPLIERS]))
    matrix.add(curve(PerturbationAxis.DELAY_BARS, [(d, 30.0) for d in DELAY_BARS]))
    return matrix


def test_tiny_sample_is_insufficient_not_a_verdict():
    """21 synthetic trades do not qualify for a robustness conclusion."""
    report = InstrumentReport("ES", windows=windows_with([5.0, 3.0], trades=10))
    verdict, reasons = grade(report)
    assert verdict is Verdict.INSUFFICIENT_SAMPLE
    assert "before any robustness claim" in reasons[0]


def test_negative_out_of_sample_expectancy_fails():
    report = InstrumentReport("ES", windows=windows_with([-5.0] * 8, trades=40))
    assert grade(report)[0] is Verdict.OUT_OF_SAMPLE_FAILURE


def test_mean_carried_by_a_minority_of_windows_is_unstable():
    report = InstrumentReport("ES", windows=windows_with(
        [200.0, -5.0, -5.0, -5.0, -5.0, -5.0, -5.0, -4.0], trades=40))
    verdict, reasons = grade(report)
    assert verdict is Verdict.UNSTABLE
    assert "median" in " ".join(reasons)


def test_cost_sensitive_candidate_is_flagged():
    matrix = healthy_matrix()
    matrix.add(curve(PerturbationAxis.COST,
                     [(1.0, 40.0), (1.25, 10.0), (1.5, -5.0), (2.0, -30.0)]))
    report = InstrumentReport("ES", windows=windows_with([5.0] * 8, trades=40),
                              matrix=matrix)
    assert grade(report)[0] is Verdict.COST_SENSITIVE


def test_execution_sensitive_candidate_is_flagged():
    matrix = healthy_matrix()
    matrix.add(curve(PerturbationAxis.DELAY_BARS,
                     [(0, 40.0), (1, -5.0), (2, -20.0), (3, -30.0)]))
    report = InstrumentReport("ES", windows=windows_with([5.0] * 8, trades=40),
                              matrix=matrix)
    assert grade(report)[0] is Verdict.EXECUTION_SENSITIVE


def test_outlier_dependent_candidate_is_unstable():
    matrix = healthy_matrix()
    matrix.trade_removal = run_trade_removal([500.0] + [-8.0] * 60)
    report = InstrumentReport("ES", windows=windows_with([5.0] * 8, trades=40),
                              matrix=matrix)
    verdict, reasons = grade(report)
    assert verdict is Verdict.UNSTABLE
    assert "best trade" in " ".join(reasons)


def test_regime_dependent_candidate_is_flagged():
    report = InstrumentReport(
        "ES", windows=windows_with([5.0] * 8, trades=40), matrix=healthy_matrix(),
        regimes={"trend": (100, 8.0), "range": (100, -3.0), "high_vol": (100, -2.0)},
    )
    assert grade(report)[0] is Verdict.REGIME_DEPENDENT


def test_thin_regime_samples_are_ignored_not_used():
    """A 5-observation regime bucket must not decide a verdict."""
    report = InstrumentReport(
        "ES", windows=windows_with([5.0] * 8, trades=40), matrix=healthy_matrix(),
        regimes={"trend": (100, 8.0), "range": (100, 6.0), "thin": (5, -50.0)},
    )
    assert grade(report)[0] is Verdict.ROBUST_CANDIDATE


def test_robust_candidate_requires_every_gate():
    report = InstrumentReport(
        "ES", windows=windows_with([5.0] * 8, trades=40), matrix=healthy_matrix(),
        regimes={"trend": (100, 8.0), "range": (100, 4.0)},
    )
    assert grade(report)[0] is Verdict.ROBUST_CANDIDATE


def test_without_a_perturbation_matrix_only_survives_robustness():
    report = InstrumentReport("ES", windows=windows_with([5.0] * 8, trades=40))
    verdict, reasons = grade(report)
    assert verdict is Verdict.SURVIVES_ROBUSTNESS
    assert "cannot certify" in " ".join(reasons)


def test_criteria_are_configurable_and_versioned():
    strict = RobustnessCriteria(version="strict-1", min_total_trades=1000, min_windows=20)
    report = InstrumentReport("ES", windows=windows_with([5.0] * 8, trades=40))
    assert grade(report, strict)[0] is Verdict.INSUFFICIENT_SAMPLE
    assert strict.to_dict()["criteria_version"] == "strict-1"


def test_catastrophic_single_window_is_unstable():
    windows = windows_with([5.0] * 7, trades=40)
    windows.append(WindowResult(7, 0, 0, 1.0, 40, -0.9, 0.9))
    report = InstrumentReport("ES", windows=windows, matrix=healthy_matrix())
    verdict, reasons = grade(report)
    assert verdict is Verdict.UNSTABLE
    assert "catastrophic" in " ".join(reasons)


# -- candidate report ------------------------------------------------------


def a_report():
    report = CandidateReport(
        candidate_id="CAND-001", lineage=a_candidate().lineage(),
        criteria=RobustnessCriteria(), walk_forward=wf_config().to_dict(),
    )
    strong = InstrumentReport("ES", windows=windows_with([5.0] * 8, trades=40),
                              matrix=healthy_matrix(),
                              regimes={"trend": (100, 8.0), "range": (100, 4.0)})
    weak = InstrumentReport("NQ", windows=windows_with([-5.0] * 8, trades=40),
                            matrix=healthy_matrix())
    report.add(strong)
    report.add(weak)
    return report


def test_instrument_failures_are_not_aggregated_away():
    """ES robust, NQ failing -- the overall verdict must reflect NQ."""
    report = a_report()
    assert report.verdicts["ES"] is Verdict.ROBUST_CANDIDATE
    assert report.verdicts["NQ"] is Verdict.OUT_OF_SAMPLE_FAILURE
    assert report.overall is Verdict.OUT_OF_SAMPLE_FAILURE


def test_report_lists_every_required_section():
    payload = a_report().to_dict()
    for field in ("candidate_id", "lineage", "criteria", "walk_forward",
                  "overall_verdict", "instruments", "holdout"):
        assert field in payload
    for instrument in payload["instruments"].values():
        for field in ("windows", "total_trades", "mean_expectancy",
                      "median_expectancy", "positive_windows", "max_drawdown",
                      "ambiguous_bar_count", "regimes", "robustness", "verdict"):
            assert field in instrument


def test_report_renders_and_shows_holdout_unspent():
    rendered = a_report().render()
    assert "OVERALL (weakest instrument)" in rendered
    assert "NOT SPENT" in rendered
    assert "profitable" not in rendered.lower()


def test_holdout_is_not_touched_by_reporting():
    """Producing a report must never consume the locked holdout."""
    report = a_report()
    assert report.holdout is None
    assert report.to_dict()["holdout"] is None


def test_reports_are_reproducible_from_the_same_inputs():
    first, second = a_report().to_dict(), a_report().to_dict()
    first.pop("created_at"), second.pop("created_at")
    assert first == second


def test_zero_validation_requires_some_separation():
    """Train -> test with nothing between them is refused outright."""
    with pytest.raises(ValueError, match="nothing separates"):
        WalkForwardConfig(train=timedelta(days=60), validation=timedelta(0),
                          test=timedelta(days=20), step=timedelta(days=20),
                          label_horizon=timedelta(0), embargo=timedelta(0))


def test_zero_validation_is_allowed_with_an_embargo():
    config = WalkForwardConfig(
        train=timedelta(days=60), validation=timedelta(0), test=timedelta(days=20),
        step=timedelta(days=20), label_horizon=timedelta(0), embargo=timedelta(days=2),
    )
    windows = generate_windows(config, T0, T0 + 200 * DAY)
    assert windows
    assert windows[0].validation_start == windows[0].validation_end
    assert windows[0].purge_cutoff < windows[0].test_start
