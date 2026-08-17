"""The frozen ICT hypothesis family.

Two properties carry the phase: the family cannot be edited once locked, and no
feature available after a hypothesis's decision time may contribute to it.
"""

from datetime import datetime, timedelta, timezone

import pytest

from ai_trading.research.ict_family import (
    BASELINES,
    DISPLACEMENT_TO_FVG_MAX_BARS,
    EQUALITY_LOOKBACK_BARS,
    FAMILY_ID,
    FIXED_PARAMETERS,
    ICT_FAMILY_V1,
    LABEL_FAMILY,
    PROTOCOL_VERSION,
    SWEEP_TO_DISPLACEMENT_MAX_BARS,
    EventRole,
    FamilyLockError,
    FeatureEvent,
    Hypothesis,
    HypothesisFamily,
    TemporalLink,
    TemporalOrderError,
    build_family_v1,
)
from ai_trading.features.ict_objective import FEATURE_REGISTRY

UTC = timezone.utc
T0 = datetime(2026, 3, 2, 14, 30, tzinfo=UTC)

SWEEP = "liquidity_sweep:v1"
DISPLACEMENT = "displacement:v1"
FVG = "fvg:v1"


def a_hypothesis(**kw):
    defaults = dict(
        hypothesis_id="H1", statement="a question", parent_id=None,
        events=(FeatureEvent(SWEEP, EventRole.TRIGGER),),
        temporal_relationships=(), decision_event=SWEEP,
        label_versions=("forward_return_15m",), cost_model="realistic",
        sampling_policy_version="1",
    )
    return Hypothesis(**{**defaults, **kw})


# =========================================================================
# Family composition
# =========================================================================


def test_the_family_has_exactly_six_hypotheses():
    assert len(ICT_FAMILY_V1.all()) == 6
    assert {h.hypothesis_id for h in ICT_FAMILY_V1.all()} == {
        "ICT-LS-001", "ICT-LS-002", "ICT-FVG-001", "ICT-COMBO-001",
        "ICT-EQ-001", "ICT-COMBO-002",
    }


def test_the_family_uses_only_the_five_implemented_features():
    assert ICT_FAMILY_V1.feature_versions() == [
        "displacement:v1", "equal_high:v1", "equal_low:v1", "fvg:v1",
        "liquidity_sweep:v1",
    ]


def test_every_feature_the_family_names_is_registered():
    for key in ICT_FAMILY_V1.feature_versions():
        assert FEATURE_REGISTRY.require(key) is not None


def test_no_deferred_concept_appears_in_the_family():
    forbidden = ("order_block", "mss", "bos", "choch", "protected", "premium",
                 "discount", "equilibrium", "breaker", "killzone", "smt",
                 "inducement")
    for key in ICT_FAMILY_V1.feature_versions():
        assert not any(term in key.lower() for term in forbidden)


def test_no_additional_indicators_are_introduced():
    assert len(ICT_FAMILY_V1.feature_versions()) == 5


def test_the_family_cites_the_frozen_protocol():
    assert ICT_FAMILY_V1.protocol_version == PROTOCOL_VERSION == "research-protocol-v1"
    for hypothesis in ICT_FAMILY_V1.all():
        assert hypothesis.protocol_version == PROTOCOL_VERSION


def test_every_hypothesis_carries_the_required_fields():
    for hypothesis in ICT_FAMILY_V1.all():
        payload = hypothesis.to_dict()
        for name in ("hypothesis_id", "parent_id", "feature_versions",
                     "conditions", "temporal_relationships", "decision_event",
                     "label_version", "cost_model", "sampling_policy",
                     "protocol_version", "creation_commit"):
            assert name in payload, name


def test_a_creation_commit_is_recorded():
    for hypothesis in ICT_FAMILY_V1.all():
        assert hypothesis.creation_commit


