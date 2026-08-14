"""Phase 4 feature engine: point-in-time correctness and look-ahead attacks.

The attacks matter most. Each plants information a decision could not have had
and asserts the feature refuses it.
"""

from datetime import datetime, time, timedelta, timezone

import pytest

from ai_trading.features import (
    ASIA,
    CME_EQUITY,
    LONDON,
    NEW_YORK,
    REGISTRY,
    DataQuality,
    Domain,
    FeatureStatus,
    LatencyConfidence,
    SessionDefinition,
    bar_available_at,
    bar_close,
    derivatives,
    futures,
    latest_completed_bar,
    microstructure,
)
from ai_trading.features.latency import DEFAULT_LATENCY, LatencyModel, SourceLatency
from ai_trading.storage import InMemoryStore, Observation, TemporalIntegrityError
from ai_trading.storage.quality import AvailabilityRule

UTC = timezone.utc
T0 = datetime(2024, 3, 4, 0, 0, tzinfo=UTC)  # a Monday
HOUR = timedelta(hours=1)


def bar(open_time, o, h, l, c, v=1000.0, timeframe="1h", instrument="ES",
        source="test", available=None):
    """A completed bar. Availability defaults to its CLOSE, never its open."""
    close_time = open_time + Timeframe_duration(timeframe)
    return Observation(
        key=instrument, kind="ohlcv", event_time=open_time,
        available_at=available or close_time, ingested_at=close_time,
        source=source, timeframe=timeframe,
        value={"open": o, "high": h, "low": l, "close": c, "volume": v},
    )


def Timeframe_duration(label):
    units = {"m": 60, "h": 3600, "d": 86400}
    return timedelta(seconds=int(label[:-1]) * units[label[-1]])


def series(n=40, timeframe="1h", start=T0, base=100.0, step=1.0, instrument="ES"):
    """Monotonic series with constant bar range.

    Deliberately degenerate: it has no pivots and a window-independent ATR,
    which makes it the right fixture for availability tests and the wrong one
    for structure or parameter-sensitivity tests. Use :func:`oscillating` there.
    """
    store = InMemoryStore()
    duration = Timeframe_duration(timeframe)
    for i in range(n):
        price = base + i * step
        store.append(bar(start + i * duration, price, price + 2, price - 2,
                         price + 1, timeframe=timeframe, instrument=instrument))
    return store


def oscillating(n=60, timeframe="1h", start=T0, instrument="ES"):
    """A wave with varying bar ranges, so pivots form and ATR responds to window."""
    import math

    store = InMemoryStore()
    duration = Timeframe_duration(timeframe)
    for i in range(n):
        mid = 100.0 + 10.0 * math.sin(i / 3.0)
        half = 1.0 + 2.5 * abs(math.sin(i / 1.7))  # range varies bar to bar
        store.append(bar(start + i * duration, mid, mid + half, mid - half,
                         mid + half / 2, timeframe=timeframe, instrument=instrument))
    return store


# -- timeframe safety ------------------------------------------------------


@pytest.mark.parametrize("timeframe", ["5m", "15m", "1h", "4h", "1d"])
def test_bar_is_available_at_close_not_open(timeframe):
    duration = Timeframe_duration(timeframe)
    assert bar_close(T0, timeframe) == T0 + duration
    assert bar_available_at(T0, timeframe) == T0 + duration


@pytest.mark.parametrize("timeframe", ["5m", "15m", "1h", "4h", "1d"])
def test_incomplete_bar_is_never_returned(timeframe):
    """ATTACK: ask for the latest bar while it is still forming."""
    duration = Timeframe_duration(timeframe)
    store = series(5, timeframe)

    midway = T0 + duration // 2
    assert latest_completed_bar(store, "ES", timeframe, midway) is None

    at_close = T0 + duration
    completed = latest_completed_bar(store, "ES", timeframe, at_close)
    assert completed is not None and completed.event_time == T0


def test_latest_completed_bar_returns_the_most_recent_available():
    store = series(10, "1h")
    latest = latest_completed_bar(store, "ES", "1h", T0 + 5 * HOUR)
    assert latest.event_time == T0 + 4 * HOUR  # the 4h bar closed at 5h


