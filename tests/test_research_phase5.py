"""Phase 5 research machinery tests.

The system must be *willing to conclude no edge*. Several tests below assert
exactly that: fed data with no relationship, the pipeline must say so rather
than find something.
"""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from ai_trading.features.ict_hypotheses import build_ict_vector, detect_fvg
from ai_trading.research import (
    FORWARD_RETURNS,
    OPTIMISTIC,
    PESSIMISTIC,
    R_LABELS,
    Conclusion,
    CostModel,
    Event,
    HypothesisRegistry,
    LabelDefinition,
    LabelKind,
    SamplingPolicy,
    apply_sampling,
    evaluate_hypothesis,
)
from ai_trading.research.baselines import BASELINES
from ai_trading.research.labels import (
    Label,
    LabelError,
    compute_forward_return,
    compute_r_multiple,
)
from ai_trading.research.statistics import (
    benjamini_hochberg,
    bonferroni,
    bootstrap_difference,
    bootstrap_mean,
    cohens_d,
    deflated_sharpe_ratio,
    hit_rate,
    permutation_test,
)
from ai_trading.storage import InMemoryStore, Observation

UTC = timezone.utc
T0 = datetime(2024, 3, 4, tzinfo=UTC)
HOUR = timedelta(hours=1)


def bar(open_time, o, h, l, c, v=1000.0, timeframe="1h", instrument="ES"):
    return Observation(
        key=instrument, kind="ohlcv", event_time=open_time,
        available_at=open_time + HOUR, ingested_at=open_time + HOUR,
        source="test", timeframe=timeframe,
        value={"open": o, "high": h, "low": l, "close": c, "volume": v},
    )


# -- label definitions -----------------------------------------------------


def test_label_definitions_are_versioned_and_checksummed():
    definition = FORWARD_RETURNS["1h"]
    assert definition.key.endswith(":v1")
    assert len(definition.checksum) == 16


def test_changing_a_label_changes_its_checksum():
    a = LabelDefinition("x", LabelKind.FORWARD_RETURN, timedelta(hours=1))
    b = LabelDefinition("x", LabelKind.FORWARD_RETURN, timedelta(hours=2))
    assert a.checksum != b.checksum


def test_label_rejects_nonpositive_horizon():
    with pytest.raises(ValueError, match="horizon"):
        LabelDefinition("x", LabelKind.FORWARD_RETURN, timedelta(0))


def test_r_multiple_label_requires_a_target():
    with pytest.raises(ValueError, match="target_r"):
        LabelDefinition("x", LabelKind.R_MULTIPLE, timedelta(hours=1))


def test_bad_tie_policy_rejected():
    with pytest.raises(ValueError, match="tie_policy"):
        LabelDefinition("x", LabelKind.FORWARD_RETURN, timedelta(hours=1), tie_policy="coin")


# -- label computation -----------------------------------------------------


def future_bars(n=8, start=T0 + HOUR, drift=1.0):
    return [bar(start + i * HOUR, 100 + i * drift, 102 + i * drift,
                98 + i * drift, 101 + i * drift) for i in range(n)]


def test_forward_return_uses_only_bars_within_the_horizon():
    definition = FORWARD_RETURNS["1h"]
    label = compute_forward_return(definition, "ES", T0, 100.0, future_bars(8))
    assert label.resolved
    assert label.detail["bars"] == 1  # 1h horizon -> exactly one hourly bar


def test_forward_return_unresolved_without_future_bars():
    label = compute_forward_return(FORWARD_RETURNS["1h"], "ES", T0, 100.0, [])
    assert not label.resolved and label.value is None


def test_costs_reduce_the_forward_return():
    definition = FORWARD_RETURNS["4h"]
    gross = compute_forward_return(definition, "ES", T0, 100.0, future_bars(8))
    net = compute_forward_return(definition, "ES", T0, 100.0, future_bars(8), cost_bps=10)
    assert net.value < gross.value
    assert net.value == pytest.approx(gross.value - 0.001)


def test_r_multiple_target_hit():
    definition = R_LABELS["hit_1R"]
    bars = [bar(T0 + HOUR, 100, 112, 99, 111)]  # +12 on a 10-wide stop
    label = compute_r_multiple(definition, "ES", T0, 100.0, 10.0, bars)
    assert label.detail["outcome"] == "target"
    assert label.value == pytest.approx(1.0)


