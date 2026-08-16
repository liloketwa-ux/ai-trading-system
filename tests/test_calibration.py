"""Research calibration: does the machinery find what is actually there?

Every earlier phase asked whether the pipeline runs. These tests ask whether it
is right. Five datasets with known generating processes, one question each, and
a scorer that compares the blind detection against sealed truth.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from ai_trading.calibration import (
    ALL_GENERATORS,
    CalibrationRun,
    EconomicVerdict,
    EdgeKind,
    GroundTruth,
    SealedTruth,
    StatisticalVerdict,
    detect_by_regime,
    detect_mean_reversion,
    detect_momentum,
    false_discovery_stress,
    generate_mean_reversion,
    generate_momentum,
    generate_null,
    generate_regime_dependent,
    generate_sub_cost,
)
from ai_trading.history import (
    CheckOutcome,
    ContinuousOnlyProviderError,
    ContractRecord,
    DataOrigin,
    DatasetChecklist,
    DatasetGrade,
    GradeError,
    InstrumentMetadata,
    ProviderCredentialError,
    ProviderManifest,
    ResponseProvenance,
    assess_grades,
    bar_close_availability,
    run_quality_gate,
)
from ai_trading.history.checklist import CHECKLIST_ITEMS
from ai_trading.history.providers import SCHEMA_VERSION, Bar, CoverageWindow, DataKind
from ai_trading.research.costs import REALISTIC

UTC = timezone.utc
COSTS = REALISTIC          # one cost model for every calibration dataset


# =========================================================================
# Sealed truth
# =========================================================================


def test_truth_is_sealed_until_revealed():
    sealed = SealedTruth(GroundTruth(EdgeKind.MOMENTUM, 0.25), "m")
    assert not sealed.was_revealed
    assert "sealed" in repr(sealed)


def test_revealing_truth_requires_a_purpose():
    sealed = SealedTruth(GroundTruth(EdgeKind.NONE, 0.0))
    with pytest.raises(ValueError, match="requires a stated purpose"):
        sealed.reveal("")


def test_every_reveal_is_logged():
    sealed = SealedTruth(GroundTruth(EdgeKind.NONE, 0.0))
    sealed.reveal("scoring")
    assert len(sealed.log) == 1
    assert sealed.log.entries[0].purpose == "scoring"


def test_the_repr_does_not_leak_the_answer():
    """Printing a sealed object in a debug session must not spill the truth."""
    sealed = SealedTruth(GroundTruth(EdgeKind.MOMENTUM, 0.9876), "m")
    assert "0.9876" not in repr(sealed)
    assert "momentum" not in repr(sealed).lower().replace("'m'", "")


def test_datasets_do_not_expose_generator_parameters():
    """A detector receives bars, not the recipe that made them."""
    dataset = generate_momentum(n=200)
    for bar in dataset.bars[:5]:
        assert not hasattr(bar, "phi")
        assert not hasattr(bar, "truth")


def test_sub_cost_still_counts_as_having_a_gross_edge():
    assert EdgeKind.SUB_COST.has_gross_edge
    assert not EdgeKind.NONE.has_gross_edge


# =========================================================================
# 1. Reject zero-drift data
# =========================================================================


def test_null_data_yields_no_evidence():
    detection = detect_momentum(generate_null(), costs=COSTS, seed=7)
    assert detection.statistical is StatisticalVerdict.NO_EVIDENCE


def test_the_scorer_marks_a_correct_null_rejection():
    run = CalibrationRun(generate_null(), COSTS)
    run.run(detect_momentum)
    score = run.score()
    assert score.correct
    assert "correctly found nothing" in score.reason


def test_null_rejection_holds_across_several_seeds():
    """One clean seed is luck; several is behaviour."""
    verdicts = [
        detect_momentum(generate_null(seed=seed), costs=COSTS, seed=7).statistical
        for seed in (11, 12, 13, 14, 15)
    ]
    false_positives = [v for v in verdicts if v.found_something]
    assert len(false_positives) <= 1, f"too many false positives: {verdicts}"


# =========================================================================
# 2. Detect known positive edge
# =========================================================================


def test_known_momentum_is_recovered():
    detection = detect_momentum(generate_momentum(), costs=COSTS, seed=7)
    assert detection.statistical is StatisticalVerdict.POSITIVE_EFFECT
    assert detection.gross_mean_bps > 0


def test_known_momentum_is_recovered_out_of_sample():
    """The half that matters: in-sample recovery proves much less."""
    _in_sample, out_of_sample = generate_momentum().split(0.5)
    detection = detect_momentum(out_of_sample, costs=COSTS, seed=7)
    assert detection.statistical is StatisticalVerdict.POSITIVE_EFFECT


def test_recovered_momentum_is_near_the_true_effect():
    """Recovery, not just detection: the magnitude should be about right."""
    dataset = generate_momentum()
    detection = detect_momentum(dataset, costs=COSTS, seed=7)
    truth = dataset.truth.reveal("test asserts magnitude recovery")
    assert detection.gross_mean_bps == pytest.approx(
        truth.expected_gross_bps, rel=0.30)


def test_the_scorer_marks_a_correct_momentum_recovery():
    run = CalibrationRun(generate_momentum(), COSTS)
    run.run(detect_momentum)
    assert run.score().correct


def test_known_mean_reversion_is_recovered():
    detection = detect_mean_reversion(generate_mean_reversion(), costs=COSTS,
                                      seed=7)
    assert detection.statistical is StatisticalVerdict.POSITIVE_EFFECT


def test_mean_reversion_is_recovered_out_of_sample():
    _in_sample, out_of_sample = generate_mean_reversion().split(0.5)
    detection = detect_mean_reversion(out_of_sample, costs=COSTS, seed=7)
    assert detection.statistical is StatisticalVerdict.POSITIVE_EFFECT


def test_the_mean_reversion_detector_picks_its_own_parameters():
    """A detector handed the generator's parameters proves nothing."""
    dataset = generate_mean_reversion(threshold_sigma=2.5, lookback=40)
    detection = detect_mean_reversion(dataset, lookback=20, threshold_sigma=1.5,
                                      costs=COSTS, seed=7)
    assert detection.samples > 0