def test_higher_timeframe_bar_not_visible_before_it_closes():
    """ATTACK: a 4H bar joined onto a 1H decision mid-formation."""
    store = InMemoryStore()
    store.append(bar(T0, 100, 110, 90, 105, timeframe="4h"))
    store.append(bar(T0, 100, 102, 99, 101, timeframe="1h"))

    decision = T0 + 2 * HOUR  # 1h bar closed; 4h bar has not
    assert latest_completed_bar(store, "ES", "1h", decision) is not None
    assert latest_completed_bar(store, "ES", "4h", decision) is None
    assert latest_completed_bar(store, "ES", "4h", T0 + 4 * HOUR) is not None


def test_cross_timeframe_join_uses_only_completed_bars():
    """The canonical multi-timeframe leak, tested directly."""
    store = InMemoryStore()
    for i in range(8):
        store.append(bar(T0 + i * HOUR, 100 + i, 101 + i, 99 + i, 100.5 + i, timeframe="1h"))
    store.append(bar(T0, 100, 130, 95, 128, timeframe="4h"))          # closes 04:00
    store.append(bar(T0 + 4 * HOUR, 128, 160, 127, 158, timeframe="4h"))  # closes 08:00

    at_six = T0 + 6 * HOUR
    htf = latest_completed_bar(store, "ES", "4h", at_six)
    assert htf.event_time == T0                    # the 04:00 bar, not the forming one
    assert htf.value["high"] == 130                # never sees 160


def test_source_latency_delays_availability():
    model = LatencyModel()
    model.register(SourceLatency("slow", timedelta(minutes=5), LatencyConfidence.MEASURED))
    assert bar_available_at(T0, "1h", "slow", model) == T0 + HOUR + timedelta(minutes=5)


def test_default_latency_is_unverified_not_zero_by_assumption():
    """Latency is an assumption until measured, and says so."""
    assert DEFAULT_LATENCY.confidence("pumpi:pumpfun") is LatencyConfidence.UNVERIFIED
    assert not DEFAULT_LATENCY.confidence("pumpi:pumpfun").is_trustworthy
    assert not DEFAULT_LATENCY.all_verified(["pumpi:pumpfun"])


def test_negative_latency_is_rejected():
    with pytest.raises(ValueError, match="negative"):
        SourceLatency("x", timedelta(seconds=-1))


# -- sessions and DST ------------------------------------------------------


def test_session_windows_are_timezone_aware():
    window = LONDON.window_for(datetime(2024, 1, 15).date())
    assert window.start.hour == 8  # 08:00 GMT in January


def test_london_session_shifts_with_dst():
    """ATTACK on fixed offsets: London is UTC+0 in winter, UTC+1 in summer."""
    winter = LONDON.window_for(datetime(2024, 1, 15).date())
    summer = LONDON.window_for(datetime(2024, 7, 15).date())
    assert winter.start.hour == 8   # 08:00 UTC
    assert summer.start.hour == 7   # 08:00 BST == 07:00 UTC
    assert winter.start.hour != summer.start.hour


def test_new_york_session_shifts_with_dst():
    winter = NEW_YORK.window_for(datetime(2024, 1, 15).date())
    summer = NEW_YORK.window_for(datetime(2024, 7, 15).date())
    assert winter.start.hour == 14  # 09:30 EST -> 14:30 UTC
    assert summer.start.hour == 13  # 09:30 EDT -> 13:30 UTC


def test_dst_transition_day_is_handled():
    """2024-03-10 is the US spring-forward date."""
    before = NEW_YORK.window_for(datetime(2024, 3, 8).date())
    after = NEW_YORK.window_for(datetime(2024, 3, 11).date())
    assert before.start.hour == 14
    assert after.start.hour == 13


def test_session_crossing_midnight():
    """CME equity opens 17:00 CT and closes 16:00 CT the next day."""
    assert CME_EQUITY.crosses_midnight
    window = CME_EQUITY.window_for(datetime(2024, 1, 15).date())
    assert window.end > window.start
    assert window.duration > timedelta(hours=20)


def test_session_definition_is_versioned():
    assert LONDON.key == "london:v1"
    modified = SessionDefinition("london", "Europe/London", time(9, 0), time(17, 0), version="2")
    assert modified.key == "london:v2"
    assert modified.window_for(datetime(2024, 1, 15).date()).start != \
        LONDON.window_for(datetime(2024, 1, 15).date()).start