def test_r_multiple_stop_hit():
    definition = R_LABELS["hit_1R"]
    bars = [bar(T0 + HOUR, 100, 101, 88, 89)]
    label = compute_r_multiple(definition, "ES", T0, 100.0, 10.0, bars)
    assert label.detail["outcome"] == "stop"
    assert label.value == pytest.approx(-1.0)


def test_r_multiple_tie_resolves_to_the_stop_by_default():
    """Bar data cannot say which side was touched first; assume the worse."""
    definition = R_LABELS["hit_1R"]
    bars = [bar(T0 + HOUR, 100, 115, 85, 100)]  # spans both levels
    label = compute_r_multiple(definition, "ES", T0, 100.0, 10.0, bars)
    assert label.detail["tie"] is True
    assert label.detail["outcome"] == "stop"


def test_r_multiple_timeout_marks_to_final_close():
    definition = LabelDefinition("t", LabelKind.R_MULTIPLE, timedelta(hours=2), target_r=5.0)
    bars = [bar(T0 + HOUR, 100, 101, 99, 100.5)]
    label = compute_r_multiple(definition, "ES", T0, 100.0, 10.0, bars)
    assert label.detail["outcome"] == "timeout"


def test_r_multiple_reports_mae_and_mfe():
    definition = R_LABELS["hit_2R"]
    bars = [bar(T0 + HOUR, 100, 105, 95, 100), bar(T0 + 2 * HOUR, 100, 121, 99, 120)]
    label = compute_r_multiple(definition, "ES", T0, 100.0, 10.0, bars)
    assert label.detail["mae_r"] == pytest.approx(0.5)
    assert label.detail["mfe_r"] >= 2.0


def test_r_multiple_rejects_nonpositive_stop():
    with pytest.raises(LabelError, match="stop_distance"):
        compute_r_multiple(R_LABELS["hit_1R"], "ES", T0, 100.0, 0.0, future_bars())


def test_label_cannot_resolve_before_the_decision():
    with pytest.raises(LabelError, match="cannot resolve before"):
        Label("k", "ES", T0, T0 - HOUR, 1.0, True)


def test_label_is_not_a_feature_snapshot():
    """Structural separation: a Label has no available_at, so it cannot pose
    as an input feature."""
    label = compute_forward_return(FORWARD_RETURNS["1h"], "ES", T0, 100.0, future_bars())
    assert not hasattr(label, "available_at")
    assert hasattr(label, "resolved_at")


# -- sampling --------------------------------------------------------------


def events_every_hour(n=20, instrument="ES"):
    return [
        Event(instrument, T0 + i * HOUR, {"bar_return": 0.001, "session": "london"})
        for i in range(n)
    ]


def test_min_spacing_thins_overlapping_events():
    policy = SamplingPolicy(min_spacing=timedelta(hours=4), deduplicate_overlaps=False)
    sampled = apply_sampling(events_every_hour(20), policy)
    assert len(sampled) == 5
    gaps = [(b.decision_time - a.decision_time) for a, b in zip(sampled, sampled[1:])]
    assert all(g >= timedelta(hours=4) for g in gaps)


def test_overlap_dedup_uses_the_label_horizon():
    """Events closer than one horizon share outcome bars and are not independent."""
    policy = SamplingPolicy(min_spacing=timedelta(0), deduplicate_overlaps=True,
                            label_horizon=timedelta(hours=6))
    assert policy.effective_spacing == timedelta(hours=6)
    sampled = apply_sampling(events_every_hour(24), policy)
    assert len(sampled) == 4


def test_exclusion_windows_drop_events():
    policy = SamplingPolicy(
        deduplicate_overlaps=False,
        exclusion_windows=((T0 + 5 * HOUR, T0 + 10 * HOUR),),
    )
    sampled = apply_sampling(events_every_hour(20), policy)
    assert not any(T0 + 5 * HOUR <= e.decision_time < T0 + 10 * HOUR for e in sampled)


def test_max_events_per_instrument_caps():
    policy = SamplingPolicy(deduplicate_overlaps=False, max_events_per_instrument=3)
    assert len(apply_sampling(events_every_hour(20), policy)) == 3


def test_sampling_policy_is_recorded():
    policy = SamplingPolicy(min_spacing=timedelta(hours=2))
    payload = policy.to_dict()
    assert payload["min_spacing_s"] == 7200.0
    assert "deduplicate_overlaps" in payload