# =========================================================================
# 3. Detect known negative edge
# =========================================================================


def test_known_negative_momentum_is_recovered_as_negative():
    detection = detect_momentum(generate_momentum(phi=-0.25, seed=61),
                                costs=COSTS, seed=7)
    assert detection.statistical is StatisticalVerdict.NEGATIVE_EFFECT
    assert detection.gross_mean_bps < 0


def test_a_negative_edge_is_never_economically_attractive():
    detection = detect_momentum(generate_momentum(phi=-0.25, seed=61),
                                costs=COSTS, seed=7)
    assert detection.economic is EconomicVerdict.NEGATIVE_NET
    assert not detection.economic.tradeable


# =========================================================================
# 4. Detect regime-dependent edge
# =========================================================================


def test_regimes_are_separated_by_the_breakdown():
    breakdown = detect_by_regime(generate_regime_dependent(), costs=COSTS, seed=7)
    assert set(breakdown.detections) == {"A", "B"}
    assert breakdown.separated
    assert breakdown.spread_bps > 0


def test_the_positive_regime_is_identified():
    breakdown = detect_by_regime(generate_regime_dependent(), costs=COSTS, seed=7)
    assert breakdown.detections["A"].statistical is StatisticalVerdict.POSITIVE_EFFECT
    assert breakdown.detections["A"].gross_mean_bps > 0


def test_the_neutral_or_negative_regime_is_identified():
    breakdown = detect_by_regime(generate_regime_dependent(), costs=COSTS, seed=7)
    regime_b = breakdown.detections["B"]
    assert regime_b.gross_mean_bps < breakdown.detections["A"].gross_mean_bps
    assert regime_b.statistical is not StatisticalVerdict.POSITIVE_EFFECT


def test_pooling_regimes_understates_the_effect():
    """Why the breakdown exists: pooled, the two regimes partly cancel."""
    dataset = generate_regime_dependent()
    pooled = detect_momentum(dataset, costs=COSTS, seed=7)
    breakdown = detect_by_regime(dataset, costs=COSTS, seed=7)
    assert breakdown.detections["A"].gross_mean_bps > pooled.gross_mean_bps


def test_a_breakdown_needs_regime_labels():
    with pytest.raises(ValueError, match="no regime labels"):
        detect_by_regime(generate_momentum(), costs=COSTS, seed=7)


# =========================================================================
# 5. Costs destroy the edge
# =========================================================================


def test_a_sub_cost_edge_is_statistically_real():
    detection = detect_momentum(generate_sub_cost(), costs=COSTS, seed=7)
    assert detection.statistical is StatisticalVerdict.POSITIVE_EFFECT
    assert detection.gross_mean_bps > 0


def test_a_sub_cost_edge_is_economically_unattractive():
    """The verdict that separates 'significant' from 'worth trading'."""
    detection = detect_momentum(generate_sub_cost(), costs=COSTS, seed=7)
    assert detection.economic is EconomicVerdict.ECONOMICALLY_UNATTRACTIVE
    assert detection.net_mean_bps < 0
    assert not detection.economic.tradeable


