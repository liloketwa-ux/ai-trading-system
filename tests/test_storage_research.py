"""Tests for dataset versioning, splits, holdout locking, and the registries."""

from datetime import datetime, timedelta, timezone

import pytest

from ai_trading.research import (
    ExperimentRegistry,
    ExperimentStatus,
    HoldoutLedger,
    HoldoutViolation,
    Purpose,
    SplitDefinition,
    SplitRegistry,
)
from ai_trading.solana import (
    ADAPTER_REGISTRY,
    AdapterHealth,
    AdapterState,
    normalize_pumpi_trade,
)
from ai_trading.storage import (
    InMemoryStore,
    Observation,
    ParquetStore,
    build_dataset_version,
)
from ai_trading.storage.dataset import DatasetVersion

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
DAY = timedelta(days=1)


def obs(kind="ohlcv", offset=0, key="BTC", value=None):
    at = T0 + offset * DAY
    return Observation(
        key=key, kind=kind, event_time=at, available_at=at, ingested_at=at,
        source="test", value=value or {"close": 100.0 + offset},
    )


def populated():
    store = InMemoryStore()
    store.append([obs(offset=i) for i in range(10)])
    return store


# -- dataset versioning ----------------------------------------------------


def test_dataset_version_is_content_addressed():
    store = populated()
    a = build_dataset_version(store, dataset_id="d", as_of=T0 + 20 * DAY)
    b = build_dataset_version(store, dataset_id="d", as_of=T0 + 20 * DAY)
    assert a.checksum == b.checksum
    assert a.row_count == 10


def test_appending_data_changes_the_checksum():
    store = populated()
    before = build_dataset_version(store, dataset_id="d", as_of=T0 + 20 * DAY)
    store.append(obs(offset=99))
    after = build_dataset_version(store, dataset_id="d", as_of=T0 + 200 * DAY)
    assert before.checksum != after.checksum


def test_dataset_version_is_itself_point_in_time():
    """Rebuilding with the same as_of gives the same checksum after growth."""
    store = populated()
    original = build_dataset_version(store, dataset_id="d", as_of=T0 + 5 * DAY)
    store.append(obs(offset=50))
    rebuilt = build_dataset_version(store, dataset_id="d", as_of=T0 + 5 * DAY)
    assert original.checksum == rebuilt.checksum


def test_dataset_version_verifies_against_the_store():
    store = populated()
    version = build_dataset_version(store, dataset_id="d", as_of=T0 + 20 * DAY)
    assert version.verify(store)


def test_dataset_version_round_trips_through_disk(tmp_path):
    store = populated()
    version = build_dataset_version(store, dataset_id="d", as_of=T0 + 20 * DAY)
    path = version.save(tmp_path / "d.json")
    assert DatasetVersion.load(path).checksum == version.checksum


def test_dataset_records_reproducibility_metadata():
    version = build_dataset_version(populated(), dataset_id="d", as_of=T0 + 20 * DAY)
    assert version.code_commit
    assert version.sources == ("test",)
    assert version.kinds == ("ohlcv",)


def test_empty_dataset_is_refused():
    with pytest.raises(ValueError, match="no observations"):
        build_dataset_version(InMemoryStore(), dataset_id="d", as_of=T0)


# -- parquet store ---------------------------------------------------------


def test_parquet_store_round_trips(tmp_path):
    store = ParquetStore(tmp_path / "obs")
    store.append([obs(offset=i) for i in range(5)])

    reopened = ParquetStore(tmp_path / "obs")
    assert reopened.count() == 5
    assert reopened.latest(T0 + 10 * DAY, "BTC", "ohlcv").value["close"] == 104.0


def test_parquet_store_preserves_point_in_time(tmp_path):
    store = ParquetStore(tmp_path / "obs")
    store.append([obs(offset=i) for i in range(5)])
    assert ParquetStore(tmp_path / "obs").latest(T0 + DAY, "BTC", "ohlcv").value["close"] == 101.0


# -- splits and holdout locking --------------------------------------------