def test_unknown_timezone_rejected():
    with pytest.raises(ValueError, match="unknown timezone"):
        SessionDefinition("x", "Mars/Base", time(1, 0), time(2, 0))


def test_previous_completed_session_has_actually_closed():
    """ATTACK: ask for 'yesterday' during yesterday's session."""
    mid_session = datetime(2024, 1, 15, 15, 0, tzinfo=UTC)  # inside London
    previous = LONDON.previous_completed(mid_session)
    assert previous.end <= mid_session
    assert previous.session_date < datetime(2024, 1, 15).date()


def test_machine_timezone_is_never_consulted():
    """Windows are absolute instants regardless of TZ env."""
    import os

    original = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "Pacific/Kiritimati"
        shifted = LONDON.window_for(datetime(2024, 1, 15).date())
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
    assert shifted.start == LONDON.window_for(datetime(2024, 1, 15).date()).start


# -- feature calculations --------------------------------------------------


def test_atr_matches_manual_wilder_smoothing():
    store = series(20, "1h")
    snapshot = futures.atr(store, "ES", "1h", T0 + 20 * HOUR, window=14)
    assert snapshot.usable
    assert snapshot.value == pytest.approx(4.0, abs=0.5)  # range is 4 each bar


def test_atr_missing_when_history_too_short():
    store = series(3, "1h")
    snapshot = futures.atr(store, "ES", "1h", T0 + 3 * HOUR, window=14)
    assert snapshot.value is None
    assert snapshot.data_quality is DataQuality.MISSING


def test_true_range_uses_previous_close():
    store = InMemoryStore()
    store.append(bar(T0, 100, 101, 99, 100))
    store.append(bar(T0 + HOUR, 100, 105, 100, 104))
    snapshot = futures.true_range(store, "ES", "1h", T0 + 2 * HOUR)
    assert snapshot.value == pytest.approx(5.0)  # |105 - 100| = 5


def test_bar_return_and_gap():
    store = series(5, "1h")
    assert futures.bar_return(store, "ES", "1h", T0 + 5 * HOUR).usable
    assert futures.gap(store, "ES", "1h", T0 + 5 * HOUR).usable


def test_realized_volatility_is_positive():
    store = series(30, "1h")
    snapshot = futures.realized_volatility(store, "ES", "1h", T0 + 30 * HOUR, window=20)
    assert snapshot.usable and snapshot.value > 0


def test_range_expansion_is_ratio_to_baseline():
    store = series(25, "1h")
    snapshot = futures.range_expansion(store, "ES", "1h", T0 + 25 * HOUR, window=20)
    assert snapshot.usable and snapshot.value == pytest.approx(1.0, abs=0.01)


# -- market structure ------------------------------------------------------


def test_swings_exclude_the_unconfirmable_tail():
    """A pivot needs right-side bars; the last N can never be pivots."""
    store = InMemoryStore()
    highs = [100, 101, 108, 102, 100, 101, 115]  # bar 2 is a pivot, bar 6 is not yet
    for i, h in enumerate(highs):
        store.append(bar(T0 + i * HOUR, h - 1, h, h - 3, h - 1))

    swing_highs, _ = futures.swings(store, "ES", "1h", T0 + 7 * HOUR, left=2, right=2)
    indices = [i for i, _ in swing_highs]
    assert 2 in indices          # confirmed
    assert 6 not in indices      # highest bar, but nothing confirms it yet


def test_structure_and_trend_state():
    store = oscillating(60)
    trend = futures.trend_state(store, "ES", "1h", T0 + 60 * HOUR)
    assert trend.usable
    assert trend.value in ("up", "down", "range")

    structure = futures.structure_state(store, "ES", "1h", T0 + 60 * HOUR)
    assert structure.usable
    assert structure.value["high"] in ("higher_high", "lower_high")
    assert structure.value["low"] in ("higher_low", "lower_low")


def test_structure_is_missing_when_no_pivots_exist():
    """A monotonic series has no local extrema -- MISSING, not a fabricated state."""
    trend = futures.trend_state(series(40, "1h", step=2.0), "ES", "1h", T0 + 40 * HOUR)
    assert trend.value is None
    assert trend.data_quality is DataQuality.MISSING


def test_break_of_structure_reports_both_directions():
    store = oscillating(60)
    snapshot = futures.break_of_structure(store, "ES", "1h", T0 + 40 * HOUR)
    if snapshot.usable:
        assert set(snapshot.value) == {"up", "down"}