# -- costs -----------------------------------------------------------------


def test_round_trip_cost_charges_both_sides():
    model = CostModel("x", spread_bps=2, slippage_bps=3, commission_bps=1)
    assert model.round_trip_bps == pytest.approx(2 + 2 * (3 + 1))


def test_pessimistic_costs_more_than_optimistic():
    assert PESSIMISTIC.round_trip_bps > OPTIMISTIC.round_trip_bps


def test_cost_application_reduces_return():
    assert PESSIMISTIC.apply(0.01) < 0.01


def test_negative_costs_rejected():
    with pytest.raises(ValueError):
        CostModel("x", spread_bps=-1, slippage_bps=0, commission_bps=0)


# -- statistics ------------------------------------------------------------


def test_bootstrap_is_reproducible_with_a_seed():
    values = list(np.random.default_rng(0).normal(0, 1, 200))
    a = bootstrap_mean(values, seed=42, resamples=1000)
    b = bootstrap_mean(values, seed=42, resamples=1000)
    assert (a.estimate, a.lower, a.upper) == (b.estimate, b.lower, b.upper)


def test_different_seeds_give_different_intervals():
    values = list(np.random.default_rng(0).normal(0, 1, 200))
    a = bootstrap_mean(values, seed=1, resamples=1000)
    b = bootstrap_mean(values, seed=2, resamples=1000)
    assert (a.lower, a.upper) != (b.lower, b.upper)


def test_bootstrap_ci_is_calibrated_under_the_null():
    """A 95% interval must exclude zero on roughly 5% of null samples.

    Asserting that one particular seeded draw contains zero would be asserting
    that draw is not in the tail -- which it sometimes legitimately is. (Seed 7
    at n=500 gives a sample mean 2.9 standard errors from zero: a real false
    positive, correctly reported.) The property worth testing is the *rate*.
    """
    false_positives = 0
    trials = 200
    for seed in range(trials):
        values = np.random.default_rng(seed).normal(0, 1, 200)
        if bootstrap_mean(values, seed=seed, resamples=400).excludes_zero:
            false_positives += 1

    rate = false_positives / trials
    assert 0.01 <= rate <= 0.12, f"false-positive rate {rate:.1%} is not near the nominal 5%"


def test_bootstrap_ci_excludes_zero_for_a_real_effect():
    values = list(np.random.default_rng(7).normal(1.0, 0.5, 500))
    assert bootstrap_mean(values, seed=3, resamples=2000).excludes_zero


def test_bootstrap_difference_detects_no_difference():
    rng = np.random.default_rng(11)
    a, b = rng.normal(0, 1, 300), rng.normal(0, 1, 300)
    assert not bootstrap_difference(a, b, seed=5, resamples=2000).excludes_zero


def test_permutation_test_reports_high_p_for_null():
    rng = np.random.default_rng(13)
    p = permutation_test(rng.normal(0, 1, 200), rng.normal(0, 1, 200), seed=1, resamples=500)
    assert p > 0.05


def test_permutation_test_reports_low_p_for_a_real_shift():
    rng = np.random.default_rng(13)
    p = permutation_test(rng.normal(1.0, 1, 200), rng.normal(0, 1, 200), seed=1, resamples=500)
    assert p < 0.01


def test_benjamini_hochberg_rejects_nothing_under_the_null():
    rng = np.random.default_rng(3)
    assert sum(benjamini_hochberg([float(rng.random()) for _ in range(40)])) == 0


def test_benjamini_hochberg_is_less_conservative_than_bonferroni():
    p_values = [0.001, 0.008, 0.02, 0.04, 0.3, 0.5]
    assert sum(benjamini_hochberg(p_values)) >= sum(bonferroni(p_values))


def test_deflated_sharpe_collapses_with_trial_count():
    """Testing many configurations makes the best Sharpe rise for free."""
    single = deflated_sharpe_ratio(1.5, n_trials=1, n_observations=252)
    many = deflated_sharpe_ratio(1.5, n_trials=200, n_observations=252)
    assert single > 0.9
    assert many < single
    assert many < 0.5


def test_cohens_d_zero_for_identical_distributions():
    rng = np.random.default_rng(0)
    a, b = rng.normal(0, 1, 500), rng.normal(0, 1, 500)
    assert abs(cohens_d(a, b)) < 0.2