def make_registry(tmp_path, split_id="s1"):
    return SplitRegistry.create(
        split_id=split_id,
        dev=(T0, T0 + 100 * DAY),
        validation=(T0 + 100 * DAY, T0 + 150 * DAY),
        holdout=(T0 + 150 * DAY, T0 + 200 * DAY),
        ledger_path=tmp_path / "HOLDOUT_TOUCHES.md",
    )


def test_windows_are_disjoint_and_ordered():
    with pytest.raises(ValueError, match="overlaps"):
        SplitDefinition(
            "s", "1",
            T0, T0 + 100 * DAY,
            T0 + 50 * DAY, T0 + 150 * DAY,   # overlaps development
            T0 + 150 * DAY, T0 + 200 * DAY,
            T0, "abc",
        )


def test_empty_window_is_rejected():
    with pytest.raises(ValueError, match="empty or inverted"):
        SplitDefinition("s", "1", T0, T0, T0 + DAY, T0 + 2 * DAY,
                        T0 + 3 * DAY, T0 + 4 * DAY, T0, "abc")


@pytest.mark.parametrize(
    "purpose", [Purpose.EXPLORATION, Purpose.TRAINING, Purpose.PARAMETER_SWEEP]
)
def test_optimization_purposes_get_the_development_window(tmp_path, purpose):
    registry = make_registry(tmp_path)
    assert registry.window(purpose) == (T0, T0 + 100 * DAY)


def test_parameter_sweep_cannot_reach_the_holdout(tmp_path):
    registry = make_registry(tmp_path)
    with pytest.raises(HoldoutViolation, match="locked holdout"):
        registry.split.assert_no_holdout(
            T0, T0 + 199 * DAY, Purpose.PARAMETER_SWEEP
        )


def test_holdout_window_is_not_reachable_through_window(tmp_path):
    """Even asking for it by purpose is refused — it must go through the ledger."""
    registry = make_registry(tmp_path)
    with pytest.raises(HoldoutViolation, match="evaluate_holdout"):
        registry.window(Purpose.FINAL_HOLDOUT_EVAL)


def test_holdout_evaluation_is_recorded(tmp_path):
    registry = make_registry(tmp_path)
    window = registry.evaluate_holdout("v1", "final evaluation of frozen strategy")
    assert window == (T0 + 150 * DAY, T0 + 200 * DAY)
    assert registry.ledger.touches() == 1
    assert "v1" in registry.ledger.versions_evaluated()


def test_reevaluating_the_same_version_is_refused(tmp_path):
    """The holdout is spent for a version once seen."""
    registry = make_registry(tmp_path)
    registry.evaluate_holdout("v1", "first look")
    with pytest.raises(HoldoutViolation, match="already been evaluated"):
        registry.evaluate_holdout("v1", "just one more look")


def test_a_new_research_version_may_use_a_new_holdout(tmp_path):
    registry = make_registry(tmp_path)
    registry.evaluate_holdout("v1", "first")
    registry.evaluate_holdout("v2", "strategy modified, new version")
    assert registry.ledger.touches() == 2


def test_ledger_persists_across_processes(tmp_path):
    make_registry(tmp_path).evaluate_holdout("v1", "first")
    assert HoldoutLedger(tmp_path / "HOLDOUT_TOUCHES.md").touches() == 1


def test_split_checksum_changes_with_dates(tmp_path):
    a = make_registry(tmp_path, "a").split
    b = SplitDefinition(
        "a", "1", T0, T0 + 90 * DAY, T0 + 100 * DAY, T0 + 150 * DAY,
        T0 + 150 * DAY, T0 + 200 * DAY, T0, "abc",
    )
    assert a.checksum != b.checksum


def test_non_holdout_range_passes_the_guard(tmp_path):
    registry = make_registry(tmp_path)
    registry.split.assert_no_holdout(T0, T0 + 100 * DAY, Purpose.PARAMETER_SWEEP)


# -- experiment registry ---------------------------------------------------


def make_experiment(registry, **kw):
    return registry.create(
        strategy_version=kw.get("strategy_version", "s1"),
        feature_version="f1",
        dataset_version="d1",
        parameters=kw.get("parameters", {"window": 20}),
        seed=kw.get("seed", 42),
        execution_assumptions={"commission_bps": 2, "slippage_bps": 3},
    )