def test_displacement_is_measured_in_atr_units():
    store = oscillating(40)
    snapshot = futures.displacement(store, "ES", "1h", T0 + 40 * HOUR)
    assert snapshot.usable and snapshot.value > 0


# -- session VWAP and previous levels --------------------------------------


def london_store(n=8):
    """Bars inside the London session on 2024-01-15."""
    store = InMemoryStore()
    start = datetime(2024, 1, 15, 8, 0, tzinfo=UTC)
    for i in range(n):
        price = 100.0 + i
        store.append(bar(start + i * HOUR, price, price + 1, price - 1, price, v=100.0))
    return store, start


def test_session_vwap_is_within_the_price_range():
    store, start = london_store()
    snapshot = futures.session_vwap(store, "ES", "1h", start + 5 * HOUR, LONDON)
    assert snapshot.usable
    assert 100.0 <= snapshot.value <= 108.0
    assert snapshot.availability_rule is AvailabilityRule.INTRABAR


def test_session_vwap_outside_the_session_is_not_applicable():
    store, start = london_store()
    outside = datetime(2024, 1, 15, 3, 0, tzinfo=UTC)
    snapshot = futures.session_vwap(store, "ES", "1h", outside, LONDON)
    assert snapshot.data_quality is DataQuality.NOT_APPLICABLE


def test_session_vwap_excludes_bars_from_other_sessions():
    """ATTACK: bars from a later session must not enter this VWAP."""
    store, start = london_store()
    future_bar_time = start + 48 * HOUR
    store.append(bar(future_bar_time, 500, 501, 499, 500, v=10_000.0))

    snapshot = futures.session_vwap(store, "ES", "1h", start + 5 * HOUR, LONDON)
    assert snapshot.value < 200  # the 500-priced bar is nowhere near


def test_missing_volume_is_not_treated_as_zero():
    """A bar with absent volume is excluded and the result flagged, not weighted 0."""
    store, start = london_store(4)
    store.append(Observation(
        key="ES", kind="ohlcv", event_time=start + 4 * HOUR,
        available_at=start + 5 * HOUR, ingested_at=start + 5 * HOUR,
        source="test", timeframe="1h",
        value={"open": 200.0, "high": 201.0, "low": 199.0, "close": 200.0, "volume": None},
    ))
    snapshot = futures.session_vwap(store, "ES", "1h", start + 6 * HOUR, LONDON)
    assert snapshot.data_quality is DataQuality.STALE  # degraded, not silently fine
    assert snapshot.value < 150  # the volume-less bar did not distort it


def test_vwap_distance_relative_to_vwap():
    store, start = london_store()
    snapshot = futures.vwap_distance(store, "ES", "1h", start + 5 * HOUR, LONDON)
    assert snapshot.usable and abs(snapshot.value) < 0.5


def test_previous_day_level_available_only_after_that_session_closed():
    """ATTACK: read 'yesterday's high' before yesterday ended."""
    store = InMemoryStore()
    day_one = datetime(2024, 1, 15, 8, 0, tzinfo=UTC)
    for i in range(8):
        store.append(bar(day_one + i * HOUR, 100 + i, 110 + i, 95 + i, 100 + i))

    session_end = LONDON.window_for(datetime(2024, 1, 15).date()).end
    during = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)

    # During the session the level is not yet a *previous* day level.
    snapshot = futures.previous_period_level(store, "ES", "1h", during, LONDON, "high")
    assert snapshot.value is None or snapshot.available_at > during

    later = session_end + timedelta(days=1)
    resolved = futures.previous_period_level(store, "ES", "1h", later, LONDON, "high")
    if resolved.usable:
        assert resolved.available_at >= session_end


def test_previous_period_level_rejects_unknown_level():
    store, _ = london_store()
    with pytest.raises(ValueError, match="unknown level"):
        futures.previous_period_level(store, "ES", "1h", T0, LONDON, "midpoint")


def test_liquidity_references_are_objective_lists():
    store = oscillating(60)
    refs = futures.liquidity_references(store, "ES", "1h", T0 + 60 * HOUR)
    assert set(refs) == {"prior_swing_highs", "prior_swing_lows"}
    assert "smart_money" not in str(refs).lower()


# -- availability propagation ----------------------------------------------


