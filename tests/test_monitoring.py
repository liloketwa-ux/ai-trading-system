"""Tests for drift detection, divergence, performance tracking, and the monitor."""

import numpy as np
import pandas as pd
import pytest

from ai_trading.monitoring import (
    PSI_SHIFTED,
    PSI_STABLE,
    EventLog,
    Monitor,
    MonitorThresholds,
    PerformanceTracker,
    Severity,
    compare_to_backtest,
    drift_report,
    ks_two_sample,
    population_stability_index,
)


@pytest.fixture
def rng():
    return np.random.default_rng(101)


def days(n, start="2024-01-01"):
    return pd.date_range(start, periods=n, freq="D")


# -- events ----------------------------------------------------------------


def test_severity_is_ordered():
    assert Severity.INFO < Severity.WARNING < Severity.CRITICAL


def test_event_log_records_and_filters():
    log = EventLog()
    log.emit("a", Severity.INFO, "info message")
    log.emit("b", Severity.CRITICAL, "critical message")

    assert len(log) == 2
    assert len(log.filter(min_severity=Severity.WARNING)) == 1
    assert len(log.filter(kind="a")) == 1
    assert log.worst_severity is Severity.CRITICAL


def test_empty_log_has_no_worst_severity():
    assert EventLog().worst_severity is None


def test_event_details_survive_into_the_frame():
    log = EventLog()
    log.emit("drift", Severity.WARNING, "shifted", psi=0.42)
    frame = log.to_frame()
    assert frame.loc[0, "psi"] == pytest.approx(0.42)
    assert frame.loc[0, "severity"] == "WARNING"


def test_log_can_be_cleared():
    log = EventLog()
    log.emit("a", Severity.INFO, "x")
    log.clear()
    assert len(log) == 0


# -- PSI -------------------------------------------------------------------


def test_psi_is_zero_for_an_identical_sample(rng):
    sample = rng.normal(0, 1, 5_000)
    assert population_stability_index(sample, sample) == pytest.approx(0.0, abs=1e-9)


def test_psi_is_small_for_two_draws_from_one_distribution(rng):
    a = rng.normal(0, 1, 10_000)
    b = rng.normal(0, 1, 10_000)
    assert population_stability_index(a, b) < PSI_STABLE


def test_psi_grows_monotonically_with_separation(rng):
    reference = rng.normal(0, 1, 10_000)
    shifts = [0.1, 0.5, 1.0, 2.0]
    values = [
        population_stability_index(reference, rng.normal(s, 1, 10_000)) for s in shifts
    ]
    assert values == sorted(values), f"PSI should grow with the shift, got {values}"


def test_psi_flags_a_large_shift_as_shifted(rng):
    reference = rng.normal(0, 1, 10_000)
    assert population_stability_index(reference, rng.normal(2, 1, 10_000)) > PSI_SHIFTED


def test_psi_handles_values_outside_the_reference_range(rng):
    """Open outer edges mean out-of-range values are binned, not dropped."""
    reference = rng.uniform(0, 1, 1_000)
    current = rng.uniform(5, 6, 1_000)
    assert np.isfinite(population_stability_index(reference, current))


def test_psi_of_a_constant_reference():
    assert population_stability_index(np.full(100, 5.0), np.full(100, 5.0)) == 0.0
    assert population_stability_index(np.full(100, 5.0), np.full(100, 9.0)) == float("inf")


def test_psi_ignores_nans(rng):
    sample = rng.normal(0, 1, 1_000)
    with_nans = np.r_[sample, np.full(50, np.nan)]
    assert population_stability_index(sample, with_nans) == pytest.approx(
        population_stability_index(sample, sample), abs=1e-9
    )


def test_psi_rejects_too_few_bins(rng):
    with pytest.raises(ValueError, match="bins"):
        population_stability_index(rng.normal(size=10), rng.normal(size=10), bins=1)


def test_psi_is_nan_for_an_empty_sample():
    assert np.isnan(population_stability_index(np.array([]), np.array([1.0, 2.0])))


# -- KS --------------------------------------------------------------------


def test_ks_statistic_is_zero_for_identical_samples(rng):
    sample = rng.normal(0, 1, 1_000)
    assert ks_two_sample(sample, sample).statistic == pytest.approx(0.0)