def test_experiment_records_reproduction_metadata(tmp_path):
    registry = ExperimentRegistry(tmp_path / "experiments.jsonl")
    experiment = make_experiment(registry)
    assert experiment.code_commit
    assert experiment.seed == 42
    assert experiment.status is ExperimentStatus.CREATED
    assert experiment.execution_assumptions["commission_bps"] == 2


def test_experiments_persist_across_instances(tmp_path):
    path = tmp_path / "experiments.jsonl"
    experiment = make_experiment(ExperimentRegistry(path))
    assert ExperimentRegistry(path).get(experiment.experiment_id) is not None


def test_completing_an_experiment_stores_metrics(tmp_path):
    registry = ExperimentRegistry(tmp_path / "e.jsonl")
    experiment = make_experiment(registry)
    completed = registry.complete(experiment.experiment_id, {"sharpe": 1.2})
    assert completed.status is ExperimentStatus.COMPLETED
    assert completed.metrics["sharpe"] == 1.2


def test_trial_count_tracks_sweeps(tmp_path):
    """The number a deflated Sharpe needs, counted automatically."""
    registry = ExperimentRegistry(tmp_path / "e.jsonl")
    for window in range(10, 60, 10):
        experiment = make_experiment(registry, parameters={"window": window})
        registry.complete(experiment.experiment_id, {"sharpe": 1.0})
    assert registry.trial_count(strategy_version="s1") == 5


def test_incomplete_experiments_excluded_from_trial_count_by_default(tmp_path):
    registry = ExperimentRegistry(tmp_path / "e.jsonl")
    make_experiment(registry)
    assert registry.trial_count() == 0
    assert registry.trial_count(completed_only=False) == 1


def test_duplicate_reproduction_key_is_detectable(tmp_path):
    registry = ExperimentRegistry(tmp_path / "e.jsonl")
    first = make_experiment(registry)
    second = make_experiment(registry)
    assert registry.find_duplicate(second).experiment_id == first.experiment_id


def test_different_seed_is_not_a_duplicate(tmp_path):
    registry = ExperimentRegistry(tmp_path / "e.jsonl")
    make_experiment(registry, seed=1)
    other = make_experiment(registry, seed=2)
    assert registry.find_duplicate(other) is None


def test_holdout_evaluations_are_flagged(tmp_path):
    registry = ExperimentRegistry(tmp_path / "e.jsonl")
    experiment = make_experiment(registry)
    registry.mark_holdout(experiment.experiment_id, (T0, T0 + DAY))
    assert len(registry.holdout_evaluations()) == 1


def test_failed_experiment_records_the_reason(tmp_path):
    registry = ExperimentRegistry(tmp_path / "e.jsonl")
    experiment = make_experiment(registry)
    failed = registry.fail(experiment.experiment_id, "data quality gate rejected input")
    assert failed.status is ExperimentStatus.FAILED
    assert "quality gate" in failed.notes


# -- Pumpi normalization ---------------------------------------------------


PUMPI_PAYLOAD = {
    "type": "trade",
    "trade": {
        "tokenAddress": "So111", "traderAddress": "Trader1", "isBuy": True,
        "ethAmount": "1.5", "tokenAmount": "1000", "priceEth": "0.0015",
        "txHash": "sig123", "platform": "pump_fun", "timestamp": "2024-01-01T00:00:00Z",
    },
    "token": {
        "address": "So111", "symbol": "PUMP", "name": "Pump Token",
        "marketCapEth": "45.2", "chain": "solana", "platform": "pump_fun",
    },
}


def test_eth_named_fields_are_renamed_to_chain_neutral_quote_terms():
    """The legacy EVM leak: Pumpi says 'eth' on a Solana system."""
    event = normalize_pumpi_trade(PUMPI_PAYLOAD)
    assert event.quote_amount == 1.5
    assert event.quote_price == 0.0015
    assert event.market_cap_quote == 45.2
    assert event.quote_asset == "SOL"
    assert event.chain == "solana"


def test_raw_field_names_are_preserved_for_debugging():
    event = normalize_pumpi_trade(PUMPI_PAYLOAD)
    assert event.raw["ethAmount"] == "1.5"


