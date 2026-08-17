"""``ICT-FAMILY-V1`` cannot change without becoming a different version.

The tests that matter here are the ones comparing the *built* family against
*literals*. A test that recomputes the expected value from the family it is
checking passes no matter what the family says, which is how a frozen record
quietly stops being frozen.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from ai_trading.research.ict_family import ICT_FAMILY_V1, build_family_v1
from ai_trading.research.ict_freeze import (
    FAMILY_LABEL,
    FROZEN_BASELINES,
    FROZEN_DECISION_EVENTS,
    FROZEN_FEATURE_VERSIONS,
    FROZEN_FINGERPRINT,
    FROZEN_HYPOTHESIS_FINGERPRINTS,
    FROZEN_LABELS,
    FROZEN_ON,
    FROZEN_PARAMETERS,
    FROZEN_TRIAL_COUNT,
    FROZEN_WINDOWS,
    NEXT_PERMITTED_ACTION,
    PROHIBITED_ACTIONS,
    FamilyDriftError,
    FamilyStatus,
    FamilySupersession,
    ProhibitedActionError,
    SupersessionRefused,
    family_status,
    freeze_record,
    require_action_permitted,
    verify_frozen,
)

UTC = timezone.utc
T0 = datetime(2026, 3, 2, 14, 30, tzinfo=UTC)


# =========================================================================
# The fingerprint is pinned to a literal
# =========================================================================


def test_the_family_fingerprint_is_the_frozen_one():
    """The single regression that catches every definitional change."""
    assert ICT_FAMILY_V1.fingerprint == "b3ebb0af7f01b137"
    assert FROZEN_FINGERPRINT == "b3ebb0af7f01b137"


def test_verify_frozen_passes_on_the_shipped_family():
    assert verify_frozen() == FROZEN_FINGERPRINT


def test_verify_frozen_passes_on_a_freshly_built_family():
    """The fingerprint is a property of the definition, not of one instance."""
    assert verify_frozen(build_family_v1()) == FROZEN_FINGERPRINT


def test_rebuilding_reproduces_the_same_fingerprint():
    assert build_family_v1().fingerprint == build_family_v1().fingerprint


def test_the_freeze_date_is_recorded():
    assert FROZEN_ON == date(2026, 8, 17)


def test_the_label_is_the_one_declared():
    assert FAMILY_LABEL == "ICT-FAMILY-V1"


# =========================================================================
# Every frozen component, against a literal
# =========================================================================


def test_the_trial_count_is_thirty_six():
    assert ICT_FAMILY_V1.trial_count == 36
    assert FROZEN_TRIAL_COUNT == 36


def test_the_hypothesis_ids_are_the_six_declared():
    assert sorted(h.hypothesis_id for h in ICT_FAMILY_V1.all()) == [
        "ICT-COMBO-001", "ICT-COMBO-002", "ICT-EQ-001",
        "ICT-FVG-001", "ICT-LS-001", "ICT-LS-002",
    ]


@pytest.mark.parametrize(("hid", "fingerprint"),
                         sorted(FROZEN_HYPOTHESIS_FINGERPRINTS.items()))
def test_each_hypothesis_keeps_its_own_fingerprint(hid, fingerprint):
    """Per-hypothesis, so a drift report names the one that moved."""
    assert ICT_FAMILY_V1.require(hid).fingerprint == fingerprint


@pytest.mark.parametrize(("hid", "decision"),
                         sorted(FROZEN_DECISION_EVENTS.items()))
def test_each_decision_event_is_frozen(hid, decision):
    assert ICT_FAMILY_V1.require(hid).decision_event == decision


def test_the_labels_are_the_six_declared():
    assert tuple(label.name for label in ICT_FAMILY_V1.label_family) == (
        "forward_return_5m", "forward_return_15m", "forward_return_30m",
        "forward_return_1h", "hit_1R_before_-1R", "hit_2R_before_-1R")
    assert FROZEN_LABELS == tuple(
        label.name for label in ICT_FAMILY_V1.label_family)


def test_the_feature_versions_are_the_five_objective_ones():
    assert tuple(ICT_FAMILY_V1.feature_versions()) == (
        "displacement:v1", "equal_high:v1", "equal_low:v1", "fvg:v1",
        "liquidity_sweep:v1")
    assert FROZEN_FEATURE_VERSIONS == tuple(ICT_FAMILY_V1.feature_versions())


def test_the_baselines_are_the_four_declared_in_order():
    assert tuple(ICT_FAMILY_V1.baselines) == (
        "random", "hold_matched_random", "momentum", "mean_reversion")
    assert FROZEN_BASELINES == tuple(ICT_FAMILY_V1.baselines)


@pytest.mark.parametrize(("name", "value"), sorted(FROZEN_PARAMETERS.items()))
def test_each_fixed_parameter_keeps_its_value(name, value):
    assert ICT_FAMILY_V1.fixed_parameters[name] == value


def test_no_parameter_was_added_or_removed():
    assert set(ICT_FAMILY_V1.fixed_parameters) == set(FROZEN_PARAMETERS)


@pytest.mark.parametrize(("name", "bars"), sorted(FROZEN_WINDOWS.items()))
def test_each_temporal_window_is_frozen(name, bars):
    """Read off the links, not the parameter block -- both must agree."""
    from ai_trading.research.ict_freeze import _declared_windows

    assert _declared_windows(ICT_FAMILY_V1)[name] == {bars}


def test_a_window_widened_in_one_hypothesis_only_is_still_caught():
    """The same window appears in several hypotheses; one is enough."""
    from dataclasses import replace

    from ai_trading.research.ict_family import TemporalLink
    from ai_trading.research.ict_freeze import _declared_windows

    family = build_family_v1()
    original = family.hypotheses["ICT-COMBO-001"]
    family.hypotheses["ICT-COMBO-001"] = replace(original, temporal_relationships=(
        TemporalLink("liquidity_sweep:v1", "displacement:v1", 8),
        TemporalLink("displacement:v1", "fvg:v1", 2)))

    assert _declared_windows(family)["sweep_to_displacement"] == {3, 8}
    with pytest.raises(FamilyDriftError, match="window sweep_to_displacement"):
        verify_frozen(family)


def test_the_windows_and_the_parameter_block_agree():
    params = ICT_FAMILY_V1.fixed_parameters
    assert FROZEN_WINDOWS["sweep_to_displacement"] == \
        params["sweep_to_displacement_max_bars"]
    assert FROZEN_WINDOWS["displacement_to_fvg"] == \
        params["displacement_to_fvg_max_bars"]
    assert FROZEN_WINDOWS["equality_to_sweep"] == params["equality_lookback_bars"]
    assert FROZEN_WINDOWS["sweep_to_fvg"] == (
        params["sweep_to_displacement_max_bars"]
        + params["displacement_to_fvg_max_bars"])


def test_the_nesting_is_frozen():
    parents = {h.hypothesis_id: h.parent_id for h in ICT_FAMILY_V1.all()}
    assert parents == {
        "ICT-LS-001": None,
        "ICT-LS-002": "ICT-LS-001",
        "ICT-FVG-001": "ICT-LS-001",
        "ICT-COMBO-001": "ICT-LS-002",
        "ICT-EQ-001": "ICT-LS-001",
        "ICT-COMBO-002": "ICT-COMBO-001",
    }


def test_the_baseline_comparison_count_is_frozen():
    assert ICT_FAMILY_V1.baseline_comparison_count == 24


# =========================================================================
# Drift detection -- each mutation is caught
# =========================================================================


def test_an_added_hypothesis_is_caught():
    from ai_trading.research.ict_family import (
        EventRole, FeatureEvent, Hypothesis, HypothesisFamily,
    )

    family = build_family_v1()
    family._locked = False          # simulate an edit to the source
    family.add(Hypothesis(
        hypothesis_id="ICT-EXTRA-001", statement="an unregistered idea",
        parent_id="ICT-LS-001",
        events=(FeatureEvent("fvg:v1", EventRole.TRIGGER),),
        temporal_relationships=(), decision_event="fvg:v1",
        label_versions=FROZEN_LABELS, cost_model="realistic",
        sampling_policy_version="1"))
    family.lock()

    with pytest.raises(FamilyDriftError, match="ICT-EXTRA-001"):
        verify_frozen(family)
    assert isinstance(family, HypothesisFamily)


def test_a_removed_hypothesis_is_caught():
    family = build_family_v1()
    family._locked = False
    del family.hypotheses["ICT-COMBO-002"]
    family.lock()

    with pytest.raises(FamilyDriftError, match="removed"):
        verify_frozen(family)


def test_a_retuned_threshold_is_caught():
    family = build_family_v1()
    family.fixed_parameters["displacement_threshold_atr"] = 1.5

    with pytest.raises(FamilyDriftError,
                       match="parameter displacement_threshold_atr"):
        verify_frozen(family)


def test_a_widened_event_window_is_caught():
    """The change most likely to be argued for once results disappoint."""
    from ai_trading.research.ict_family import TemporalLink
    from dataclasses import replace

    family = build_family_v1()
    original = family.hypotheses["ICT-LS-002"]
    widened = replace(original, temporal_relationships=(
        TemporalLink("liquidity_sweep:v1", "displacement:v1", 10),))
    family.hypotheses["ICT-LS-002"] = widened

    with pytest.raises(FamilyDriftError,
                       match="window sweep_to_displacement"):
        verify_frozen(family)


def test_a_moved_decision_event_is_caught():
    from dataclasses import replace

    family = build_family_v1()
    original = family.hypotheses["ICT-LS-002"]
    family.hypotheses["ICT-LS-002"] = replace(original,
                                              decision_event="liquidity_sweep:v1")

    with pytest.raises(FamilyDriftError, match="decision event"):
        verify_frozen(family)


def test_a_dropped_baseline_is_caught():
    family = build_family_v1()
    family.baselines = ("random", "momentum")

    with pytest.raises(FamilyDriftError, match="baselines"):
        verify_frozen(family)


def test_an_added_label_is_caught():
    from ai_trading.research.labels import FORWARD_RETURNS

    family = build_family_v1()
    family.label_family = family.label_family + (FORWARD_RETURNS["4h"],)

    with pytest.raises(FamilyDriftError, match="labels"):
        verify_frozen(family)


def test_an_edited_statement_is_caught_by_the_fingerprint():
    """No field of a hypothesis is outside the hash."""
    from dataclasses import replace

    family = build_family_v1()
    original = family.hypotheses["ICT-LS-001"]
    family.hypotheses["ICT-LS-001"] = replace(
        original, statement="Forward outcomes following a sweep are positive.")

    with pytest.raises(FamilyDriftError, match="ICT-LS-001 fingerprint"):
        verify_frozen(family)


def test_an_unlocked_family_is_caught():
    family = build_family_v1()
    family._locked = False

    with pytest.raises(FamilyDriftError, match="not locked"):
        verify_frozen(family)


def test_the_drift_message_names_the_remedy():
    """A failing freeze test is a version bump, not a value to paste over."""
    family = build_family_v1()
    family.fixed_parameters["atr_period"] = 20

    with pytest.raises(FamilyDriftError) as caught:
        verify_frozen(family)
    message = str(caught.value)
    assert "ICT-FAMILY-V2" in message
    assert "FamilySupersession" in message
    assert "permanently frozen" in message


def test_drift_reports_every_difference_at_once():
    family = build_family_v1()
    family.fixed_parameters["atr_period"] = 20
    family.baselines = ("random",)

    with pytest.raises(FamilyDriftError) as caught:
        verify_frozen(family)
    message = str(caught.value)
    assert "parameter atr_period" in message
    assert "baselines" in message
    assert "fingerprint" in message


# =========================================================================
# Status: REAL_DATA_PENDING until the gate opens
# =========================================================================


def test_the_family_is_real_data_pending():
    assert family_status() is FamilyStatus.REAL_DATA_PENDING
    assert not family_status().permits_execution


def test_status_stays_pending_for_a_research_grade_synthetic_dataset():
    """Calibration is not progress toward a market claim."""
    from ai_trading.history import DataOrigin, assess_grades, run_quality_gate
    from ai_trading.history.providers import SCHEMA_VERSION, Bar

    bars = [
        Bar(source="fixture", event_time=T0 + timedelta(minutes=i),
            available_at=T0 + timedelta(minutes=i), retrieved_at=T0,
            schema_version=SCHEMA_VERSION, instrument="NQ", contract="NQM26",
            timeframe="1m", open=20_000.0, high=20_010.0, low=19_990.0,
            close=20_005.0, volume=100.0)
        for i in range(30)
    ]
    report = run_quality_gate(bars, provider="fixture")

    class Dataset:
        origin = DataOrigin.SYNTHETIC
        grades = assess_grades(source_name="fixture",
                               origin=DataOrigin.SYNTHETIC,
                               quality_report=report, point_in_time_clean=True)

    assert Dataset.grades.permits_research
    assert family_status(dataset=Dataset()) is FamilyStatus.REAL_DATA_PENDING


def test_status_advances_only_on_an_approved_real_dataset():
    from ai_trading.history import DataOrigin, assess_grades, run_quality_gate
    from ai_trading.history.providers import SCHEMA_VERSION, Bar

    bars = [
        Bar(source="databento", event_time=T0 + timedelta(minutes=i),
            available_at=T0 + timedelta(minutes=i), retrieved_at=T0,
            schema_version=SCHEMA_VERSION, instrument="NQ", contract="NQM26",
            timeframe="1m", open=20_000.0, high=20_010.0, low=19_990.0,
            close=20_005.0, volume=100.0)
        for i in range(30)
    ]
    report = run_quality_gate(bars, provider="databento")

    class Dataset:
        origin = DataOrigin.REAL_MARKET
        grades = assess_grades(source_name="databento",
                               origin=DataOrigin.REAL_MARKET,
                               quality_report=report, point_in_time_clean=True)

    status = family_status(dataset=Dataset())
    assert status is FamilyStatus.APPROVED_FOR_REAL_DATA
    assert status.permits_execution


def test_a_derived_dataset_is_refused_by_origin_directly():
    """A second refusal that does not depend on the grade ladder."""
    from ai_trading.history import DataOrigin

    class Dataset:
        origin = DataOrigin.DERIVED
        grades = None

    with pytest.raises(PermissionError, match="describe the generator"):
        ICT_FAMILY_V1.require_market_claim_allowed(Dataset())


def test_there_is_no_status_reachable_by_synthetic_validation():
    values = [s.value for s in FamilyStatus]
    assert values == ["real_data_pending", "approved_for_real_data",
                      "superseded"]
    for absent in ("calibrated", "synthetic_validated", "partially_run"):
        assert absent not in values


# =========================================================================
# Prohibitions and the next permitted action
# =========================================================================


@pytest.mark.parametrize("action", sorted(PROHIBITED_ACTIONS))
def test_each_prohibited_action_is_refused(action):
    with pytest.raises(ProhibitedActionError):
        require_action_permitted(action)


def test_a_refusal_states_the_reason_and_the_permitted_action():
    with pytest.raises(ProhibitedActionError) as caught:
        require_action_permitted("tune_event_windows")
    message = str(caught.value)
    assert "pre-registered values" in message
    assert NEXT_PERMITTED_ACTION in message


def test_the_prohibitions_cover_what_was_declared():
    for action in ("run_on_synthetic_for_evidence",
                   "use_openmobius_cases_as_evidence", "alter_definitions",
                   "tune_thresholds", "tune_event_windows", "add_features",
                   "expand_family", "reorder_family", "spend_holdout",
                   "create_trade_signals",
                   "optimize_for_topstep_pass_probability"):
        assert action in PROHIBITED_ACTIONS


def test_the_next_permitted_action_is_the_single_declared_one():
    assert NEXT_PERMITTED_ACTION == (
        "run ICT-FAMILY-V1 against the first approved real NQ dataset under "
        "research-protocol-v1")


def test_an_unrecognised_action_is_not_silently_authorised():
    """It passes, and the docstring says why -- this is not an allowlist."""
    require_action_permitted("read_the_documentation")
    assert "allowlist" in require_action_permitted.__doc__


def test_no_hypothesis_is_a_signal():
    assert all(not h.is_signal for h in ICT_FAMILY_V1.all())


# =========================================================================
# Supersession -- the only route to a change
# =========================================================================


def a_supersession(**kw):
    defaults = dict(
        family_id="ict-objective-family-v2", version="v2",
        fingerprint="0000000000000000",
        protocol_version="research-protocol-v2", trial_count=42,
        supersedes_fingerprint=FROZEN_FINGERPRINT,
        change_summary="Widened the sweep-to-displacement window from 3 to 5 "
                       "bars and added a seventh hypothesis.",
        reason="Bar-gap distribution on the first approved NQ dataset showed "
               "the 3-bar window truncating the chain.",
    )
    return FamilySupersession(**{**defaults, **kw})


def test_a_complete_supersession_is_accepted():
    record = a_supersession()
    assert record.version == "v2"
    assert record.superseded_label == "ICT-FAMILY-V1"
    assert record.to_dict()["supersedes"] == "ICT-FAMILY-V1"


def test_a_supersession_reusing_v1s_fingerprint_is_refused():
    with pytest.raises(SupersessionRefused, match="nothing actually changed"):
        a_supersession(fingerprint=FROZEN_FINGERPRINT)


def test_a_supersession_reusing_v1s_protocol_is_refused():
    with pytest.raises(SupersessionRefused, match="new research protocol"):
        a_supersession(protocol_version="research-protocol-v1")


def test_a_supersession_reusing_v1s_trial_count_is_refused():
    with pytest.raises(SupersessionRefused, match="recounts its own trials"):
        a_supersession(trial_count=36)


def test_a_supersession_reusing_the_v1_version_string_is_refused():
    with pytest.raises(SupersessionRefused, match="frozen version"):
        a_supersession(version="v1")


def test_a_supersession_must_name_what_it_replaces():
    with pytest.raises(SupersessionRefused, match="does not name"):
        a_supersession(supersedes_fingerprint="deadbeefdeadbeef")


@pytest.mark.parametrize("field_name", ["change_summary", "reason"])
def test_a_supersession_without_provenance_is_refused(field_name):
    with pytest.raises(SupersessionRefused, match="provenance"):
        a_supersession(**{field_name: "tweaked it"})


def test_a_supersession_needs_a_positive_trial_count():
    with pytest.raises(SupersessionRefused, match="must be positive"):
        a_supersession(trial_count=-1)


def test_a_supersession_records_when_and_from_what_commit():
    record = a_supersession()
    assert record.declared_at.tzinfo is not None
    assert record.to_dict()["declared_commit"]


def test_a_supersession_is_immutable():
    from dataclasses import FrozenInstanceError

    record = a_supersession()
    with pytest.raises(FrozenInstanceError):
        record.trial_count = 99          # type: ignore[misc]


def test_declaring_a_v2_does_not_alter_v1():
    a_supersession()
    assert verify_frozen() == FROZEN_FINGERPRINT
    assert ICT_FAMILY_V1.trial_count == 36


def test_a_declared_supersession_marks_v1_superseded():
    assert family_status(supersession=a_supersession()) is \
        FamilyStatus.SUPERSEDED


def test_there_is_no_way_to_edit_v1_in_place():
    """The absent methods are the guarantee, so their absence is asserted."""
    from ai_trading.research import ict_freeze

    for forbidden in ("unfreeze", "amend", "retune", "update_fingerprint",
                      "edit_frozen"):
        assert not hasattr(ict_freeze, forbidden)
    for forbidden in ("unlock", "reopen", "edit", "remove"):
        assert not hasattr(ICT_FAMILY_V1, forbidden)


# =========================================================================
# The record as data
# =========================================================================


def test_the_freeze_record_serializes_completely():
    record = freeze_record()
    for key in ("label", "family_id", "version", "protocol_version",
                "frozen_on", "fingerprint", "hypothesis_count", "trial_count",
                "hypothesis_fingerprints", "decision_events", "labels",
                "feature_versions", "baselines", "parameters", "windows",
                "status", "prohibited_actions", "next_permitted_action",
                "record_checksum"):
        assert key in record, key
    assert record["status"] == "real_data_pending"
    assert record["fingerprint"] == FROZEN_FINGERPRINT
    assert record["trial_count"] == 36


def test_the_freeze_record_checksum_is_stable():
    assert freeze_record()["record_checksum"] == \
        freeze_record()["record_checksum"]


def test_the_freeze_record_carries_no_result():
    """A pre-registration that carries a number has already seen data."""
    import json

    text = json.dumps(freeze_record())
    for forbidden in ("p_value", "sharpe", "expectancy", "win_rate",
                      "hit_rate", "pnl"):
        assert forbidden not in text