def test_feature_availability_never_precedes_its_inputs():
    """The central Phase 3 invariant, re-asserted at the feature layer."""
    store = series(20, "1h")
    decision = T0 + 20 * HOUR
    for snapshot in [
        futures.atr(store, "ES", "1h", decision),
        futures.true_range(store, "ES", "1h", decision),
        futures.bar_return(store, "ES", "1h", decision),
        futures.realized_volatility(store, "ES", "1h", decision),
    ]:
        if snapshot.usable:
            bars = [o for o in store.query(decision, key="ES", kind="ohlcv")]
            latest_input = max(b.available_at for b in bars if b.provenance_id in snapshot.inputs)
            assert snapshot.available_at >= latest_input


def test_feature_never_available_before_the_bar_it_uses_closes():
    store = series(20, "1h")
    decision = T0 + 20 * HOUR
    snapshot = futures.atr(store, "ES", "1h", decision)
    assert snapshot.available_at <= decision
    assert snapshot.available_at >= T0 + HOUR


def test_manual_earlier_availability_is_refused():
    from ai_trading.storage import derive_feature

    a = Observation(key="ES", kind="ohlcv", event_time=T0, available_at=T0 + HOUR,
                    ingested_at=T0 + HOUR, source="t", value={"x": 1})
    with pytest.raises(TemporalIntegrityError, match="precedes its inputs"):
        derive_feature("f", [a], lambda v: 1.0, available_at=T0)


# -- determinism -----------------------------------------------------------


def test_features_are_deterministic_across_runs():
    """Same store, same decision time, same parameters -> identical output."""
    decision = T0 + 30 * HOUR
    values = []
    for _ in range(3):
        store = series(30, "1h")
        snapshot = futures.atr(store, "ES", "1h", decision, window=14)
        values.append((snapshot.value, snapshot.available_at, snapshot.provenance_id))
    assert len(set(values)) == 1


def test_provenance_id_is_stable_for_identical_features():
    store = series(30, "1h")
    decision = T0 + 30 * HOUR
    first = futures.atr(store, "ES", "1h", decision)
    second = futures.atr(store, "ES", "1h", decision)
    assert first.provenance_id == second.provenance_id


def test_different_parameters_give_different_values():
    store = oscillating(60)
    decision = T0 + 60 * HOUR
    assert futures.atr(store, "ES", "1h", decision, window=5).value != \
        futures.atr(store, "ES", "1h", decision, window=20).value


# -- derivatives -----------------------------------------------------------


def deriv_store(capability="native", rate=0.0001):
    store = InMemoryStore()
    store.append(Observation(
        key="BTC/USDT:USDT", kind="funding", event_time=T0, available_at=T0,
        ingested_at=T0, source="ccxt:binanceusdm",
        value={"rate": rate, "capability": capability},
    ))
    return store


def test_native_funding_rate_is_returned():
    snapshot = derivatives.funding_rate(deriv_store(), "BTC/USDT:USDT", T0 + HOUR)
    assert snapshot.usable and snapshot.value == pytest.approx(0.0001)


def test_emulated_funding_excluded_under_native_only():
    """Emulated data is derived by the library, not reported by the venue."""
    store = deriv_store(capability="emulated")
    permissive = derivatives.funding_rate(store, "BTC/USDT:USDT", T0 + HOUR)
    assert permissive.usable

    strict = derivatives.funding_rate(store, "BTC/USDT:USDT", T0 + HOUR, native_only=True)
    assert not strict.usable
    assert strict.data_quality is DataQuality.UNAVAILABLE


def test_derivative_feature_missing_when_absent():
    snapshot = derivatives.funding_rate(InMemoryStore(), "X", T0)
    assert snapshot.data_quality is DataQuality.MISSING


def test_basis_available_only_when_both_inputs_are():
    store = InMemoryStore()
    store.append(Observation(key="X", kind="mark", event_time=T0, available_at=T0,
                             ingested_at=T0, source="s",
                             value={"price": 101.0, "capability": "native"}))
    store.append(Observation(key="X", kind="index", event_time=T0,
                             available_at=T0 + 2 * HOUR, ingested_at=T0,
                             source="s", value={"price": 100.0, "capability": "native"}))

    assert not derivatives.basis(store, "X", T0 + HOUR).usable   # index not yet available
    late = derivatives.basis(store, "X", T0 + 3 * HOUR)
    assert late.usable
    assert late.value == pytest.approx(0.01)
    assert late.available_at == T0 + 2 * HOUR   # the later input governs