def test_normalized_event_has_no_eth_named_attributes():
    """Nothing downstream may see the legacy names."""
    event = normalize_pumpi_trade(PUMPI_PAYLOAD)
    assert not [a for a in dir(event) if "eth" in a.lower() and not a.startswith("_")]


def test_provenance_is_preserved():
    event = normalize_pumpi_trade(PUMPI_PAYLOAD)
    assert event.transaction_hash == "sig123"
    assert event.trader_address == "Trader1"
    assert event.parser_version.startswith("pumpi-normalizer/")


def test_side_derived_from_is_buy():
    assert normalize_pumpi_trade(PUMPI_PAYLOAD).side == "buy"
    sell = {**PUMPI_PAYLOAD, "trade": {**PUMPI_PAYLOAD["trade"], "isBuy": False}}
    assert normalize_pumpi_trade(sell).side == "sell"


def test_missing_token_address_is_refused():
    broken = {"trade": {"timestamp": "2024-01-01T00:00:00Z"}, "token": {}}
    with pytest.raises(ValueError, match="no token address"):
        normalize_pumpi_trade(broken)


def test_missing_timestamp_is_refused_without_a_default():
    broken = {"trade": {"tokenAddress": "So111"}, "token": {}}
    with pytest.raises(ValueError, match="no timestamp"):
        normalize_pumpi_trade(broken)


def test_normalized_event_converts_to_an_observation():
    event = normalize_pumpi_trade(PUMPI_PAYLOAD)
    observation = event.to_observation(ingested_at=T0 + DAY)
    assert observation.kind == "solana_trade"
    assert observation.source == "pumpi:pump_fun"
    assert observation.raw_ref == "sig123"
    assert observation.value["quote_amount"] == 1.5


def test_indexing_lag_can_be_declared_explicitly():
    event = normalize_pumpi_trade(PUMPI_PAYLOAD)
    observation = event.to_observation(
        ingested_at=T0 + DAY, available_at=T0 + timedelta(seconds=30)
    )
    assert observation.available_at == T0 + timedelta(seconds=30)


# -- adapter lifecycle -----------------------------------------------------


def test_source_existing_is_not_production_ready():
    assert not AdapterState.PRESENT.usable_for_research
    assert not AdapterState.UNIT_TESTED.usable_for_research
    assert AdapterState.HISTORICALLY_VALIDATED.usable_for_research


def test_audit_findings_are_encoded_in_the_registry():
    """Only three adapters are actually started by Pumpi; none is verified here."""
    assert ADAPTER_REGISTRY["raydium_launchlab"].state is AdapterState.UNIT_TESTED
    for dormant in ("meteora", "orca", "moonshot", "letsbonk", "raydium_amm"):
        assert ADAPTER_REGISTRY[dormant].state is AdapterState.PRESENT
        assert "NOT started" in ADAPTER_REGISTRY[dormant].notes


def test_no_adapter_currently_claims_research_readiness():
    assert not any(a.usable_for_research for a in ADAPTER_REGISTRY.values())


def test_promotion_requires_evidence():
    health = AdapterHealth("x", "X")
    with pytest.raises(ValueError, match="evidence"):
        health.promote(AdapterState.UNIT_TESTED, "  ")


def test_promotion_cannot_skip_levels():
    health = AdapterHealth("x", "X", AdapterState.PRESENT)
    with pytest.raises(ValueError, match="cannot skip"):
        health.promote(AdapterState.PRODUCTION_ENABLED, "looks fine")


def test_promotion_cannot_go_backwards():
    health = AdapterHealth("x", "X", AdapterState.RUNTIME_VERIFIED)
    with pytest.raises(ValueError, match="forward only"):
        health.promote(AdapterState.PRESENT, "regression")


def test_promotion_records_evidence():
    health = AdapterHealth("x", "X")
    health.promote(AdapterState.UNIT_TESTED, "decode tests added")
    assert health.state is AdapterState.UNIT_TESTED
    assert "decode tests added" in health.evidence[0]


def test_demote_disables_regardless_of_level():
    health = AdapterHealth("x", "X", AdapterState.PRODUCTION_ENABLED)
    health.demote("decode drift detected")
    assert health.state is AdapterState.DISABLED
    assert not health.usable_for_research