def test_ks_statistic_is_one_for_disjoint_samples():
    result = ks_two_sample(np.arange(0.0, 100.0), np.arange(200.0, 300.0))
    assert result.statistic == pytest.approx(1.0)
    assert result.p_value < 1e-6


def test_ks_does_not_flag_two_draws_from_one_distribution(rng):
    a = rng.normal(0, 1, 2_000)
    b = rng.normal(0, 1, 2_000)
    assert not ks_two_sample(a, b).is_significant()


def test_ks_flags_a_shifted_distribution(rng):
    a = rng.normal(0, 1, 2_000)
    b = rng.normal(0.5, 1, 2_000)
    assert ks_two_sample(a, b).is_significant()


def test_ks_detects_a_variance_change_that_leaves_the_mean_alone(rng):
    """A pure scale change is invisible to a mean test but not to KS."""
    a = rng.normal(0, 1, 3_000)
    b = rng.normal(0, 3, 3_000)
    assert ks_two_sample(a, b).is_significant()


def test_ks_statistic_stays_within_bounds(rng):
    for _ in range(10):
        result = ks_two_sample(rng.normal(size=200), rng.normal(size=200))
        assert 0.0 <= result.statistic <= 1.0
        assert 0.0 <= result.p_value <= 1.0


def test_ks_is_nan_for_an_empty_sample():
    result = ks_two_sample(np.array([]), np.array([1.0]))
    assert np.isnan(result.statistic)


def test_ks_matches_scipy_reference(rng):
    """Validate the hand-rolled KS against SciPy, when SciPy is available.

    The library itself depends only on numpy; SciPy is a test-only convenience,
    so this skips rather than failing when it is absent.
    """
    stats = pytest.importorskip("scipy.stats")

    cases = [
        (rng.normal(0, 1, 1_000), rng.normal(0, 1, 1_000)),
        (rng.normal(0, 1, 1_000), rng.normal(0.3, 1, 1_000)),
        (rng.normal(0, 1, 800), rng.normal(1.0, 1, 1_200)),  # unequal sizes
        (rng.normal(0, 1, 1_500), rng.normal(0, 2.5, 1_500)),  # variance only
        (rng.uniform(-2, 2, 900), rng.normal(0, 1, 900)),  # different shape
        (rng.normal(0, 1, 40), rng.normal(0.8, 1, 35)),  # small samples
    ]
    for a, b in cases:
        mine = ks_two_sample(a, b)
        reference = stats.ks_2samp(a, b)
        assert mine.statistic == pytest.approx(reference.statistic, abs=1e-12)
        # The asymptotic p-value diverges from the exact one deep in the tail,
        # where both are far past any threshold; agreement where it matters is
        # what the check is for.
        if reference.pvalue > 1e-4:
            assert mine.p_value == pytest.approx(reference.pvalue, abs=0.02)
        else:
            assert mine.p_value < 1e-3


# -- drift report ----------------------------------------------------------


def test_drift_report_ranks_the_shifted_feature_first(rng):
    reference = pd.DataFrame(
        {"stable": rng.normal(0, 1, 3_000), "shifted": rng.normal(0, 1, 3_000)}
    )
    current = pd.DataFrame(
        {"stable": rng.normal(0, 1, 3_000), "shifted": rng.normal(3, 1, 3_000)}
    )
    report = drift_report(reference, current)

    assert report.index[0] == "shifted"
    assert report.loc["shifted", "verdict"] == "shifted"
    assert report.loc["stable", "verdict"] == "stable"


def test_drift_report_only_compares_shared_columns(rng):
    reference = pd.DataFrame({"a": rng.normal(size=500), "only_ref": rng.normal(size=500)})
    current = pd.DataFrame({"a": rng.normal(size=500), "only_cur": rng.normal(size=500)})
    assert list(drift_report(reference, current).index) == ["a"]


def test_drift_report_skips_non_numeric_columns(rng):
    reference = pd.DataFrame({"a": rng.normal(size=100), "label": ["x"] * 100})
    current = pd.DataFrame({"a": rng.normal(size=100), "label": ["y"] * 100})
    assert "label" not in drift_report(reference, current).index


