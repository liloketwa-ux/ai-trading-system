"""Phase 9: historical data acquisition, quality gating, lineage and replay.

Two things are being proven here. First, that the pipeline works end to end.
Second -- and this is the half that matters given no real data was reachable --
that every gate in it *refuses* correctly: synthetic data cannot become a market
claim, defective data cannot become a dataset, an unproven adapter cannot become
research-approved, and the ICT pre-registration stays closed.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from ai_trading.history import (
    AvailabilityError,
    AvailabilityPolicy,
    AvailabilityQuality,
    Bar,
    ContinuousSeriesRefused,
    ContractBook,
    ContractMetadata,
    CoverageWindow,
    DataKind,
    DataOrigin,
    DatasetGateError,
    HistoricalDataProvider,
    LatencyInstrument,
    LatencyObservation,
    LatencyStage,
    LatencyStatus,
    LeakageError,
    PointInTimeReplay,
    ProviderCapabilityError,
    ProviderDescriptor,
    QualityStatus,
    ResearchDataset,
    SessionSpec,
    Severity,
    SourceLedger,
    SourcePromotionError,
    SourceStatus,
    UnmeasuredLatencyError,
    bar_close_availability,
    run_quality_gate,
)
from ai_trading.history.providers import SCHEMA_VERSION
from ai_trading.research.campaign import (
    BASELINE_SUITE,
    CampaignDeclaration,
    CampaignPurpose,
    CampaignResult,
    CampaignStatus,
    ICTGate,
    ICTGateError,
)
from ai_trading.validation.rolls import AdjustmentMethod, RollMethod, RollPolicy

UTC = timezone.utc
RETRIEVED = datetime(2026, 8, 16, tzinfo=UTC)

#: Availability is assumed, not observed. Every fixture says so.
ASSUMED = bar_close_availability("fixture bars carry no arrival timestamp")


def bar(minute=0, *, contract="NQZ25", timeframe="1m", open_=20_000.0, high=20_010.0,
        low=19_990.0, close=20_005.0, volume=100.0, instrument="NQ",
        available_delay=timedelta(0), source="fixture",
        quality=AvailabilityQuality.ASSUMED_BAR_CLOSE):
    event = datetime(2026, 3, 2, 14, 30, tzinfo=UTC) + timedelta(minutes=minute)
    return Bar(
        source=source, event_time=event, available_at=event + available_delay,
        retrieved_at=RETRIEVED, schema_version=SCHEMA_VERSION,
        availability_quality=quality, instrument=instrument, contract=contract,
        timeframe=timeframe, open=open_, high=high, low=low, close=close,
        volume=volume,
    )


def clean_bars(count=30, **kw):
    return [bar(i, **kw) for i in range(count)]


class FixtureProvider(HistoricalDataProvider):
    """Serves bars only. Everything else is declared unsupported."""

    def __init__(self, bars=None):
        self._bars = list(bars or clean_bars())

    @property
    def descriptor(self):
        return ProviderDescriptor(
            name="fixture", kinds=frozenset({DataKind.BARS}),
            availability_policy=ASSUMED, timezone="UTC",
            known_limitations=("synthetic fixture; not a market",),
        )

    def coverage(self, kind, instrument):
        self.require(kind)
        return CoverageWindow(kind, instrument, date(2026, 3, 2), date(2026, 3, 2),
                              ("1m",))

    def fetch_bars(self, instrument, contract, timeframe, start, end):
        self.require(DataKind.BARS)
        return [b for b in self._bars
                if b.contract == contract and b.timeframe == timeframe
                and start <= b.event_time <= end]


# =========================================================================
# Availability semantics
# =========================================================================


def test_naive_timestamps_are_refused():
    with pytest.raises(AvailabilityError, match="naive datetime"):
        AvailabilityPolicy(AvailabilityQuality.ASSUMED_BAR_CLOSE,
                           justification="x").available_at(
            event_time=datetime(2026, 3, 2, 14, 30))


def test_an_assumption_must_ship_with_its_justification():
    with pytest.raises(AvailabilityError, match="needs a justification"):
        AvailabilityPolicy(AvailabilityQuality.ASSUMED_BAR_CLOSE)


def test_unverified_availability_needs_no_justification():
    """It claims nothing, so there is nothing to justify."""
    policy = AvailabilityPolicy(AvailabilityQuality.UNVERIFIED)
    assert policy.quality is AvailabilityQuality.UNVERIFIED


def test_only_observed_availability_supports_latency_claims():
    assert AvailabilityQuality.OBSERVED.supports_latency_research
    for quality in (AvailabilityQuality.DERIVED,
                    AvailabilityQuality.ASSUMED_BAR_CLOSE,
                    AvailabilityQuality.UNVERIFIED):
        assert not quality.supports_latency_research


def test_unverified_availability_still_permits_bar_research():
    """A 15-minute study does not collapse because dissemination is unmeasured."""
    assert AvailabilityQuality.UNVERIFIED.is_usable_for_research


def test_a_derived_policy_needs_a_real_delay():
    with pytest.raises(AvailabilityError, match="wearing a better label"):
        AvailabilityPolicy(AvailabilityQuality.DERIVED, justification="documented")


def test_a_derived_policy_adds_its_publication_delay():
    policy = AvailabilityPolicy(AvailabilityQuality.DERIVED,
                                publication_delay=timedelta(seconds=250),
                                justification="vendor publishes 250ms after close")
    close = datetime(2026, 3, 2, 14, 31, tzinfo=UTC)
    assert policy.available_at(event_time=close) == close + timedelta(seconds=250)


def test_an_observed_arrival_beats_the_policy():
    """A real measurement always wins over a rule about measurements."""
    observed = datetime(2026, 3, 2, 14, 31, 5, tzinfo=UTC)
    assert ASSUMED.available_at(
        event_time=datetime(2026, 3, 2, 14, 30, tzinfo=UTC),
        observed_at=observed) == observed


def test_an_observed_policy_refuses_a_row_with_no_observation():
    policy = AvailabilityPolicy(AvailabilityQuality.OBSERVED,
                                justification="feed stamps arrival")
    with pytest.raises(AvailabilityError, match="cannot be honoured"):
        policy.available_at(event_time=datetime(2026, 3, 2, tzinfo=UTC))


def test_negative_publication_delay_is_refused():
    with pytest.raises(AvailabilityError, match="does not arrive before it exists"):
        AvailabilityPolicy(AvailabilityQuality.DERIVED,
                           publication_delay=timedelta(seconds=-1),
                           justification="x")


def test_a_bar_cannot_be_available_before_it_happened():
    with pytest.raises(ValueError, match="precedes event_time"):
        bar(available_delay=timedelta(minutes=-5))


# =========================================================================
# Provider contract
# =========================================================================


def test_every_record_carries_the_five_provenance_fields():
    payload = bar().provenance()
    for name in ("source", "event_time", "available_at", "retrieved_at",
                 "schema_version"):
        assert name in payload


def test_a_record_must_name_its_source():
    with pytest.raises(ValueError, match="must name its source"):
        bar(source="")


def test_a_bar_must_name_its_contract():
    """A bar without its contract cannot be kept out of a continuous series."""
    with pytest.raises(ValueError, match="cannot be kept out of a continuous series"):
        bar(contract="")


def test_a_provider_declares_its_capabilities():
    provider = FixtureProvider()
    assert provider.supports(DataKind.BARS)
    assert not provider.supports(DataKind.ORDER_BOOKS)
    assert provider.capability_report()["order_books"] is False


def test_an_unsupported_kind_raises_rather_than_returning_empty():
    """An empty list is indistinguishable from 'no data in that window'."""
    provider = FixtureProvider()
    with pytest.raises(ProviderCapabilityError, match="does not serve order_books"):
        provider.fetch_order_books("NQ", "NQZ25",
                                   datetime(2026, 3, 1, tzinfo=UTC),
                                   datetime(2026, 3, 3, tzinfo=UTC))


def test_coverage_is_declared_not_inferred():
    window = FixtureProvider().coverage(DataKind.BARS, "NQ")
    assert window.start == date(2026, 3, 2)
    assert not window.is_empty


def test_microstructure_kinds_are_flagged():
    assert DataKind.ORDER_BOOKS.is_market_microstructure
    assert DataKind.TRADES.is_market_microstructure
    assert not DataKind.BARS.is_market_microstructure


# =========================================================================
# Contract-aware ingestion
# =========================================================================


def test_contracts_are_stored_separately():
    book = ContractBook("NQ")
    book.add_bars(clean_bars(10, contract="NQZ25"))
    book.add_bars(clean_bars(10, contract="NQH26"))

    assert book.contracts == ["NQH26", "NQZ25"]
    assert book.bar_count("NQZ25", "1m") == 10


def test_a_book_refuses_another_instruments_bars():
    book = ContractBook("NQ")
    with pytest.raises(ValueError, match="mixing instruments"):
        book.add_bars(clean_bars(2, instrument="ES"))


def test_ingestion_records_the_observed_window_per_contract():
    book = ContractBook("NQ")
    book.add_bars(clean_bars(10))
    metadata = book.metadata("NQZ25")
    assert metadata.first_seen == datetime(2026, 3, 2, 14, 30, tzinfo=UTC)
    assert metadata.last_seen == datetime(2026, 3, 2, 14, 39, tzinfo=UTC)


def test_first_seen_describes_the_dataset_not_the_contracts_life():
    """Our coverage starts when our file starts, not when the contract listed."""
    book = ContractBook("NQ")
    book.register_contract(ContractMetadata("NQ", "NQZ25", expiry=date(2025, 12, 19)))
    book.add_bars(clean_bars(5))
    assert book.metadata("NQZ25").first_seen == datetime(2026, 3, 2, 14, 30, tzinfo=UTC)
    assert book.metadata("NQZ25").expiry == date(2025, 12, 19)


def test_a_roll_indicator_must_carry_its_date():
    from ai_trading.history.contracts import RollIndicator

    with pytest.raises(ValueError, match="records no date"):
        ContractMetadata("NQ", "NQZ25", expiry=None,
                         roll_indicator=RollIndicator.VOLUME_CROSSOVER)


def test_roll_evidence_is_an_observation_not_a_decision():
    from ai_trading.history.contracts import RollIndicator

    metadata = ContractMetadata("NQ", "NQZ25", expiry=date(2025, 12, 19),
                                roll_indicator=RollIndicator.VOLUME_CROSSOVER,
                                roll_indicator_date=date(2025, 12, 12))
    assert metadata.has_observed_roll_evidence


def test_unknown_expiry_is_recorded_not_guessed():
    book = ContractBook("NQ")
    book.add_bars(clean_bars(3))
    metadata = book.metadata("NQZ25")
    assert not metadata.expiry_known
    assert "expiry unknown" in metadata.note


def test_a_continuous_series_is_refused_without_a_roll_policy():
    """Delegated to the Phase 7 guard, so the rule lives in exactly one place."""
    from ai_trading.validation.rolls import ContinuityError

    book = ContractBook("NQ")
    book.add_bars(clean_bars(5))
    with pytest.raises(ContinuityError, match="must not be described as continuous"):
        book.continuous_series(RollPolicy())


def test_the_phase_9_refusal_is_a_phase_7_continuity_error():
    """So a caller catching the Phase 7 error still catches this one."""
    from ai_trading.validation.rolls import ContinuityError

    assert issubclass(ContinuousSeriesRefused, ContinuityError)


def test_a_continuous_series_is_refused_even_with_a_valid_policy():
    """No adjustment implementation exists; refusing beats concatenating."""
    book = ContractBook("NQ")
    book.add_bars(clean_bars(5))
    policy = RollPolicy(method=RollMethod.VOLUME,
                        adjustment=AdjustmentMethod.BACK_ADJUSTED)
    with pytest.raises(ContinuousSeriesRefused, match="no adjustment implementation"):
        book.continuous_series(policy)


def test_the_book_has_no_method_that_returns_a_joined_series():
    """The omission is the design, so assert the omission."""
    assert not hasattr(ContractBook, "as_continuous")
    assert not hasattr(ContractBook, "stitched")


def test_the_coverage_report_states_it_is_not_continuous():
    book = ContractBook("NQ")
    book.add_bars(clean_bars(5))
    report = book.coverage_report()
    assert report["is_continuous"] is False
    assert "no roll policy applied" in report["continuity_note"]


# =========================================================================
# Quality gate
# =========================================================================


def test_clean_bars_pass():
    report = run_quality_gate(clean_bars(30), provider="fixture")
    assert report.quality_status is QualityStatus.ELIGIBLE_WITH_WARNINGS
    assert report.fatal_findings == []
    assert report.rows == 30


def test_an_empty_slice_is_rejected_not_treated_as_clean():
    report = run_quality_gate([], provider="fixture")
    assert report.quality_status is QualityStatus.REJECTED
    assert report.findings[0].check == "non_empty"


def test_mixing_slices_is_refused():
    bars = clean_bars(5, contract="NQZ25") + clean_bars(5, contract="NQH26")
    with pytest.raises(ValueError, match="one instrument/contract/timeframe slice"):
        run_quality_gate(bars, provider="fixture")


def test_duplicate_timestamps_are_fatal():
    bars = clean_bars(10) + [bar(3)]
    report = run_quality_gate(bars, provider="fixture")
    assert report.duplicate_rows == 1
    assert report.quality_status is QualityStatus.REJECTED


def test_out_of_order_bars_are_fatal():
    bars = clean_bars(10)
    bars[3], bars[7] = bars[7], bars[3]
    report = run_quality_gate(bars, provider="fixture")
    assert any(f.check == "chronological_order" for f in report.fatal_findings)


def test_impossible_ohlc_is_fatal():
    bars = clean_bars(5) + [bar(9, high=19_000.0, low=20_500.0)]
    report = run_quality_gate(bars, provider="fixture")
    assert any(f.check == "impossible_ohlc" for f in report.fatal_findings)
    assert report.invalid_rows == 1


def test_a_close_outside_the_range_is_impossible():
    assert bar(0, low=19_990.0, high=20_010.0, close=20_500.0).has_impossible_ohlc


def test_negative_volume_is_fatal():
    bars = clean_bars(5) + [bar(9, volume=-1.0)]
    report = run_quality_gate(bars, provider="fixture")
    assert any(f.check == "negative_volume" for f in report.fatal_findings)


def test_a_zero_price_is_a_parse_error_not_a_market_event():
    bars = clean_bars(5) + [bar(9, open_=0.0, low=0.0)]
    report = run_quality_gate(bars, provider="fixture")
    assert any(f.check == "non_positive_price" for f in report.fatal_findings)


def test_implausible_timestamps_are_fatal():
    stray = Bar(source="fixture", event_time=datetime(1972, 1, 1, tzinfo=UTC),
                available_at=datetime(1972, 1, 1, tzinfo=UTC),
                retrieved_at=RETRIEVED, instrument="NQ", contract="NQZ25",
                timeframe="1m", open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0)
    report = run_quality_gate([stray] + clean_bars(5), provider="fixture")
    assert any(f.check == "implausible_timestamp" for f in report.fatal_findings)


def test_misaligned_bars_warn_about_resampling():
    odd = bar(0)
    shifted = Bar(source="fixture",
                  event_time=odd.event_time + timedelta(seconds=17),
                  available_at=odd.event_time + timedelta(seconds=17),
                  retrieved_at=RETRIEVED, instrument="NQ", contract="NQZ25",
                  timeframe="1m", open=1.0, high=2.0, low=0.5, close=1.5, volume=1.0)
    report = run_quality_gate(clean_bars(5) + [shifted], provider="fixture")
    finding = next(f for f in report.findings if f.check == "bar_alignment")
    assert finding.severity is Severity.WARNING
    assert "resampled" in finding.detail


def test_an_unknown_timeframe_cannot_be_spacing_checked():
    report = run_quality_gate(clean_bars(5, timeframe="7m"), provider="fixture")
    assert any(f.check == "known_timeframe" for f in report.findings)
    assert report.missing_rows is None


def test_missing_rows_is_unknown_without_a_session_spec():
    """Reporting zero would claim completeness that was never measured."""
    report = run_quality_gate(clean_bars(10), provider="fixture")
    assert report.missing_rows is None
    assert report.completeness is None


def test_a_session_spec_makes_missing_bars_countable():
    bars = [b for i, b in enumerate(clean_bars(20)) if i not in (5, 6, 7)]
    spec = SessionSpec("continuous", weekdays=frozenset(range(7)))
    report = run_quality_gate(bars, provider="fixture", session_spec=spec)
    assert report.missing_rows == 3
    assert report.completeness == pytest.approx(17 / 20)


def test_missing_bars_are_a_warning_not_a_rejection():
    bars = [b for i, b in enumerate(clean_bars(20)) if i != 5]
    spec = SessionSpec("continuous", weekdays=frozenset(range(7)))
    report = run_quality_gate(bars, provider="fixture", session_spec=spec)
    assert report.quality_status is QualityStatus.ELIGIBLE_WITH_WARNINGS
    assert report.is_research_eligible


def test_bars_during_a_declared_break_are_flagged():
    """Either the session spec or the timezone is wrong; both matter."""
    spec = SessionSpec("with_break", weekdays=frozenset(range(7)),
                       daily_break_utc=(14 * 60 + 33, 14 * 60 + 36))
    report = run_quality_gate(clean_bars(10), provider="fixture", session_spec=spec)
    assert report.session_anomalies == 3
    assert any(f.check == "bars_outside_session" for f in report.findings)


def test_a_holiday_is_not_counted_as_missing_data():
    spec = SessionSpec("holiday", weekdays=frozenset(range(7)),
                       holidays=frozenset({date(2026, 3, 2)}))
    report = run_quality_gate(clean_bars(10), provider="fixture", session_spec=spec)
    assert report.missing_rows == 0


def test_availability_preceding_the_event_is_fatal_at_the_gate():
    good = clean_bars(5)
    leaky = Bar(source="fixture", event_time=datetime(2026, 3, 2, 14, 40, tzinfo=UTC),
                available_at=datetime(2026, 3, 2, 14, 40, tzinfo=UTC),
                retrieved_at=RETRIEVED, instrument="NQ", contract="NQZ25",
                timeframe="1m", open=1.0, high=2.0, low=0.5, close=1.5, volume=1.0)
    object.__setattr__(leaky, "available_at",
                       datetime(2026, 3, 2, 14, 35, tzinfo=UTC))
    report = run_quality_gate(good + [leaky], provider="fixture")
    assert any(f.check == "availability_precedes_event" for f in report.fatal_findings)


def test_the_report_serializes_every_required_field():
    payload = run_quality_gate(clean_bars(5), provider="fixture").to_dict()
    for name in ("rows", "date_range", "missing_rows", "duplicate_rows",
                 "invalid_rows", "timestamp_anomalies", "session_anomalies",
                 "quality_status"):
        assert name in payload


# =========================================================================
# Source status ladder
# =========================================================================


def test_a_new_source_starts_at_source_present():
    ledger = SourceLedger()
    assert ledger.register("databento").status is SourceStatus.SOURCE_PRESENT


def test_levels_are_climbed_one_at_a_time():
    ledger = SourceLedger()
    ledger.register("databento")
    with pytest.raises(SourcePromotionError, match="one at a time"):
        ledger.promote("databento", SourceStatus.RESEARCH_APPROVED,
                       evidence="looks fine")


def test_a_promotion_requires_evidence():
    ledger = SourceLedger()
    ledger.register("databento")
    with pytest.raises(SourcePromotionError, match="requires evidence"):
        ledger.promote("databento", SourceStatus.UNIT_TESTED, evidence="")


def test_research_approval_requires_a_quality_report():
    ledger = SourceLedger()
    ledger.register("fixture")
    for level in (SourceStatus.UNIT_TESTED, SourceStatus.MACHINE_RETRIEVED,
                  SourceStatus.RUNTIME_VERIFIED,
                  SourceStatus.HISTORICALLY_VALIDATED):
        ledger.promote("fixture", level, evidence="step")

    with pytest.raises(SourcePromotionError, match="without a quality report"):
        ledger.promote("fixture", SourceStatus.RESEARCH_APPROVED, evidence="trust me")


def test_research_approval_refuses_a_failing_quality_report():
    ledger = SourceLedger()
    ledger.register("fixture")
    for level in (SourceStatus.UNIT_TESTED, SourceStatus.MACHINE_RETRIEVED,
                  SourceStatus.RUNTIME_VERIFIED,
                  SourceStatus.HISTORICALLY_VALIDATED):
        ledger.promote("fixture", level, evidence="step")

    failing = run_quality_gate(clean_bars(5) + [bar(3)], provider="fixture")
    with pytest.raises(SourcePromotionError, match="duplicate_timestamps"):
        ledger.promote("fixture", SourceStatus.RESEARCH_APPROVED,
                       evidence="ingested", quality_report=failing)


def test_research_approval_succeeds_on_a_passing_report():
    ledger = SourceLedger()
    ledger.register("fixture")
    for level in (SourceStatus.UNIT_TESTED, SourceStatus.MACHINE_RETRIEVED,
                  SourceStatus.RUNTIME_VERIFIED,
                  SourceStatus.HISTORICALLY_VALIDATED):
        ledger.promote("fixture", level, evidence="step")

    record = ledger.promote("fixture", SourceStatus.RESEARCH_APPROVED,
                            evidence="ingested and gated",
                            quality_report=run_quality_gate(clean_bars(30),
                                                            provider="fixture"))
    assert record.may_enter_research
    assert ledger.research_approved() == [record]


def test_only_machine_retrieved_and_above_has_touched_real_data():
    assert not SourceStatus.UNIT_TESTED.has_touched_real_data
    assert SourceStatus.MACHINE_RETRIEVED.has_touched_real_data


def test_a_blocked_source_records_why():
    ledger = SourceLedger()
    record = ledger.block("databento", "network egress blocked at the proxy")
    assert record.is_blocked
    assert not record.may_enter_research


def test_promotion_history_is_kept():
    ledger = SourceLedger()
    ledger.register("fixture")
    ledger.promote("fixture", SourceStatus.UNIT_TESTED, evidence="fixtures pass")
    assert [e.to_status for e in ledger.get("fixture").history] == [
        SourceStatus.UNIT_TESTED]


# =========================================================================
# Research datasets and lineage
# =========================================================================


def approved_dataset(origin=DataOrigin.SYNTHETIC, bars=None):
    bars = bars or clean_bars(30)
    return ResearchDataset.create(
        bars, source="fixture", origin=origin,
        quality_report=run_quality_gate(bars, provider="fixture"),
    )


def test_a_dataset_records_full_lineage():
    dataset = approved_dataset()
    payload = dataset.to_dict()
    for name in ("dataset_id", "source", "date_range", "instrument", "contract",
                 "timeframes", "schema_version", "feature_eligibility",
                 "quality_report", "code_commit", "checksum", "created_at"):
        assert name in payload


def test_the_dataset_id_is_derived_from_content():
    assert approved_dataset().dataset_id == approved_dataset().dataset_id


def test_different_data_produces_a_different_id():
    a = approved_dataset()
    b = approved_dataset(bars=clean_bars(29))
    assert a.dataset_id != b.dataset_id


def test_re_deriving_availability_changes_the_dataset():
    """Different availability is different research, so it must hash differently."""
    a = approved_dataset()
    b = approved_dataset(bars=clean_bars(30, available_delay=timedelta(seconds=30)))
    assert a.checksum != b.checksum


def test_a_dataset_cannot_be_created_from_rejected_data():
    bars = clean_bars(10) + [bar(3)]
    with pytest.raises(DatasetGateError, match="duplicate_timestamps"):
        ResearchDataset.create(bars, source="fixture", origin=DataOrigin.SYNTHETIC,
                               quality_report=run_quality_gate(bars,
                                                               provider="fixture"))


def test_a_dataset_covers_one_contract():
    bars = clean_bars(5, contract="NQZ25") + clean_bars(5, contract="NQH26")
    report = run_quality_gate(clean_bars(5), provider="fixture")
    with pytest.raises(DatasetGateError, match="joining them is a roll"):
        ResearchDataset.create(bars, source="fixture", origin=DataOrigin.SYNTHETIC,
                               quality_report=report)


def test_a_dataset_cannot_span_schema_versions():
    old = Bar(source="fixture", event_time=datetime(2026, 3, 2, 15, tzinfo=UTC),
              available_at=datetime(2026, 3, 2, 15, tzinfo=UTC),
              retrieved_at=RETRIEVED, schema_version="0.9.0", instrument="NQ",
              contract="NQZ25", timeframe="1m", open=1.0, high=2.0, low=0.5,
              close=1.5, volume=1.0)
    bars = clean_bars(5) + [old]
    report = run_quality_gate(clean_bars(5), provider="fixture")
    with pytest.raises(DatasetGateError, match="multiple schema versions"):
        ResearchDataset.create(bars, source="fixture", origin=DataOrigin.SYNTHETIC,
                               quality_report=report)


def test_synthetic_data_cannot_support_a_market_claim():
    """The boundary that keeps this phase honest."""
    dataset = approved_dataset(DataOrigin.SYNTHETIC)
    assert not dataset.may_support_market_claims
    with pytest.raises(DatasetGateError, match="describe the generator"):
        dataset.require_real_market()


def test_real_market_data_may_support_a_claim():
    approved_dataset(DataOrigin.REAL_MARKET).require_real_market()


def test_bar_data_does_not_grant_microstructure_features():
    eligibility = approved_dataset().feature_eligibility
    assert eligibility.bar_features
    assert not eligibility.microstructure_features


def test_assumed_availability_blocks_latency_sensitive_features():
    eligibility = approved_dataset().feature_eligibility
    assert not eligibility.latency_sensitive_features
    assert "measure the availability assumption" in eligibility.reason


def test_observed_availability_permits_latency_sensitive_features():
    bars = clean_bars(30, quality=AvailabilityQuality.OBSERVED)
    dataset = approved_dataset(bars=bars)
    assert dataset.feature_eligibility.latency_sensitive_features


def test_a_dataset_round_trips_to_disk(tmp_path):
    import json

    dataset = approved_dataset()
    path = dataset.save(tmp_path / "dataset.json")
    assert json.loads(path.read_text())["dataset_id"] == dataset.dataset_id


# =========================================================================
# Point-in-time replay
# =========================================================================


def test_a_decision_sees_only_what_was_available():
    replay = PointInTimeReplay(clean_bars(10, available_delay=timedelta(minutes=1)))
    at = datetime(2026, 3, 2, 14, 35, tzinfo=UTC)
    visible = replay.visible_at(at)

    assert all(b.available_at <= at for b in visible)
    assert len(visible) == 5


def test_an_injected_future_observation_is_not_visible():
    """The governing look-ahead test."""
    bars = clean_bars(5)
    future = bar(999, close=99_999.0)          # far beyond the decision time
    replay = PointInTimeReplay(bars + [future])

    at = datetime(2026, 3, 2, 14, 34, tzinfo=UTC)
    visible = replay.visible_at(at)
    assert future not in visible
    assert all(b.close != 99_999.0 for b in visible)


def test_an_injected_future_observation_does_not_become_the_latest():
    bars = clean_bars(5)
    replay = PointInTimeReplay(bars + [bar(999, close=99_999.0)])
    latest = replay.latest_at(datetime(2026, 3, 2, 14, 34, tzinfo=UTC))
    assert latest.close != 99_999.0


def test_leakage_is_detected_when_availability_is_tampered_with():
    bars = clean_bars(5)
    tampered = bar(50)
    object.__setattr__(tampered, "available_at",
                       datetime(2026, 3, 2, 14, 32, tzinfo=UTC))
    replay = PointInTimeReplay(bars + [tampered])

    at = datetime(2026, 3, 2, 14, 33, tzinfo=UTC)
    assert tampered in replay.visible_at(at)     # it claims to be available
    # ...and the honest check still passes, because the claim is self-consistent.
    replay.assert_no_leakage(at)
    # The defect is that its event_time is in the future of the decision:
    assert max(b.event_time for b in replay.visible_at(at)) > at


def test_late_arriving_data_is_replayed_late():
    """Sorting by event time would replay a correction as though it were on time."""
    early_event_late_arrival = bar(0, available_delay=timedelta(minutes=20))
    replay = PointInTimeReplay([early_event_late_arrival] + clean_bars(5)[1:])

    at = datetime(2026, 3, 2, 14, 35, tzinfo=UTC)
    assert early_event_late_arrival not in replay.visible_at(at)


def test_the_cursor_cannot_rewind():
    replay = PointInTimeReplay(clean_bars(10))
    replay.advance(datetime(2026, 3, 2, 14, 35, tzinfo=UTC))
    with pytest.raises(LeakageError, match="cannot rewind"):
        replay.advance(datetime(2026, 3, 2, 14, 32, tzinfo=UTC))


def test_advance_yields_only_newly_available_rows():
    replay = PointInTimeReplay(clean_bars(10))
    first = replay.advance(datetime(2026, 3, 2, 14, 33, tzinfo=UTC))
    second = replay.advance(datetime(2026, 3, 2, 14, 36, tzinfo=UTC))

    assert len(first) == 4
    assert len(second) == 3
    assert not set(first) & set(second)


def test_stepping_a_schedule_never_repeats_a_row():
    replay = PointInTimeReplay(clean_bars(20))
    times = [datetime(2026, 3, 2, 14, 30, tzinfo=UTC) + timedelta(minutes=5 * i)
             for i in range(1, 5)]
    seen = [b for _t, batch in replay.steps(times) for b in batch]
    assert len(seen) == len(set(seen))


def test_the_replay_exposes_no_unfiltered_view():
    """Look-ahead arrives as a code path that skipped the filter."""
    for name in ("bars", "all", "rows", "everything"):
        assert not hasattr(PointInTimeReplay, name)


# =========================================================================
# Pumpi latency
# =========================================================================


def test_latency_stages_must_occur_in_order():
    base = datetime(2026, 3, 2, 14, tzinfo=UTC)
    with pytest.raises(ValueError, match="precedes"):
        LatencyObservation(event_time=base, observed_at=base - timedelta(seconds=1),
                           persisted_at=base, processed_at=base)


def test_an_unmeasured_pipeline_reports_unverified():
    profile = LatencyInstrument("pumpi").profile()
    assert profile.status is LatencyStatus.UNVERIFIED
    assert not profile.is_measured


def test_research_cannot_assume_zero_indexing_latency():
    with pytest.raises(UnmeasuredLatencyError, match="must not assume zero"):
        LatencyInstrument("pumpi").profile(LatencyStage.INDEXING).require_measured()


def test_a_small_sample_refuses_to_produce_percentiles():
    """A P99 from eleven observations describes the sample, not the pipeline."""
    instrument = LatencyInstrument("pumpi")
    base = datetime(2026, 3, 2, 14, tzinfo=UTC)
    for i in range(11):
        instrument.record_event(
            event_time=base + timedelta(seconds=i),
            observed_at=base + timedelta(seconds=i, milliseconds=400),
            persisted_at=base + timedelta(seconds=i, milliseconds=450),
            processed_at=base + timedelta(seconds=i, milliseconds=500))

    profile = instrument.profile()
    assert profile.status is LatencyStatus.INSUFFICIENT_SAMPLES
    assert profile.p99_ms is None


def test_a_sufficient_sample_reports_percentiles_to_p99():
    instrument = LatencyInstrument("pumpi", min_samples=100)
    base = datetime(2026, 3, 2, 14, tzinfo=UTC)
    for i in range(200):
        lag = timedelta(milliseconds=100 + i)
        instrument.record_event(
            event_time=base + timedelta(seconds=i),
            observed_at=base + timedelta(seconds=i) + lag,
            persisted_at=base + timedelta(seconds=i) + lag + timedelta(milliseconds=10),
            processed_at=base + timedelta(seconds=i) + lag + timedelta(milliseconds=20))

    profile = instrument.profile(LatencyStage.INDEXING)
    assert profile.status is LatencyStatus.MEASURED
    assert profile.p50_ms < profile.p95_ms < profile.p99_ms <= profile.max_ms
    profile.require_measured()


def test_each_stage_is_measured_separately():
    report = LatencyInstrument("pumpi").report()
    assert set(report["stages"]) == {s.value for s in LatencyStage}


# =========================================================================
# Campaign declaration and the ICT gate
# =========================================================================


def a_declaration(**kw):
    defaults = dict(
        name="nq-pipeline-validation", purpose=CampaignPurpose.PIPELINE_VALIDATION,
        dataset_id="nq-nqz25-synthetic-abc123", instrument="NQ", contract="NQZ25",
        timeframes=("5m",), features=("atr_14",), labels=("forward_return_12",),
        hypotheses=(), cost_model="fixed_tick_plus_commission",
        execution_model="next_bar_open_stop_wins",
        validation_protocol="purged_walk_forward_v1", seed=7,
    )
    return CampaignDeclaration(**{**defaults, **kw})


def test_a_campaign_must_fix_its_cost_model_before_running():
    with pytest.raises(ValueError, match="cost and execution models"):
        a_declaration(cost_model="")


def test_a_campaign_must_fix_its_validation_protocol():
    with pytest.raises(ValueError, match="validation protocol"):
        a_declaration(validation_protocol="")


def test_the_campaign_id_changes_when_the_search_widens():
    """A quietly widened search becomes visible in the record."""
    narrow = a_declaration()
    wide = a_declaration(features=("atr_14", "rsi_14"))
    assert narrow.campaign_id != wide.campaign_id


def test_the_campaign_id_is_stable_for_the_same_declaration():
    assert a_declaration().campaign_id == a_declaration().campaign_id


def test_a_pipeline_validation_campaign_cannot_claim_an_edge():
    result = CampaignResult(a_declaration(), status=CampaignStatus.COMPLETE)
    allowed, reason = result.may_report_edge()
    assert not allowed
    assert "pipeline_validation" in reason


def test_an_edge_claim_requires_the_baselines_to_have_run():
    declaration = a_declaration(purpose=CampaignPurpose.HYPOTHESIS_EVALUATION,
                                hypotheses=("liquidity_sweep",))
    result = CampaignResult(declaration, status=CampaignStatus.COMPLETE)
    allowed, reason = result.may_report_edge()
    assert not allowed
    assert "baselines not run" in reason


def test_an_edge_claim_is_permitted_once_baselines_and_status_are_complete():
    declaration = a_declaration(purpose=CampaignPurpose.HYPOTHESIS_EVALUATION,
                                hypotheses=("liquidity_sweep",))
    result = CampaignResult(declaration, status=CampaignStatus.COMPLETE)
    for name in BASELINE_SUITE:
        result.baseline_results[name] = {"expectancy": 0.0}
    allowed, _reason = result.may_report_edge()
    assert allowed


def test_the_declared_baseline_suite_covers_the_four_required_baselines():
    assert BASELINE_SUITE == ("random", "hold_matched_random", "momentum",
                              "mean_reversion")


def test_a_hypothesis_campaign_must_declare_hypotheses():
    with pytest.raises(ValueError, match="declares none"):
        a_declaration(purpose=CampaignPurpose.HYPOTHESIS_EVALUATION, hypotheses=())


def test_the_test_count_reflects_the_declaration():
    declaration = a_declaration(purpose=CampaignPurpose.HYPOTHESIS_EVALUATION,
                                hypotheses=("a", "b", "c"),
                                timeframes=("5m", "15m"))
    assert declaration.test_count == 6


def test_the_ict_gate_is_closed_without_a_dataset():
    gate = ICTGate(reason_blocked="no market data reachable")
    assert not gate.is_open
    assert "CLOSED" in gate.status()
    with pytest.raises(ICTGateError, match="may not be evaluated yet"):
        gate.require_open()


def test_the_ict_gate_stays_closed_on_synthetic_data():
    """A synthetic dataset cannot open it, however it is labelled."""
    gate = ICTGate(dataset=approved_dataset(DataOrigin.SYNTHETIC))
    assert not gate.is_open
    assert "origin synthetic" in gate.status()
    with pytest.raises(ICTGateError, match="one-shot pre-registration"):
        gate.require_open()


def test_the_ict_gate_opens_on_real_research_eligible_data():
    gate = ICTGate(dataset=approved_dataset(DataOrigin.REAL_MARKET))
    assert gate.is_open
    assert gate.require_open().origin is DataOrigin.REAL_MARKET


def test_hypothesis_definitions_may_not_drift():
    """A poor real-data result is not a licence to edit the hypotheses."""
    gate = ICTGate(dataset=approved_dataset(DataOrigin.REAL_MARKET))
    with pytest.raises(ICTGateError, match="new study"):
        gate.verify_definitions_unchanged(
            registered=["liquidity_sweep", "fvg", "mss"],
            current=["liquidity_sweep", "fvg", "mss", "tuned_variant"],
        )


def test_an_unchanged_hypothesis_set_passes():
    gate = ICTGate(dataset=approved_dataset(DataOrigin.REAL_MARKET))
    gate.verify_definitions_unchanged(registered=["a", "b"], current=["b", "a"])