# -- microstructure: refuses to fabricate ----------------------------------


def test_orderbook_features_unavailable_without_book_data():
    """ATTACK: these must never be synthesized from candles."""
    store = series(20, "1h")
    for name in microstructure.ORDERBOOK_FEATURES:
        snapshot = microstructure.orderbook_feature(store, "ES", T0 + 20 * HOUR, name)
        assert snapshot.value is None
        assert snapshot.data_quality is DataQuality.UNAVAILABLE


def test_orderbook_features_compute_when_data_exists():
    store = InMemoryStore()
    store.append(Observation(
        key="ES", kind="orderbook", event_time=T0, available_at=T0, ingested_at=T0,
        source="s", value={"bids": [[99.0, 5.0]], "asks": [[101.0, 3.0]]},
    ))
    spread = microstructure.orderbook_feature(store, "ES", T0 + HOUR, "bid_ask_spread")
    assert spread.value == pytest.approx(2.0)
    mid = microstructure.orderbook_feature(store, "ES", T0 + HOUR, "mid_price")
    assert mid.value == pytest.approx(100.0)


def test_unknown_microstructure_feature_rejected():
    with pytest.raises(ValueError, match="unknown microstructure"):
        microstructure.orderbook_feature(InMemoryStore(), "ES", T0, "vibes")


def test_has_orderbook_data_reports_honestly():
    assert not microstructure.has_orderbook_data(series(5, "1h"), "ES")


# -- registry --------------------------------------------------------------


def test_registry_versions_are_immutable():
    from ai_trading.features.registry import FeatureRegistry, FeatureSpec

    registry = FeatureRegistry()
    registry.register(FeatureSpec("atr", "v1 desc", Domain.VOLATILITY))
    registry.register(FeatureSpec("atr", "v1 desc", Domain.VOLATILITY))  # identical, fine
    with pytest.raises(ValueError, match="immutable"):
        registry.register(FeatureSpec("atr", "CHANGED", Domain.VOLATILITY))


def test_registry_supports_multiple_versions():
    from ai_trading.features.registry import FeatureRegistry, FeatureSpec

    registry = FeatureRegistry()
    registry.register(FeatureSpec("atr", "d", Domain.VOLATILITY, calculation_version="1"))
    registry.register(FeatureSpec("atr", "d2", Domain.VOLATILITY, calculation_version="2"))
    assert registry.versions_of("atr") == ["1", "2"]


def test_registry_catalogues_every_domain():
    for domain in (Domain.VOLATILITY, Domain.MARKET_STRUCTURE, Domain.SESSION,
                   Domain.LIQUIDITY, Domain.DERIVATIVES, Domain.MICROSTRUCTURE):
        assert REGISTRY.by_domain(domain), f"no features registered for {domain}"


def test_microstructure_features_registered_as_unavailable():
    for spec in REGISTRY.by_domain(Domain.MICROSTRUCTURE):
        assert spec.status is FeatureStatus.UNAVAILABLE


def test_solana_features_registered_as_reserved_not_implemented():
    reserved = REGISTRY.by_domain(Domain.ON_CHAIN)
    assert reserved
    for spec in reserved:
        assert spec.status is FeatureStatus.RESERVED


def test_feature_keys_are_versioned():
    assert REGISTRY.require("atr:v1").key == "atr:v1"


def test_unregistered_feature_raises():
    with pytest.raises(KeyError, match="unregistered"):
        REGISTRY.require("does_not_exist:v1")


# -- data quality semantics ------------------------------------------------


def test_missing_is_distinct_from_zero():
    assert DataQuality.MISSING is not DataQuality.ZERO
    assert not DataQuality.MISSING.usable
    assert DataQuality.ZERO.usable


def test_stale_has_a_value_but_is_not_ok():
    assert DataQuality.STALE.has_value
    assert not DataQuality.STALE.usable


def test_eligibility_requires_quality_not_just_availability():
    """A value that arrived on time but is MISSING must not be used."""
    snapshot = futures.missing("atr", T0, DataQuality.MISSING, instrument="ES")
    assert not snapshot.is_eligible_at(T0 + HOUR)
