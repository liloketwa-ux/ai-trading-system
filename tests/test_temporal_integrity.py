"""Temporal integrity suite (Phase 3).

Each test below corresponds to a way future information leaks backward into a
historical decision. They are written as *attacks*: each one appends data that
arrives later and asserts the earlier reconstructed state is untouched.

The store is append-only, so "the enrichment overwrote the old value" cannot
happen by construction — these tests prove the reconstruction filter also
refuses to *read* the newer value when reconstructing an earlier instant.
"""

from datetime import datetime, timedelta, timezone

import pytest

from ai_trading.storage import (
    Restatements,
    Availability,
    InMemoryStore,
    Observation,
    TemporalIntegrityError,
    UnknownAvailabilityError,
    derive_feature,
)
from ai_trading.storage.features import FeatureSnapshot

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


def obs(kind, event, available, value, key="BTC", source="test", **kw):
    return Observation(
        key=key, kind=kind, event_time=event, available_at=available,
        ingested_at=available or event, source=source, value=value, **kw
    )


# -- 1. future enrichment cannot leak backward ------------------------------


def test_later_enrichment_does_not_alter_earlier_state():
    """The Pumpi hazard: enrichment describing Monday, fetched Friday."""
    store = InMemoryStore()
    store.append(obs("liquidity", T0, T0, {"usd": 50_000}))
    # Same event time, but only knowable four days later.
    store.append(obs("liquidity", T0, T0 + 4 * DAY, {"usd": 900_000}))

    monday = store.reconstruct_state(T0 + HOUR, "BTC")
    assert monday["liquidity"].value["usd"] == 50_000

    friday = store.reconstruct_state(T0 + 5 * DAY, "BTC")
    assert friday["liquidity"].value["usd"] == 900_000


# -- 2. late social data ----------------------------------------------------


def test_late_arriving_social_data_cannot_leak_backward():
    store = InMemoryStore()
    store.append(obs("social", T0 + HOUR, T0 + 6 * HOUR, {"mentions": 5_000}))

    assert "social" not in store.reconstruct_state(T0 + 2 * HOUR, "BTC")
    assert store.reconstruct_state(T0 + 7 * HOUR, "BTC")["social"].value["mentions"] == 5_000


# -- 3. future news ---------------------------------------------------------


def test_future_news_is_absent_from_earlier_market_state():
    store = InMemoryStore()
    store.append(obs("news", T0 + 2 * DAY, T0 + 2 * DAY, {"headline": "rate cut"}))
    assert store.reconstruct_state(T0, "BTC") == {}


def test_news_publication_and_event_time_are_distinct():
    """A release embargoed until publication is not knowable at its event time."""
    store = InMemoryStore()
    store.append(obs("news", T0, T0 + 3 * HOUR, {"headline": "embargoed"}))
    assert store.latest(T0 + HOUR, "BTC", "news") is None
    assert store.latest(T0 + 4 * HOUR, "BTC", "news") is not None


# -- 4. future wallet events ------------------------------------------------


def test_future_wallet_activity_absent_from_earlier_wallet_intelligence():
    store = InMemoryStore()
    store.append(obs("wallet", T0, T0, {"trades": 10}, key="wallet1"))
    store.append(obs("wallet", T0 + DAY, T0 + DAY, {"trades": 250}, key="wallet1"))

    assert store.latest(T0 + HOUR, "wallet1", "wallet").value["trades"] == 10


# -- 5. future liquidity snapshots ------------------------------------------


def test_later_liquidity_snapshot_does_not_alter_earlier_liquidity():
    store = InMemoryStore()
    for offset, usd in [(0, 10_000), (1, 40_000), (2, 5_000)]:
        store.append(obs("liquidity", T0 + offset * HOUR, T0 + offset * HOUR, {"usd": usd}))

    assert store.latest(T0, "BTC", "liquidity").value["usd"] == 10_000
    assert store.latest(T0 + HOUR, "BTC", "liquidity").value["usd"] == 40_000
    assert store.latest(T0 + 2 * HOUR, "BTC", "liquidity").value["usd"] == 5_000