def test_hit_rate_counts_positives():
    assert hit_rate([1.0, -1.0, 2.0, -0.5]) == pytest.approx(0.5)


# -- hypothesis registry ---------------------------------------------------


def make_registry(tmp_path):
    return HypothesisRegistry(tmp_path / "hypotheses.jsonl")


def register(registry, hypothesis_id="ICT-001", features=("liquidity_sweep",)):
    return registry.register(
        hypothesis_id, "test hypothesis", features,
        label_key="forward_return_1h:v1", horizon_seconds=3600.0,
        research_version="r1", dataset_version="d1",
    )


def test_hypothesis_registration_records_provenance(tmp_path):
    hypothesis = register(make_registry(tmp_path))
    assert hypothesis.code_commit
    assert hypothesis.checksum
    assert hypothesis.family == "ICT"


def test_hypotheses_are_immutable(tmp_path):
    registry = make_registry(tmp_path)
    register(registry, features=("liquidity_sweep",))
    with pytest.raises(ValueError, match="immutable"):
        register(registry, features=("liquidity_sweep", "fvg"))


def test_reregistering_identical_content_is_allowed(tmp_path):
    registry = make_registry(tmp_path)
    first = register(registry)
    second = register(registry)
    assert first.checksum == second.checksum


def test_registry_persists(tmp_path):
    register(make_registry(tmp_path))
    assert HypothesisRegistry(tmp_path / "hypotheses.jsonl").get("ICT-001") is not None


def test_family_size_is_the_trial_denominator(tmp_path):
    registry = make_registry(tmp_path)
    for i in range(6):
        register(registry, f"ICT-{i:03d}", ("liquidity_sweep", f"f{i}"))
    assert registry.family_size("ICT") == 6


def test_empty_feature_set_rejected(tmp_path):
    with pytest.raises(ValueError, match="feature_set"):
        make_registry(tmp_path).register(
            "X", "d", (), label_key="l", horizon_seconds=1.0,
            research_version="r", dataset_version="d",
        )


def test_unregistered_hypothesis_raises(tmp_path):
    with pytest.raises(KeyError, match="unregistered"):
        make_registry(tmp_path).require("ICT-999")


# -- baselines -------------------------------------------------------------


def test_all_seven_baselines_exist():
    assert set(BASELINES) == {
        "random", "hold_matched_random", "momentum", "mean_reversion",
        "session_only", "volatility_only", "structure_only",
    }


def test_hold_matched_random_matches_the_treatment_count():
    events = events_every_hour(60)
    selected = BASELINES["hold_matched_random"].select(events, 0, 12)
    assert len(selected) == 12


def test_momentum_and_mean_reversion_partition_by_sign():
    events = [
        Event("ES", T0 + i * HOUR, {"bar_return": 0.001 if i % 2 else -0.001})
        for i in range(20)
    ]
    up = BASELINES["momentum"].select(events, 0)
    down = BASELINES["mean_reversion"].select(events, 0)
    assert len(up) + len(down) == 20
    assert not set(id(e) for e in up) & set(id(e) for e in down)


def test_volatility_only_selects_above_median():
    events = [Event("ES", T0 + i * HOUR, {"displacement_atr": float(i)}) for i in range(11)]
    assert len(BASELINES["volatility_only"].select(events, 0)) == 5


def test_unknown_baseline_raises():
    from ai_trading.research.baselines import select_baseline

    with pytest.raises(KeyError, match="unknown baseline"):
        select_baseline("vibes", [], 0)


# -- evaluation and verdicts ----------------------------------------------


def synthetic_campaign(tmp_path, effect=0.0, n=200, seed=0):
    """Events with an optional planted effect on the treatment subset."""
    rng = np.random.default_rng(seed)
    registry = make_registry(tmp_path)
    hypothesis = register(registry, features=("liquidity_sweep", "fvg"))

    events, labels = [], {}
    for i in range(n):
        when = T0 + i * 6 * HOUR
        treated = i % 3 == 0
        features = {
            "liquidity_sweep": treated, "fvg": treated,
            "displacement_atr": float(rng.uniform(0.5, 2.0)),
            "mss": bool(rng.random() > 0.5),
            "session": "new_york" if i % 2 else "london",
            "htf_bias": "bullish" if i % 4 else "bearish",
            "bar_return": float(rng.normal(0, 0.001)),
        }
        events.append(Event("ES", when, features))
        outcome = float(rng.normal(effect if treated else 0.0, 0.01))
        labels[when] = Label("forward_return_1h:v1", "ES", when, when + HOUR, outcome, True)
    return hypothesis, events, labels