def test_no_hypothesis_is_a_signal():
    for hypothesis in ICT_FAMILY_V1.all():
        assert not hypothesis.is_signal
        payload = hypothesis.to_dict()
        for forbidden in ("side", "entry", "stop", "target", "size", "action"):
            assert forbidden not in payload


# =========================================================================
# Immutability / locking
# =========================================================================


def test_the_family_is_locked_on_construction():
    assert ICT_FAMILY_V1.is_locked


def test_a_locked_family_refuses_additions():
    with pytest.raises(FamilyLockError, match="is locked"):
        ICT_FAMILY_V1.add(a_hypothesis(hypothesis_id="ICT-EXTRA-001"))


def test_the_refusal_explains_the_trial_count_consequence():
    with pytest.raises(FamilyLockError, match="trial count"):
        ICT_FAMILY_V1.add(a_hypothesis(hypothesis_id="ICT-EXTRA-002"))


def test_there_is_no_unlock():
    """The only thing anyone would ever reach for, so it does not exist."""
    for forbidden in ("unlock", "reopen", "edit", "remove", "delete"):
        assert not hasattr(HypothesisFamily, forbidden)


def test_an_empty_family_cannot_be_locked():
    with pytest.raises(FamilyLockError, match="empty family"):
        HypothesisFamily("x", "v1").lock()


def test_a_duplicate_hypothesis_id_is_refused():
    family = HypothesisFamily("f", "v1")
    family.add(a_hypothesis())
    with pytest.raises(FamilyLockError, match="already in this family"):
        family.add(a_hypothesis())


def test_rebuilding_produces_an_identical_fingerprint():
    """A locked family is reproducible from its definition."""
    assert build_family_v1().fingerprint == ICT_FAMILY_V1.fingerprint


def test_a_changed_hypothesis_changes_its_fingerprint():
    base = a_hypothesis()
    changed = a_hypothesis(label_versions=("forward_return_1h",))
    assert base.fingerprint != changed.fingerprint


def test_changing_a_window_would_change_the_family_fingerprint():
    """Windows are hypothesis parameters, so a change is a new family."""
    family = HypothesisFamily("f", "v1")
    family.add(a_hypothesis(
        events=(FeatureEvent(SWEEP, EventRole.TRIGGER),
                FeatureEvent(DISPLACEMENT, EventRole.SEQUENCE)),
        temporal_relationships=(TemporalLink(SWEEP, DISPLACEMENT, 3),),
        decision_event=DISPLACEMENT))
    narrow = family.fingerprint

    other = HypothesisFamily("f", "v1")
    other.add(a_hypothesis(
        events=(FeatureEvent(SWEEP, EventRole.TRIGGER),
                FeatureEvent(DISPLACEMENT, EventRole.SEQUENCE)),
        temporal_relationships=(TemporalLink(SWEEP, DISPLACEMENT, 5),),
        decision_event=DISPLACEMENT))
    assert other.fingerprint != narrow


# =========================================================================
# Temporal ordering
# =========================================================================


def test_a_conjunction_without_an_ordering_is_refused():
    """'Sweep then displacement' is a different claim from the reverse."""
    with pytest.raises(TemporalOrderError, match="unordered set"):
        a_hypothesis(
            events=(FeatureEvent(SWEEP, EventRole.TRIGGER),
                    FeatureEvent(DISPLACEMENT, EventRole.SEQUENCE)),
            temporal_relationships=(), decision_event=DISPLACEMENT)


def test_every_multi_event_hypothesis_declares_an_ordering():
    for hypothesis in ICT_FAMILY_V1.all():
        sequenced = [e for e in hypothesis.events
                     if e.role in (EventRole.TRIGGER, EventRole.SEQUENCE)]
        if len(sequenced) > 1:
            assert hypothesis.temporal_relationships


def test_a_link_must_reference_the_hypothesis_own_events():
    with pytest.raises(TemporalOrderError, match="not one of this hypothesis"):
        a_hypothesis(
            events=(FeatureEvent(SWEEP, EventRole.TRIGGER),),
            temporal_relationships=(TemporalLink(SWEEP, FVG, 2),))