def test_gross_edge_alone_does_not_pass():
    detection = detect_momentum(generate_sub_cost(), costs=COSTS, seed=7)
    assert detection.gross_mean_bps < detection.cost_bps


def test_the_scorer_requires_both_verdicts_on_sub_cost_data():
    run = CalibrationRun(generate_sub_cost(), COSTS)
    run.run(detect_momentum)
    score = run.score()
    assert score.correct
    assert "refused it on costs" in score.reason


def test_the_same_cost_model_is_used_across_datasets():
    """The calibration is not rigged by per-dataset cost tuning."""
    tradeable = detect_momentum(generate_momentum(), costs=COSTS, seed=7)
    refused = detect_momentum(generate_sub_cost(), costs=COSTS, seed=7)
    assert tradeable.cost_bps == refused.cost_bps
    assert tradeable.economic is EconomicVerdict.ECONOMICALLY_ATTRACTIVE
    assert refused.economic is EconomicVerdict.ECONOMICALLY_UNATTRACTIVE


# =========================================================================
# Blindness
# =========================================================================


def test_detection_runs_before_truth_is_revealed():
    run = CalibrationRun(generate_momentum(), COSTS)
    run.run(detect_momentum)
    run.assert_blind()
    assert run.reveals_before_detection == 0


def test_a_peeked_run_is_rejected():
    dataset = generate_momentum()
    dataset.truth.reveal("peeking before detection")
    run = CalibrationRun(dataset, COSTS)
    run.run(detect_momentum)
    with pytest.raises(AssertionError, match="cannot be treated as a blind"):
        run.score()


def test_scoring_without_a_detection_is_refused():
    with pytest.raises(RuntimeError, match="run a detector before scoring"):
        CalibrationRun(generate_null(), COSTS).score()


@pytest.mark.parametrize("name", sorted(ALL_GENERATORS))
def test_every_calibration_dataset_scores_correctly(name):
    """The whole suite, one assertion per dataset."""
    dataset = ALL_GENERATORS[name]()
    detector = detect_mean_reversion if name == "mean_reversion" else detect_momentum
    run = CalibrationRun(dataset, COSTS)
    run.run(detector)
    score = run.score()
    assert score.correct, f"{name}: {score.reason}"


# =========================================================================
# False-discovery stress
# =========================================================================


def test_trial_counting_reflects_the_declared_family():
    report = false_discovery_stress(trials=200)
    assert report.trials == 200


def test_uncorrected_false_positive_rate_is_calibrated():
    """Every hypothesis is false, so the raw rate should land near alpha."""
    report = false_discovery_stress(trials=400, alpha=0.05, seed=13)
    assert report.raw_rate_is_calibrated, report.observed_false_positive_rate


def test_benjamini_hochberg_reduces_discoveries_on_pure_noise():
    report = false_discovery_stress(trials=400, seed=13)
    assert report.correction_reduces_discoveries
    assert report.bh_discoveries == 0


def test_bonferroni_is_at_least_as_strict_as_bh():
    report = false_discovery_stress(trials=400, seed=13)
    assert report.bonferroni_discoveries <= report.bh_discoveries or \
        report.bh_discoveries == 0


def test_deflated_sharpe_penalises_selection_over_many_trials():
    report = false_discovery_stress(trials=400, seed=13)
    assert report.dsr_penalises_selection
    assert report.best_sharpe > 0        # something looked good...
    assert report.deflated_sharpe < 0.5  # ...and the DSR did not believe it


def test_more_trials_do_not_make_the_best_null_look_better():
    few = false_discovery_stress(trials=50, seed=13)
    many = false_discovery_stress(trials=800, seed=13)
    assert many.deflated_sharpe <= few.deflated_sharpe + 1e-9


# =========================================================================
# Part B -- dataset grades
# =========================================================================


def a_bar(minute=0, contract="NQZ25", **kw):
    event = datetime(2026, 3, 2, 14, 30, tzinfo=UTC) + timedelta(minutes=minute)
    defaults = dict(
        source="fixture", event_time=event, available_at=event,
        retrieved_at=datetime(2026, 8, 16, tzinfo=UTC),
        schema_version=SCHEMA_VERSION, instrument="NQ", contract=contract,
        timeframe="1m", open=20_000.0, high=20_010.0, low=19_990.0,
        close=20_005.0, volume=100.0,
    )
    return Bar(**{**defaults, **kw})


def clean_report():
    return run_quality_gate([a_bar(i) for i in range(30)], provider="fixture")