def test_no_relationship_yields_no_evidence(tmp_path):
    """The system must be willing to find nothing."""
    hypothesis, events, labels = synthetic_campaign(tmp_path, effect=0.0, seed=1)
    treatment = [e for e in events if e.features["liquidity_sweep"]]

    result = evaluate_hypothesis(
        hypothesis, treatment, labels,
        label_definition=FORWARD_RETURNS["1h"],
        sampling_policy=SamplingPolicy(),
        cost_model=PESSIMISTIC, n_trials=6, seed=7, resamples=500,
    )
    assert result.conclusion is Conclusion.NO_EVIDENCE
    assert "includes zero" in " ".join(result.reasons) or "beats none" in " ".join(result.reasons)


def test_insufficient_sample_is_its_own_verdict(tmp_path):
    hypothesis, events, labels = synthetic_campaign(tmp_path, n=20, seed=2)
    treatment = [e for e in events if e.features["liquidity_sweep"]]
    result = evaluate_hypothesis(
        hypothesis, treatment, labels, label_definition=FORWARD_RETURNS["1h"],
        sampling_policy=SamplingPolicy(), seed=1, resamples=200,
    )
    assert result.conclusion is Conclusion.INSUFFICIENT_SAMPLE


def test_gross_effect_killed_by_costs_is_economically_unattractive(tmp_path):
    """A 3bp gross edge against a 10bp round trip is not a finding."""
    hypothesis, events, labels = synthetic_campaign(tmp_path, effect=0.0003, n=400, seed=3)
    treatment = [e for e in events if e.features["liquidity_sweep"]]

    result = evaluate_hypothesis(
        hypothesis, treatment, labels, label_definition=FORWARD_RETURNS["1h"],
        sampling_policy=SamplingPolicy(), cost_model=PESSIMISTIC,
        n_trials=6, seed=5, resamples=800,
    )
    assert result.conclusion in (
        Conclusion.ECONOMICALLY_UNATTRACTIVE, Conclusion.NO_EVIDENCE,
        Conclusion.UNSTABLE, Conclusion.WEAK,
    )


def test_report_records_every_required_field(tmp_path):
    hypothesis, events, labels = synthetic_campaign(tmp_path, n=200, seed=4)
    treatment = [e for e in events if e.features["liquidity_sweep"]]
    result = evaluate_hypothesis(
        hypothesis, treatment, labels, label_definition=FORWARD_RETURNS["1h"],
        sampling_policy=SamplingPolicy(min_spacing=timedelta(hours=6)),
        cost_model=PESSIMISTIC, n_trials=6, seed=9, resamples=400,
        feature_versions={"liquidity_sweep": "1", "fvg": "1"},
    )
    payload = result.to_dict()
    for field in ("hypothesis_id", "dataset_version", "feature_versions",
                  "label_definition", "sampling_policy", "cost_model", "n_events",
                  "raw", "net", "baselines", "regimes", "n_trials", "seed",
                  "conclusion"):
        assert field in payload, f"report missing {field}"


def test_report_renders_readably(tmp_path):
    hypothesis, events, labels = synthetic_campaign(tmp_path, n=200, seed=6)
    treatment = [e for e in events if e.features["liquidity_sweep"]]
    result = evaluate_hypothesis(
        hypothesis, treatment, labels, label_definition=FORWARD_RETURNS["1h"],
        sampling_policy=SamplingPolicy(), seed=1, resamples=300,
    )
    rendered = result.render()
    assert "CONCLUSION" in rendered
    assert "Baseline comparison" in rendered
    assert "profitable" not in rendered.lower()


def test_evaluation_is_deterministic(tmp_path):
    hypothesis, events, labels = synthetic_campaign(tmp_path, n=150, seed=8)
    treatment = [e for e in events if e.features["liquidity_sweep"]]
    kwargs = dict(label_definition=FORWARD_RETURNS["1h"],
                  sampling_policy=SamplingPolicy(), seed=3, resamples=300)
    first = evaluate_hypothesis(hypothesis, treatment, labels, **kwargs)
    second = evaluate_hypothesis(hypothesis, treatment, labels, **kwargs)
    assert first.raw.estimate == second.raw.estimate
    assert first.conclusion == second.conclusion