def test_a_negative_window_is_refused():
    with pytest.raises(TemporalOrderError, match="cannot be negative"):
        TemporalLink(SWEEP, DISPLACEMENT, -1)


def test_a_max_below_min_is_refused():
    with pytest.raises(TemporalOrderError, match="below min_bars"):
        TemporalLink(SWEEP, DISPLACEMENT, max_bars=1, min_bars=3)


def test_a_link_permits_only_forward_ordering():
    link = TemporalLink(SWEEP, DISPLACEMENT, max_bars=3)
    assert link.permits(10, 10)          # same bar allowed
    assert link.permits(10, 13)
    assert not link.permits(10, 14)      # beyond the window
    assert not link.permits(10, 9)       # backwards


def test_an_observed_chain_out_of_order_is_rejected():
    hypothesis = ICT_FAMILY_V1.require("ICT-COMBO-001")
    with pytest.raises(TemporalOrderError, match="violated"):
        hypothesis.validate_ordering(
            {SWEEP: 100, DISPLACEMENT: 95, FVG: 97})   # displacement first


def test_an_observed_chain_beyond_the_window_is_rejected():
    hypothesis = ICT_FAMILY_V1.require("ICT-COMBO-001")
    with pytest.raises(TemporalOrderError, match="violated"):
        hypothesis.validate_ordering(
            {SWEEP: 100, DISPLACEMENT: 110, FVG: 111})  # 10 bars, max is 3


def test_a_valid_chain_passes():
    hypothesis = ICT_FAMILY_V1.require("ICT-COMBO-001")
    hypothesis.validate_ordering({SWEEP: 100, DISPLACEMENT: 102, FVG: 103})


# =========================================================================
# Event windows
# =========================================================================


def test_the_declared_windows_are_the_pre_registered_values():
    combo = ICT_FAMILY_V1.require("ICT-COMBO-001")
    links = {(l.from_feature, l.to_feature): l.max_bars
             for l in combo.temporal_relationships}
    assert links[(SWEEP, DISPLACEMENT)] == SWEEP_TO_DISPLACEMENT_MAX_BARS == 3
    assert links[(DISPLACEMENT, FVG)] == DISPLACEMENT_TO_FVG_MAX_BARS == 2


def test_the_equality_context_window_is_declared():
    equality = ICT_FAMILY_V1.require("ICT-EQ-001")
    for link in equality.temporal_relationships:
        assert link.max_bars == EQUALITY_LOOKBACK_BARS == 50


def test_windows_are_recorded_in_the_fixed_parameters():
    assert FIXED_PARAMETERS["sweep_to_displacement_max_bars"] == 3
    assert FIXED_PARAMETERS["displacement_to_fvg_max_bars"] == 2
    assert FIXED_PARAMETERS["equality_lookback_bars"] == 50


def test_the_builder_accepts_no_parameters():
    """A build function with a threshold argument is a sweep waiting to happen."""
    import inspect

    assert list(inspect.signature(build_family_v1).parameters) == []


# =========================================================================
# Decision event and the feature-after-decision guard
# =========================================================================


def test_every_hypothesis_names_a_decision_event():
    for hypothesis in ICT_FAMILY_V1.all():
        assert hypothesis.decision_event in hypothesis.feature_versions


def test_a_decision_event_outside_the_hypothesis_is_refused():
    with pytest.raises(TemporalOrderError, match="not among the hypothesis"):
        a_hypothesis(decision_event=FVG)


def test_an_fvg_based_setup_decides_at_the_fvg_availability():
    for hid in ("ICT-FVG-001", "ICT-COMBO-001", "ICT-COMBO-002"):
        assert ICT_FAMILY_V1.require(hid).decision_event == FVG