def failing_report():
    bars = [a_bar(i) for i in range(10)] + [a_bar(3)]
    return run_quality_gate(bars, provider="fixture")


def test_the_five_grades_are_ordered():
    ranks = [g.rank for g in DatasetGrade]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == 5


def test_real_clean_data_reaches_market_claim_allowed():
    result = assess_grades(source_name="databento", origin=DataOrigin.REAL_MARKET,
                           quality_report=clean_report(), point_in_time_clean=True)
    assert result.highest is DatasetGrade.MARKET_CLAIM_ALLOWED
    assert result.permits_market_claims


def test_synthetic_data_is_research_grade_and_not_market_claim_allowed():
    """The distinction the ladder exists for."""
    result = assess_grades(source_name="generator", origin=DataOrigin.SYNTHETIC,
                           quality_report=clean_report(), point_in_time_clean=True)
    assert result.permits_research
    assert not result.permits_market_claims
    assert result.highest is DatasetGrade.RESEARCH_GRADE


def test_the_synthetic_block_explains_itself():
    result = assess_grades(source_name="generator", origin=DataOrigin.SYNTHETIC,
                           quality_report=clean_report(), point_in_time_clean=True)
    assert "describe the generator" in result.blocking_reason


def test_failing_quality_blocks_every_higher_grade():
    result = assess_grades(source_name="databento", origin=DataOrigin.REAL_MARKET,
                           quality_report=failing_report(), point_in_time_clean=True)
    assert result.granted(DatasetGrade.SOURCE_VALID)
    assert not result.granted(DatasetGrade.DATA_QUALITY_VALID)
    assert not result.granted(DatasetGrade.POINT_IN_TIME_VALID)
    assert not result.permits_research


def test_point_in_time_failure_blocks_research_grade():
    result = assess_grades(source_name="databento", origin=DataOrigin.REAL_MARKET,
                           quality_report=clean_report(), point_in_time_clean=False,
                           point_in_time_note="3 rows available before their event")
    assert result.granted(DatasetGrade.DATA_QUALITY_VALID)
    assert not result.permits_research
    assert "3 rows" in result.blocking_reason


def test_an_unidentified_source_fails_the_first_gate():
    result = assess_grades(source_name="", origin=DataOrigin.REAL_MARKET,
                           quality_report=clean_report(), point_in_time_clean=True)
    assert result.highest is None
    assert "not identified" in result.blocking_reason


def test_requiring_an_ungranted_grade_raises():
    result = assess_grades(source_name="generator", origin=DataOrigin.SYNTHETIC,
                           quality_report=clean_report(), point_in_time_clean=True)
    result.require(DatasetGrade.RESEARCH_GRADE)
    with pytest.raises(GradeError, match="market_claim_allowed"):
        result.require(DatasetGrade.MARKET_CLAIM_ALLOWED, "the ICT campaign")


# =========================================================================
# Part B -- provenance timestamps
# =========================================================================


def test_source_availability_is_unknown_by_default():
    """Bar files do not publish delivery times, so the field stays empty."""
    bar = a_bar()
    assert bar.source_available_at is None
    assert not bar.source_availability_known
    assert bar.availability_is_policy_derived


def test_policy_derived_availability_is_not_a_delivery_claim():
    bar = a_bar()
    payload = bar.provenance()
    assert payload["source_available_at"] is None
    assert payload["availability_is_policy_derived"] is True


def test_a_provider_supplied_availability_is_recorded_separately():
    event = datetime(2026, 3, 2, 14, 30, tzinfo=UTC)
    bar = a_bar(source_available_at=event + timedelta(seconds=3))
    assert bar.source_availability_known
    assert not bar.availability_is_policy_derived


def test_source_availability_cannot_precede_the_event():
    event = datetime(2026, 3, 2, 14, 30, tzinfo=UTC)
    with pytest.raises(ValueError, match="source_available_at"):
        a_bar(source_available_at=event - timedelta(seconds=1))


def test_all_four_provenance_timestamps_are_carried():
    event = datetime(2026, 3, 2, 14, 30, tzinfo=UTC)
    bar = a_bar(source_available_at=event + timedelta(seconds=2),
                system_observed_at=event + timedelta(seconds=5),
                ingested_at=event + timedelta(seconds=9))
    payload = bar.provenance()
    for name in ("event_time", "source_available_at", "system_observed_at",
                 "ingested_at"):
        assert payload[name] is not None


# =========================================================================
# Part C -- futures provider contract
# =========================================================================