# -- 6. future holder snapshots ---------------------------------------------


def test_later_holder_counts_do_not_alter_earlier_features():
    store = InMemoryStore()
    store.append(obs("holders", T0, T0, {"count": 120}))
    store.append(obs("holders", T0 + DAY, T0 + DAY, {"count": 9_000}))
    assert store.latest(T0 + HOUR, "BTC", "holders").value["count"] == 120


# -- 7. candle availability equals its close --------------------------------


def test_candle_is_available_at_its_close_not_its_open():
    """A bar open at 00:00 on a 1h timeframe is knowable at 01:00."""
    store = InMemoryStore()
    store.append(obs("ohlcv", T0, T0 + HOUR, {"close": 100.0}, timeframe="1h"))

    assert store.latest(T0 + timedelta(minutes=59), "BTC", "ohlcv") is None
    assert store.latest(T0 + HOUR, "BTC", "ohlcv") is not None


def test_availability_before_event_is_rejected_at_construction():
    with pytest.raises(TemporalIntegrityError, match="precedes"):
        obs("ohlcv", T0, T0 - HOUR, {"close": 1.0})


# -- 8. derived features inherit the latest input availability --------------


def test_derived_feature_availability_is_the_max_over_inputs():
    early = obs("ohlcv", T0, T0 + HOUR, {"close": 100.0})
    late = obs("liquidity", T0, T0 + 6 * HOUR, {"usd": 1.0})

    feature = derive_feature("combo", [early, late], lambda vals: 1.0)
    assert feature.available_at == T0 + 6 * HOUR


def test_derived_feature_cannot_declare_earlier_availability_than_inputs():
    early = obs("ohlcv", T0, T0 + HOUR, {"close": 100.0})
    late = obs("liquidity", T0, T0 + 6 * HOUR, {"usd": 1.0})

    with pytest.raises(TemporalIntegrityError, match="precedes its inputs"):
        derive_feature("combo", [early, late], lambda v: 1.0, available_at=T0 + 2 * HOUR)


def test_derived_feature_rejects_inputs_with_unknown_availability():
    known = obs("ohlcv", T0, T0 + HOUR, {"close": 1.0})
    unknown = obs("liquidity", T0, None, {"usd": 1.0})
    with pytest.raises(TemporalIntegrityError, match="UNKNOWN_AVAILABILITY"):
        derive_feature("combo", [known, unknown], lambda v: 1.0)


def test_chained_derivation_propagates_availability():
    a = obs("ohlcv", T0, T0 + HOUR, {"x": 1})
    b = obs("liquidity", T0, T0 + 3 * HOUR, {"y": 2})
    first = derive_feature("f1", [a, b], lambda v: 1.0)
    second = derive_feature("f2", [first, a], lambda v: 2.0)
    assert second.available_at == T0 + 3 * HOUR


def test_feature_snapshot_eligibility_uses_availability_not_order():
    snapshot = FeatureSnapshot("f", 1.0, T0, T0 + 2 * HOUR, "test")
    assert not snapshot.is_eligible_at(T0 + HOUR)
    assert snapshot.is_eligible_at(T0 + 2 * HOUR)


# -- 9. merged views keep the latest VALID observation only -----------------


def test_merge_keeps_the_latest_eligible_record_not_the_latest_record():
    store = InMemoryStore()
    store.append(obs("price", T0, T0, {"p": 1.0}))
    store.append(obs("price", T0 + HOUR, T0 + HOUR, {"p": 2.0}))
    store.append(obs("price", T0 + 2 * HOUR, T0 + 2 * HOUR, {"p": 3.0}))

    state = store.reconstruct_state(T0 + HOUR, "BTC")
    assert state["price"].value["p"] == 2.0  # not 3.0
    assert len(state) == 1  # one record per kind, not all three