def test_the_decision_time_is_the_decision_events_availability():
    hypothesis = ICT_FAMILY_V1.require("ICT-COMBO-001")
    availabilities = {SWEEP: T0, DISPLACEMENT: T0 + timedelta(minutes=2),
                      FVG: T0 + timedelta(minutes=4)}
    assert hypothesis.decision_time(availabilities) == T0 + timedelta(minutes=4)


def test_a_feature_available_after_the_decision_is_refused():
    """The core guard: a late feature is an outcome, never an input."""
    hypothesis = ICT_FAMILY_V1.require("ICT-LS-002")
    with pytest.raises(TemporalOrderError, match="may only be an outcome label"):
        hypothesis.validate_contribution({
            SWEEP: T0 + timedelta(minutes=10),      # after the decision
            DISPLACEMENT: T0,                        # the decision event
        })


def test_features_available_at_or_before_the_decision_pass():
    hypothesis = ICT_FAMILY_V1.require("ICT-COMBO-001")
    hypothesis.validate_contribution({
        SWEEP: T0, DISPLACEMENT: T0 + timedelta(minutes=2),
        FVG: T0 + timedelta(minutes=4),
    })


def test_a_missing_decision_availability_is_refused():
    hypothesis = ICT_FAMILY_V1.require("ICT-LS-002")
    with pytest.raises(TemporalOrderError, match="no availability supplied"):
        hypothesis.decision_time({SWEEP: T0})


def test_the_equality_context_decides_at_the_sweep_not_the_equality():
    """Context precedes the trigger, so the trigger fixes the decision."""
    equality = ICT_FAMILY_V1.require("ICT-EQ-001")
    assert equality.decision_event == SWEEP


# =========================================================================
# Nesting and incremental comparison
# =========================================================================


def test_the_family_has_exactly_one_root():
    assert [h.hypothesis_id for h in ICT_FAMILY_V1.roots()] == ["ICT-LS-001"]


def test_every_non_root_names_a_parent_in_the_family():
    for hypothesis in ICT_FAMILY_V1.all():
        if hypothesis.parent_id is not None:
            assert ICT_FAMILY_V1.get(hypothesis.parent_id) is not None


def test_a_parent_must_already_exist_when_a_child_is_added():
    family = HypothesisFamily("f", "v1")
    with pytest.raises(TemporalOrderError, match="not in the family"):
        family.add(a_hypothesis(hypothesis_id="child", parent_id="absent"))


def test_the_full_conjunction_has_a_measurable_lineage():
    chain = [h.hypothesis_id for h in ICT_FAMILY_V1.lineage("ICT-COMBO-002")]
    assert chain == ["ICT-LS-001", "ICT-LS-002", "ICT-COMBO-001",
                     "ICT-COMBO-002"]


def test_each_step_of_the_lineage_adds_conditions():
    chain = ICT_FAMILY_V1.lineage("ICT-COMBO-002")
    counts = [h.condition_count for h in chain]
    assert counts == sorted(counts)
    assert counts[0] < counts[-1]


def test_every_child_is_reported_against_its_parent():
    comparisons = set(ICT_FAMILY_V1.incremental_comparisons())
    assert ("ICT-LS-001", "ICT-LS-002") in comparisons
    assert ("ICT-LS-002", "ICT-COMBO-001") in comparisons
    assert ("ICT-COMBO-001", "ICT-COMBO-002") in comparisons
    assert len(comparisons) == 5      # every non-root


def test_children_of_a_hypothesis_are_listed():
    children = {h.hypothesis_id for h in ICT_FAMILY_V1.children_of("ICT-LS-001")}
    assert children == {"ICT-LS-002", "ICT-FVG-001", "ICT-EQ-001"}


# =========================================================================
# Labels
# =========================================================================


def test_the_label_family_is_pre_registered():
    names = [l.name for l in LABEL_FAMILY]
    assert names == ["forward_return_5m", "forward_return_15m",
                     "forward_return_30m", "forward_return_1h",
                     "hit_1R_before_-1R", "hit_2R_before_-1R"]