def test_regime_breakdown_flags_small_buckets(tmp_path):
    hypothesis, events, labels = synthetic_campaign(tmp_path, n=200, seed=10)
    treatment = [e for e in events if e.features["liquidity_sweep"]]
    result = evaluate_hypothesis(
        hypothesis, treatment, labels, label_definition=FORWARD_RETURNS["1h"],
        sampling_policy=SamplingPolicy(), seed=1, resamples=300,
    )
    assert result.regimes
    assert any(r.regime == "session" for r in result.regimes)
    for regime in result.regimes:
        assert regime.reliable == (regime.n >= 30)


def test_trial_count_is_reported(tmp_path):
    hypothesis, events, labels = synthetic_campaign(tmp_path, n=200, seed=11)
    treatment = [e for e in events if e.features["liquidity_sweep"]]
    result = evaluate_hypothesis(
        hypothesis, treatment, labels, label_definition=FORWARD_RETURNS["1h"],
        sampling_policy=SamplingPolicy(), n_trials=6, seed=1, resamples=300,
    )
    assert result.n_trials == 6
    assert any("6 hypotheses" in r for r in result.reasons)


def test_conclusion_vocabulary_excludes_profitable_strategy():
    values = {c.value for c in Conclusion}
    assert "PROFITABLE STRATEGY" not in values
    assert Conclusion.NO_EVIDENCE.value in values


# -- ICT vector temporal safety --------------------------------------------


def ohlcv_store(n=60, timeframe="1h"):
    store = InMemoryStore()
    for i in range(n):
        mid = 100 + 5 * np.sin(i / 3)
        store.append(bar(T0 + i * HOUR, mid, mid + 1.5, mid - 1.5, mid + 0.5,
                         timeframe=timeframe))
    return store


def test_ict_vector_components_are_feature_snapshots():
    store = ohlcv_store()
    vector = build_ict_vector(store, "ES", "1h", T0 + 40 * HOUR)
    for name, component in vector.components.items():
        assert hasattr(component, "available_at"), f"{name} is not a FeatureSnapshot"
        assert hasattr(component, "feature_version")


def test_ict_vector_availability_is_the_max_over_components():
    store = ohlcv_store()
    vector = build_ict_vector(store, "ES", "1h", T0 + 40 * HOUR)
    assert vector.available_at == max(c.available_at for c in vector.components.values())


def test_ict_vector_never_available_after_the_decision_time():
    """ATTACK: no component may require data from after the decision."""
    store = ohlcv_store()
    decision = T0 + 40 * HOUR
    vector = build_ict_vector(store, "ES", "1h", decision)
    for name, component in vector.components.items():
        if component.usable:
            assert component.available_at <= decision, f"{name} needs future data"


def test_future_bars_do_not_change_an_earlier_vector():
    """ATTACK: append bars after the decision; the vector must not move."""
    store = ohlcv_store(40)
    decision = T0 + 30 * HOUR
    before = build_ict_vector(store, "ES", "1h", decision).as_dict()

    for i in range(40, 60):
        store.append(bar(T0 + i * HOUR, 500, 520, 480, 510))
    after = build_ict_vector(store, "ES", "1h", decision).as_dict()
    assert before == after


def test_fvg_detection_uses_three_completed_bars():
    store = InMemoryStore()
    store.append(bar(T0, 100, 101, 99, 100))
    store.append(bar(T0 + HOUR, 103, 108, 102, 107))
    store.append(bar(T0 + 2 * HOUR, 108, 112, 105, 110))  # low 105 > bar1 high 101
    snapshot = detect_fvg(store, "ES", "1h", T0 + 3 * HOUR)
    assert snapshot.value is True


def test_labels_use_future_bars_but_features_never_do():
    """The separation that makes labels legitimate."""
    store = ohlcv_store(60)
    decision = T0 + 30 * HOUR
    vector = build_ict_vector(store, "ES", "1h", decision)
    future = [o for o in store._all() if o.event_time > decision]

    label = compute_forward_return(FORWARD_RETURNS["1h"], "ES", decision, 100.0, future)
    assert label.resolved
    assert label.resolved_at > decision          # outcome is in the future
    assert vector.available_at <= decision       # inputs are not