def test_drift_report_with_no_shared_columns_is_empty():
    a = pd.DataFrame({"x": [1.0, 2.0]})
    b = pd.DataFrame({"y": [1.0, 2.0]})
    assert drift_report(a, b).empty


# -- divergence ------------------------------------------------------------


def test_identical_series_are_aligned(rng):
    returns = pd.Series(rng.normal(0.001, 0.01, 200), index=days(200))
    report = compare_to_backtest(returns, returns)

    assert report.verdict == "aligned"
    assert report.mean_difference == pytest.approx(0.0)
    assert report.p_value == pytest.approx(1.0)
    assert not report.is_significant


def test_systematic_underperformance_is_detected(rng):
    backtest = pd.Series(rng.normal(0.001, 0.01, 300), index=days(300))
    live = backtest - 0.002  # a consistent per-period drag
    report = compare_to_backtest(live, backtest)

    assert report.verdict == "underperforming"
    assert report.annualized_difference < 0
    assert report.is_significant


def test_systematic_outperformance_is_detected(rng):
    backtest = pd.Series(rng.normal(0.001, 0.01, 300), index=days(300))
    report = compare_to_backtest(backtest + 0.002, backtest)
    assert report.verdict == "outperforming"


def test_noise_alone_does_not_trigger_a_verdict(rng):
    """Unbiased noise must not be reported as divergence."""
    backtest = pd.Series(rng.normal(0.001, 0.01, 500), index=days(500))
    live = backtest + rng.normal(0.0, 0.001, 500)
    assert compare_to_backtest(live, backtest).verdict == "aligned"


def test_only_overlapping_periods_are_compared(rng):
    backtest = pd.Series(rng.normal(0.001, 0.01, 100), index=days(100))
    live = backtest.iloc[40:]  # a partial live history
    assert compare_to_backtest(live, backtest).n_periods == 60


def test_tracking_error_is_zero_when_series_match(rng):
    returns = pd.Series(rng.normal(0.001, 0.01, 100), index=days(100))
    assert compare_to_backtest(returns, returns).tracking_error == pytest.approx(0.0)


def test_divergence_needs_overlapping_periods():
    a = pd.Series([0.01, 0.02], index=days(2, "2024-01-01"))
    b = pd.Series([0.01, 0.02], index=days(2, "2025-01-01"))
    with pytest.raises(ValueError, match="at least 2 overlapping"):
        compare_to_backtest(a, b)


def test_divergence_summary_is_readable(rng):
    returns = pd.Series(rng.normal(0.001, 0.01, 100), index=days(100))
    assert "aligned" in compare_to_backtest(returns, returns).summary()


# -- performance tracker ---------------------------------------------------


def test_tracker_computes_drawdown_from_peak():
    tracker = PerformanceTracker()
    for equity in (100_000.0, 120_000.0, 90_000.0):
        tracker.record(pd.Timestamp("2024-01-01"), equity)
    assert tracker.current_drawdown == pytest.approx(0.25)
    assert tracker.peak_equity == pytest.approx(120_000.0)


def test_tracker_metrics_match_the_backtester_definitions():
    tracker = PerformanceTracker(periods_per_year=252)
    for i, equity in enumerate(np.linspace(100_000.0, 130_000.0, 50)):
        tracker.record(days(50)[i], equity)
    tracker.record_trade(500.0)
    tracker.record_trade(-200.0)

    metrics = tracker.metrics()
    assert metrics["total_return"] == pytest.approx(0.3)
    assert metrics["num_trades"] == 2.0
    assert metrics["win_rate"] == pytest.approx(0.5)


def test_tracker_metrics_empty_before_two_observations():
    tracker = PerformanceTracker()
    tracker.record(pd.Timestamp("2024-01-01"), 100.0)
    assert tracker.metrics() == {}


def test_tracker_rejects_out_of_order_timestamps():
    tracker = PerformanceTracker()
    tracker.record(pd.Timestamp("2024-01-02"), 100.0)
    with pytest.raises(ValueError, match="non-decreasing"):
        tracker.record(pd.Timestamp("2024-01-01"), 100.0)


def test_tracker_rejects_non_positive_equity():
    with pytest.raises(ValueError, match="equity"):
        PerformanceTracker().record(pd.Timestamp("2024-01-01"), 0.0)