def test_every_hypothesis_uses_the_whole_label_family():
    expected = tuple(l.name for l in LABEL_FAMILY)
    for hypothesis in ICT_FAMILY_V1.all():
        assert hypothesis.label_versions == expected


def test_labels_use_the_existing_definitions_unmodified():
    from ai_trading.research.labels import FORWARD_RETURNS, R_LABELS

    assert FORWARD_RETURNS["15m"] in LABEL_FAMILY
    assert R_LABELS["hit_2R"] in LABEL_FAMILY


def test_excursion_diagnostics_are_separate_from_labels():
    """MAE and MFE come back with the R-multiple, not as extra trials."""
    from ai_trading.research.ict_family import EXCURSION_DIAGNOSTICS

    assert EXCURSION_DIAGNOSTICS == ("mae_r", "mfe_r")
    assert not set(EXCURSION_DIAGNOSTICS) & {l.name for l in LABEL_FAMILY}


def test_r_labels_resolve_the_stop_pessimistically():
    from ai_trading.research.labels import R_LABELS

    assert R_LABELS["hit_1R"].tie_policy == "stop"


# =========================================================================
# Trial count and baselines
# =========================================================================


def test_the_trial_count_is_hypotheses_times_labels():
    assert ICT_FAMILY_V1.trial_count == 6 * 6 == 36


def test_the_trial_count_counts_labels_as_separate_looks():
    """Six outcomes is six looks at the data, not one."""
    hypothesis = ICT_FAMILY_V1.require("ICT-LS-001")
    assert hypothesis.trial_count == len(LABEL_FAMILY) == 6


def test_the_four_baselines_are_declared():
    assert BASELINES == ("random", "hold_matched_random", "momentum",
                         "mean_reversion")
    assert ICT_FAMILY_V1.baselines == BASELINES


def test_every_hypothesis_is_compared_against_every_baseline():
    assert ICT_FAMILY_V1.baseline_comparison_count == 6 * 4 == 24


# =========================================================================
# Fixed parameters, no sweep
# =========================================================================


@pytest.mark.parametrize(("name", "value"), [
    ("fvg_min_size_atr", 0.2),
    ("displacement_threshold_atr", 2.0),
    ("equal_tolerance_atr", 0.1),
    ("equal_min_separation_bars", 3),
])
def test_the_v1_parameters_are_fixed(name, value):
    assert FIXED_PARAMETERS[name] == value


def test_the_family_parameters_match_the_feature_registry():
    """The family record must not drift from the implementation."""
    assert (FEATURE_REGISTRY.require("fvg:v1").parameters["min_size_atr"]
            == FIXED_PARAMETERS["fvg_min_size_atr"])
    assert (FEATURE_REGISTRY.require("displacement:v1").parameters["atr_multiple"]
            == FIXED_PARAMETERS["displacement_threshold_atr"])
    assert (FEATURE_REGISTRY.require("equal_high:v1").parameters["tolerance_atr"]
            == FIXED_PARAMETERS["equal_tolerance_atr"])


def test_the_family_declares_a_single_cost_model():
    models = {h.cost_model for h in ICT_FAMILY_V1.all()}
    assert models == {"realistic"}


# =========================================================================
# Sampling and overlap
# =========================================================================


def test_the_family_declares_a_sampling_policy():
    policy = ICT_FAMILY_V1.sampling_policy
    assert policy is not None
    assert policy.deduplicate_overlaps


def test_overlapping_label_windows_are_not_independent():
    """Events closer than one label horizon share outcome bars."""
    policy = ICT_FAMILY_V1.sampling_policy
    assert policy.effective_spacing >= policy.label_horizon


def test_the_label_horizon_covers_the_longest_label():
    policy = ICT_FAMILY_V1.sampling_policy
    longest = max(l.horizon for l in LABEL_FAMILY)
    assert policy.label_horizon >= longest