def test_restatement_of_the_same_instant_uses_the_latest_correction_known():
    """A later restatement of the same event is knowledge, not look-ahead.

    Both records describe T0. At T0+6h a decision genuinely knows the
    correction, so the default policy uses it. Before the correction arrives it
    is invisible — which is the property that matters.
    """
    store = InMemoryStore()
    store.append(obs("price", T0, T0 + 5 * HOUR, {"p": "corrected"}))
    store.append(obs("price", T0, T0 + HOUR, {"p": "initial"}))

    assert store.reconstruct_state(T0 + 2 * HOUR, "BTC")["price"].value["p"] == "initial"
    assert store.reconstruct_state(T0 + 6 * HOUR, "BTC")["price"].value["p"] == "corrected"


def test_first_known_policy_ignores_later_restatements():
    """Modelling a live system that never revisits a decision."""
    store = InMemoryStore()
    store.append(obs("price", T0, T0 + 5 * HOUR, {"p": "corrected"}))
    store.append(obs("price", T0, T0 + HOUR, {"p": "initial"}))

    state = store.reconstruct_state(
        T0 + 6 * HOUR, "BTC", restatements=Restatements.FIRST_KNOWN
    )
    assert state["price"].value["p"] == "initial"


def test_reconstruct_state_spans_kinds():
    store = InMemoryStore()
    store.append(obs("ohlcv", T0, T0, {"close": 1.0}))
    store.append(obs("liquidity", T0, T0, {"usd": 2.0}))
    store.append(obs("holders", T0 + DAY, T0 + DAY, {"count": 3}))

    state = store.reconstruct_state(T0 + HOUR, "BTC")
    assert set(state) == {"ohlcv", "liquidity"}


# -- 10. fail closed on missing provenance ----------------------------------


def test_query_fails_closed_when_availability_is_unknown():
    """A backtest must stop, not silently run on a filtered subset."""
    store = InMemoryStore()
    store.append(obs("liquidity", T0, None, {"usd": 1.0}))

    with pytest.raises(UnknownAvailabilityError, match="UNKNOWN_AVAILABILITY"):
        store.query(T0 + DAY, key="BTC")


def test_unknown_availability_excluded_when_explicitly_non_strict():
    store = InMemoryStore()
    store.append(obs("liquidity", T0, None, {"usd": 1.0}))
    store.append(obs("liquidity", T0, T0, {"usd": 2.0}))

    eligible = store.query(T0 + DAY, key="BTC", strict=False)
    assert len(eligible) == 1
    assert eligible[0].value["usd"] == 2.0


def test_unknown_availability_is_never_usable():
    record = obs("x", T0, None, {})
    assert record.availability is Availability.UNKNOWN
    assert not record.is_available_at(T0 + 10 * DAY)


def test_unresolved_records_are_listed_for_triage():
    store = InMemoryStore()
    store.append(obs("a", T0, None, {}))
    store.append(obs("b", T0, T0, {}))
    assert len(store.unresolved()) == 1


def test_resolving_availability_creates_a_new_record():
    """Resolution appends; it does not mutate the original."""
    original = obs("liquidity", T0, None, {"usd": 1.0})
    resolved = original.with_availability(T0 + HOUR)

    assert original.available_at is None  # untouched
    assert resolved.available_at == T0 + HOUR
    assert resolved.provenance_id != original.provenance_id


# -- append-only invariant --------------------------------------------------


def test_store_is_append_only():
    store = InMemoryStore()
    record = obs("price", T0, T0, {"p": 1.0})
    store.append(record)

    # Same id, different content -> corruption, must raise.
    forged = Observation(
        key=record.key, kind=record.kind, event_time=record.event_time,
        available_at=record.available_at, ingested_at=record.ingested_at,
        source=record.source, value={"p": 999.0}, provenance_id=record.provenance_id,
    )
    with pytest.raises(TemporalIntegrityError, match="append-only"):
        store.append(forged)


def test_reappending_an_identical_record_is_idempotent():
    """Ingestion retries are expected and must not duplicate."""
    store = InMemoryStore()
    record = obs("price", T0, T0, {"p": 1.0})
    assert store.append(record) == 1
    assert store.append(record) == 0
    assert store.count() == 1


def test_store_exposes_no_update_or_delete():
    store = InMemoryStore()
    for forbidden in ("update", "delete", "overwrite", "replace"):
        assert not hasattr(store, forbidden), f"append-only store exposes {forbidden}()"