def a_manifest(**kw):
    defaults = dict(
        provider="databento", dataset="GLBX.MDP3",
        kinds=frozenset({DataKind.BARS, DataKind.TRADES}),
        availability_policy=bar_close_availability("documented bar completion"),
        credential_env_vars=("DATABENTO_API_KEY",),
    )
    return ProviderManifest(**{**defaults, **kw})


def test_a_manifest_reports_its_credential_requirement():
    manifest = a_manifest()
    assert manifest.requires_credentials
    assert manifest.credential_env_vars == ("DATABENTO_API_KEY",)


def test_missing_credentials_raise_without_echoing_values():
    manifest = a_manifest()
    with pytest.raises(ProviderCredentialError) as error:
        manifest.check_credentials({})
    assert "DATABENTO_API_KEY" in str(error.value)
    assert "never in a commit" in str(error.value)


def test_present_credentials_pass_without_being_stored():
    manifest = a_manifest()
    manifest.check_credentials({"DATABENTO_API_KEY": "secret-value"})
    assert "secret-value" not in str(manifest.to_dict())


def test_a_continuous_only_provider_is_refused_for_ingestion():
    manifest = a_manifest(serves_continuous_only=True, credential_env_vars=())
    with pytest.raises(ContinuousOnlyProviderError, match="individual contracts"):
        manifest.require_contract_level()


def test_a_contract_level_provider_passes_preflight():
    a_manifest(credential_env_vars=()).require_contract_level()


def test_response_provenance_requires_all_seven_fields():
    coverage = CoverageWindow(DataKind.BARS, "NQ", date(2026, 1, 1),
                              date(2026, 6, 1), ("1m",))
    with pytest.raises(ValueError, match="requires provider"):
        ResponseProvenance("", "GLBX", "NQM26", datetime.now(UTC), "UTC",
                           "1.0.0", coverage)


def test_instrument_metadata_converts_points_to_currency():
    metadata = InstrumentMetadata("NQ", "CME", "USD", tick_size=0.25,
                                  tick_value=5.0, multiplier=20.0)
    assert metadata.points_to_currency(3.0) == 60.0


def test_instrument_metadata_rejects_non_positive_multipliers():
    with pytest.raises(ValueError, match="multiplier must be positive"):
        InstrumentMetadata("NQ", "CME", "USD", 0.25, 5.0, 0.0)


def test_a_contract_record_carries_its_expiry():
    record = ContractRecord("NQ", "NQM26", expiry=date(2026, 6, 19))
    assert record.to_dict()["expiry"] == "2026-06-19"


def test_no_futures_provider_implementation_ships():
    from ai_trading.history import FuturesDataProvider

    with pytest.raises(TypeError):
        FuturesDataProvider()


# =========================================================================
# Part F -- dataset checklist
# =========================================================================


def test_the_checklist_has_fourteen_items():
    assert len(CHECKLIST_ITEMS) == 14


def test_a_new_checklist_starts_entirely_unknown():
    checklist = DatasetChecklist("nq-nqm26")
    assert len(checklist.unknowns) == 14
    assert not checklist.is_complete


def test_unknown_is_not_the_same_as_fail():
    """A gap to close, versus a defect to fix."""
    checklist = DatasetChecklist("nq")
    checklist.record("roll_metadata", CheckOutcome.UNKNOWN, "not yet gathered")
    checklist.record("invalid_ohlc", CheckOutcome.FAIL, "12 impossible bars")
    assert len(checklist.failures) == 1
    assert checklist.failures[0].name == "invalid_ohlc"
    assert not CheckOutcome.UNKNOWN.is_defect
    assert CheckOutcome.FAIL.is_defect


def test_both_unknown_and_fail_block_approval():
    assert CheckOutcome.UNKNOWN.blocks_approval
    assert CheckOutcome.FAIL.blocks_approval
    assert not CheckOutcome.PASS.blocks_approval


def test_a_complete_checklist_permits_approval():
    checklist = DatasetChecklist("nq")
    for name in CHECKLIST_ITEMS:
        checklist.record(name, CheckOutcome.PASS, "verified")
    assert checklist.is_complete
    checklist.require_complete()


def test_an_incomplete_checklist_names_what_is_missing():
    checklist = DatasetChecklist("nq")
    for name in CHECKLIST_ITEMS:
        checklist.record(name, CheckOutcome.PASS, "verified")
    checklist.record("adjustment_policy", CheckOutcome.UNKNOWN, "none declared")
    with pytest.raises(RuntimeError, match="adjustment_policy"):
        checklist.require_complete()


def test_the_checklist_cannot_be_shortened():
    checklist = DatasetChecklist("nq")
    with pytest.raises(KeyError, match="is not a checklist item"):
        checklist.record("looks_fine_to_me", CheckOutcome.PASS)