def test_the_sampling_policy_version_is_cited_by_every_hypothesis():
    version = ICT_FAMILY_V1.sampling_policy.version
    for hypothesis in ICT_FAMILY_V1.all():
        assert hypothesis.sampling_policy_version == version


def test_overlapping_events_are_deduplicated():
    from ai_trading.research.sampling import Event, apply_sampling

    policy = ICT_FAMILY_V1.sampling_policy
    events = [Event("NQ", T0 + timedelta(minutes=30 * i), {}) for i in range(6)]
    kept = apply_sampling(events, policy)
    assert len(kept) < len(events)


# =========================================================================
# The data gate
# =========================================================================


def test_the_family_exists_without_real_data():
    assert ICT_FAMILY_V1.is_locked
    assert ICT_FAMILY_V1.trial_count == 36


def test_execution_is_refused_without_market_claim_allowed():
    class Dataset:
        grades = None

    with pytest.raises(PermissionError, match="MARKET_CLAIM_ALLOWED not granted"):
        ICT_FAMILY_V1.require_market_claim_allowed(Dataset())


def test_execution_is_refused_on_a_research_grade_synthetic_dataset():
    """Research grade is not enough; the origin must be a real market."""
    from ai_trading.history import DataOrigin, assess_grades, run_quality_gate
    from ai_trading.history.providers import SCHEMA_VERSION, Bar

    bars = [
        Bar(source="fixture",
            event_time=T0 + timedelta(minutes=i),
            available_at=T0 + timedelta(minutes=i),
            retrieved_at=T0, schema_version=SCHEMA_VERSION, instrument="NQ",
            contract="NQM26", timeframe="1m", open=20_000.0, high=20_010.0,
            low=19_990.0, close=20_005.0, volume=100.0)
        for i in range(30)
    ]
    report = run_quality_gate(bars, provider="fixture")

    class Dataset:
        grades = assess_grades(source_name="fixture",
                               origin=DataOrigin.SYNTHETIC,
                               quality_report=report, point_in_time_clean=True)

    assert Dataset.grades.permits_research
    with pytest.raises(PermissionError, match="MARKET_CLAIM_ALLOWED"):
        ICT_FAMILY_V1.require_market_claim_allowed(Dataset())


def test_execution_is_permitted_once_the_gate_opens():
    from ai_trading.history import DataOrigin, assess_grades, run_quality_gate
    from ai_trading.history.providers import SCHEMA_VERSION, Bar

    bars = [
        Bar(source="databento",
            event_time=T0 + timedelta(minutes=i),
            available_at=T0 + timedelta(minutes=i),
            retrieved_at=T0, schema_version=SCHEMA_VERSION, instrument="NQ",
            contract="NQM26", timeframe="1m", open=20_000.0, high=20_010.0,
            low=19_990.0, close=20_005.0, volume=100.0)
        for i in range(30)
    ]
    report = run_quality_gate(bars, provider="databento")

    class Dataset:
        grades = assess_grades(source_name="databento",
                               origin=DataOrigin.REAL_MARKET,
                               quality_report=report, point_in_time_clean=True)

    ICT_FAMILY_V1.require_market_claim_allowed(Dataset())


# =========================================================================
# Serialization
# =========================================================================


def test_the_family_serializes_completely():
    payload = ICT_FAMILY_V1.to_dict()
    for name in ("family_id", "version", "protocol_version", "is_locked",
                 "fingerprint", "trial_count", "feature_versions", "labels",
                 "baselines", "fixed_parameters", "sampling_policy",
                 "incremental_comparisons", "creation_commit", "hypotheses"):
        assert name in payload
    assert payload["family_id"] == FAMILY_ID
    assert payload["is_locked"] is True
    assert len(payload["hypotheses"]) == 6


def test_an_unversioned_feature_key_is_refused():
    with pytest.raises(ValueError, match="must name a version"):
        FeatureEvent("liquidity_sweep", EventRole.TRIGGER)