def test_rolling_sharpe_has_a_value_once_the_window_fills(rng):
    tracker = PerformanceTracker()
    equity = 100_000 * np.exp(np.cumsum(rng.normal(0.001, 0.01, 100)))
    for ts, value in zip(days(100), equity):
        tracker.record(ts, value)
    rolling = tracker.rolling_sharpe(30)
    assert rolling.dropna().size > 0


# -- monitor ---------------------------------------------------------------


def test_monitor_escalates_through_drawdown_thresholds():
    monitor = Monitor(MonitorThresholds(drawdown_warning=0.10, drawdown_critical=0.20))
    monitor.record_equity(days(3)[0], 100_000.0)
    assert len(monitor.log) == 0

    monitor.record_equity(days(3)[1], 88_000.0)  # -12%
    assert monitor.log.worst_severity is Severity.WARNING
    assert monitor.healthy

    monitor.record_equity(days(3)[2], 75_000.0)  # -25%
    assert monitor.log.worst_severity is Severity.CRITICAL
    assert not monitor.healthy


def test_monitor_stays_quiet_while_equity_rises():
    monitor = Monitor()
    for i, equity in enumerate([100_000.0, 105_000.0, 110_000.0]):
        monitor.record_equity(days(3)[i], equity)
    assert len(monitor.log) == 0
    assert monitor.healthy


def test_monitor_emits_a_drift_event(rng):
    monitor = Monitor()
    reference = pd.DataFrame({"rsi": rng.normal(50, 10, 2_000)})
    current = pd.DataFrame({"rsi": rng.normal(80, 10, 2_000)})

    monitor.check_drift(reference, current)
    drift_events = monitor.log.filter(kind="drift")
    assert len(drift_events) == 1
    assert drift_events[0].severity is Severity.CRITICAL


def test_monitor_emits_no_drift_event_when_stable(rng):
    monitor = Monitor()
    reference = pd.DataFrame({"rsi": rng.normal(50, 10, 2_000)})
    current = pd.DataFrame({"rsi": rng.normal(50, 10, 2_000)})
    monitor.check_drift(reference, current)
    assert not monitor.log.filter(kind="drift")


def test_monitor_warns_when_live_trails_backtest(rng):
    monitor = Monitor()
    backtest = pd.Series(rng.normal(0.001, 0.005, 200), index=days(200))

    equity = 100_000.0
    for ts, r in zip(days(200), backtest - 0.002):
        equity *= 1 + r
        monitor.record_equity(ts, equity)

    report = monitor.check_divergence(backtest)
    assert report is not None
    assert report.verdict == "underperforming"
    assert monitor.log.filter(kind="divergence")


def test_monitor_divergence_returns_none_without_history():
    assert Monitor().check_divergence(pd.Series([0.01, 0.02], index=days(2))) is None


def test_monitor_flags_performance_decay():
    monitor = Monitor(MonitorThresholds(min_rolling_sharpe=0.0))
    equity = 100_000.0
    for ts in days(80):
        equity *= 0.999  # a steady bleed
        monitor.record_equity(ts, equity)

    monitor.check_performance_decay(window=30)
    assert monitor.log.filter(kind="performance_decay")


def test_monitor_decay_check_disabled_by_none():
    monitor = Monitor(MonitorThresholds(min_rolling_sharpe=None))
    assert monitor.check_performance_decay() is None


def test_snapshot_reports_current_state():
    monitor = Monitor()
    for i, equity in enumerate([100_000.0, 110_000.0, 105_000.0]):
        monitor.record_equity(days(3)[i], equity)

    snapshot = monitor.snapshot()
    assert snapshot["equity"] == pytest.approx(105_000.0)
    assert snapshot["peak_equity"] == pytest.approx(110_000.0)
    assert snapshot["drawdown"] == pytest.approx(1 - 105_000 / 110_000)
    assert snapshot["healthy"] is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"drawdown_warning": 0.0},
        {"drawdown_warning": 0.5, "drawdown_critical": 0.2},  # inverted
        {"psi_warning": 0.5, "psi_critical": 0.1},  # inverted
        {"divergence_alpha": 0.0},
        {"divergence_alpha": 1.0},
    ],
)
def test_thresholds_reject_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        MonitorThresholds(**kwargs)
