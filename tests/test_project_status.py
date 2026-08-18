"""EVIDENCE_PENDING status, the real-data gate, the status command, the audit.

Each test targets a property nothing else covers. The status object is
interesting mostly for what it *cannot* say, so several tests assert absences:
no timestamp, no hand-typed status string, no path from synthetic data to a
market claim.
"""

from datetime import datetime, timedelta, timezone

import pytest

from ai_trading.project import (
    ExternalAction,
    LiveExecutionStatus,
    MarketClaimStatus,
    ProjectPhase,
    RealDataPending,
    RealDataStatus,
    resolve_status,
    run_integrity_audit,
)
from ai_trading.project.audit import CHECK_NAMES, Severity
from ai_trading.project.cli import build_parser, main, render_status
from ai_trading.project.gate import (
    REAL_DATA_PENDING_MESSAGE,
    may_run_ict_family,
    require_real_data_approved,
    run_ict_family_campaign,
)
from ai_trading.project.status import (
    PRIMARY_PROP_TARGET,
    TargetUnresolved,
    collect_test_count,
)

UTC = timezone.utc
T0 = datetime(2026, 3, 2, 14, 30, tzinfo=UTC)


def a_status(**kw):
    return resolve_status(include_test_count=False, **kw)


def approved_dataset():
    """A REAL_MARKET dataset that clears the ladder."""
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

    class Dataset:
        origin = DataOrigin.REAL_MARKET
        grades = assess_grades(source_name="databento",
                               origin=DataOrigin.REAL_MARKET,
                               quality_report=run_quality_gate(
                                   bars, provider="databento"),
                               point_in_time_clean=True)

    return Dataset()


def synthetic_dataset():
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

    class Dataset:
        origin = DataOrigin.SYNTHETIC
        grades = assess_grades(source_name="fixture",
                               origin=DataOrigin.SYNTHETIC,
                               quality_report=run_quality_gate(
                                   bars, provider="fixture"),
                               point_in_time_clean=True)

    return Dataset()


# =========================================================================
# EVIDENCE_PENDING
# =========================================================================


def test_the_project_resolves_to_evidence_pending():
    status = a_status()
    assert status.project_status is ProjectPhase.EVIDENCE_PENDING
    assert status.real_data_status is RealDataStatus.NOT_AVAILABLE
    assert status.market_claim_status is MarketClaimStatus.BLOCKED
    assert status.live_execution_status is LiveExecutionStatus.DISABLED
    assert status.primary_prop_target == "TOPSTEP_COMBINE_100K"
    assert status.next_required_external_action is \
        ExternalAction.PROVIDE_APPROVED_REAL_NQ_DATA


def test_the_ten_declared_fields_are_all_present():
    payload = a_status().to_dict()
    for field in ("project_status", "research_protocol_version",
                  "ict_family_version", "ict_family_fingerprint", "test_count",
                  "real_data_status", "market_claim_status",
                  "live_execution_status", "primary_prop_target",
                  "next_required_external_action"):
        assert field in payload, field


def test_the_fingerprint_is_verified_not_quoted():
    """resolve_status calls verify_frozen, so drift breaks the status too."""
    assert a_status().ict_family_fingerprint == "b3ebb0af7f01b137"


def test_the_status_is_derived_from_the_family_not_typed():
    from ai_trading.research.ict_family import ICT_FAMILY_V1

    status = a_status()
    assert status.declared_trials == ICT_FAMILY_V1.trial_count == 36
    assert status.ict_family_version == ICT_FAMILY_V1.version
    assert status.ict_family_locked


def test_real_data_status_is_derived_from_the_provider_registry():
    from ai_trading.history.cli import PROVIDER_REGISTRY

    status = a_status()
    assert tuple(sorted(PROVIDER_REGISTRY)) == status.registered_providers == ()
    assert status.real_data_status is RealDataStatus.NOT_AVAILABLE


def test_live_execution_status_is_derived_from_broker_subclasses():
    """Not a config flag: an added live adapter flips this without a setting."""
    status = a_status()
    assert status.live_broker_implementations == ()
    assert status.live_execution_status is LiveExecutionStatus.DISABLED


def test_a_test_double_is_not_a_live_route():
    """Test doubles subclass Broker; only shipped adapters count."""
    from ai_trading.execution.broker import Broker

    class DoubleInATest(Broker):
        def submit(self, *args, **kwargs): raise NotImplementedError
        def cancel(self, *args, **kwargs): raise NotImplementedError

    assert DoubleInATest.__module__.split(".")[0] != "ai_trading"
    assert a_status().live_execution_status is LiveExecutionStatus.DISABLED


def test_the_prop_target_must_resolve_in_the_rules_registry():
    from ai_trading.propfirm import REGISTRY, Stage

    assert REGISTRY.resolve("topstep", "trading_combine", Stage.EVALUATION,
                            100_000) is not None
    assert PRIMARY_PROP_TARGET == "TOPSTEP_COMBINE_100K"


def test_an_unresolvable_prop_target_raises(monkeypatch):
    from ai_trading.project import status as status_module

    monkeypatch.setattr(status_module, "_TARGET_KEY",
                        ("nonesuch", "nonesuch", status_module.Stage.EVALUATION,
                         1))
    with pytest.raises(TargetUnresolved, match="does not resolve"):
        status_module.resolve_status(include_test_count=False)


def test_a_synthetic_dataset_does_not_advance_the_project():
    status = a_status(dataset=synthetic_dataset())
    assert status.project_status is ProjectPhase.EVIDENCE_PENDING
    assert status.market_claim_status is MarketClaimStatus.BLOCKED


def test_an_approved_dataset_advances_every_dependent_field():
    """The derivations are real: supply the input and the outputs move."""
    status = a_status(dataset=approved_dataset())
    assert status.real_data_status is RealDataStatus.APPROVED
    assert status.market_claim_status is MarketClaimStatus.ALLOWED
    assert status.project_status is ProjectPhase.EVIDENCE_AVAILABLE
    assert status.next_required_external_action is ExternalAction.NONE_REQUIRED
    assert not status.is_blocked


def test_there_is_no_middle_project_phase():
    assert [p.value for p in ProjectPhase] == ["EVIDENCE_PENDING",
                                              "EVIDENCE_AVAILABLE"]


def test_the_status_carries_no_timestamp():
    """Determinism: two runs at one commit must produce identical bytes."""
    import json

    first = json.dumps(a_status().to_dict(), sort_keys=True)
    second = json.dumps(a_status().to_dict(), sort_keys=True)
    assert first == second
    for temporal in ("created_at", "generated_at", "timestamp", "elapsed"):
        assert temporal not in first


def test_the_test_count_is_collected_not_written_down():
    count = collect_test_count()
    assert count is not None and count > 1_500


# =========================================================================
# The real-data gate
# =========================================================================


def test_the_gate_message_is_the_declared_wording():
    assert REAL_DATA_PENDING_MESSAGE == (
        "REAL_DATA_PENDING:\n"
        "ICT-FAMILY-V1 is frozen and cannot be evaluated until a dataset "
        "reaches MARKET_CLAIM_ALLOWED.")


def test_the_gate_refuses_with_no_dataset():
    with pytest.raises(RealDataPending, match="REAL_DATA_PENDING"):
        require_real_data_approved(None)


def test_the_gate_refuses_a_research_grade_synthetic_dataset():
    dataset = synthetic_dataset()
    assert dataset.grades.permits_research
    with pytest.raises(RealDataPending) as caught:
        require_real_data_approved(dataset)
    assert "describe the generator" in str(caught.value)


def test_the_gate_opens_for_an_approved_dataset():
    require_real_data_approved(approved_dataset())
    assert may_run_ict_family(approved_dataset())


def test_may_run_is_a_question_not_a_refusal():
    assert not may_run_ict_family(None)
    assert not may_run_ict_family(synthetic_dataset())


def test_the_campaign_entry_point_is_gated():
    with pytest.raises(RealDataPending, match="REAL_DATA_PENDING"):
        run_ict_family_campaign(None)


def test_the_gate_does_not_block_synthetic_calibration():
    """Explicitly: calibration on synthetic data still runs."""
    from ai_trading.calibration import ALL_GENERATORS, generate_momentum

    assert len(ALL_GENERATORS) == 5
    assert generate_momentum(n=200, seed=7).bars      # runs, ungated


def test_the_gate_does_not_block_the_feature_engine():
    from ai_trading.features.ict_objective import Candle, ObjectiveFeatureEngine

    candles = [
        Candle(index=i, event_time=T0 + timedelta(minutes=i),
               available_at=T0 + timedelta(minutes=i + 1), open=20_000.0,
               high=20_010.0, low=19_990.0, close=20_005.0)
        for i in range(30)
    ]
    assert len(ObjectiveFeatureEngine().run(candles)) == 30


# =========================================================================
# system:status / system:audit
# =========================================================================


def test_the_status_command_reports_every_required_line(capsys):
    assert main(["system:status", "--no-tests"]) == 0
    out = capsys.readouterr().out
    for line in ("project_status", "code_commit", "research_protocol_version",
                 "ict_family_fingerprint", "real_data_status",
                 "holdout_status", "prop_firm_readiness",
                 "live_execution_status", "outstanding blockers"):
        assert line in out
    assert "EVIDENCE_PENDING" in out


def test_the_status_command_is_deterministic(capsys):
    main(["system:status", "--no-tests"])
    first = capsys.readouterr().out
    main(["system:status", "--no-tests"])
    assert capsys.readouterr().out == first


def test_the_status_command_emits_json(capsys):
    import json

    assert main(["system:status", "--no-tests", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["project_status"] == "EVIDENCE_PENDING"
    assert payload["next_required_external_action"] == \
        "PROVIDE_APPROVED_REAL_NQ_DATA"
    assert payload["test_count"] is None


def test_the_blockers_are_derived_from_the_status():
    rendered = render_status(a_status())
    assert "no approved real NQ dataset" in rendered
    assert "MARKET_CLAIM_ALLOWED not granted" in rendered


def test_the_ict_run_command_refuses_with_exit_code_three(capsys):
    assert main(["research:ict:run"]) == 3
    assert "REAL_DATA_PENDING" in capsys.readouterr().err


def test_the_audit_command_passes_and_exits_zero(capsys):
    assert main(["system:audit"]) == 0
    assert "0 failing" in capsys.readouterr().out


def test_the_commands_are_the_three_declared():
    subparsers = build_parser()._subparsers._group_actions[0]
    assert sorted(subparsers.choices) == ["research:ict:run", "system:audit",
                                          "system:status"]


# =========================================================================
# The integrity audit
# =========================================================================


def test_the_audit_runs_every_declared_check():
    report = run_integrity_audit()
    assert tuple(c.name for c in report.checks) == CHECK_NAMES
    assert len(CHECK_NAMES) == 8


def test_the_audit_passes():
    report = run_integrity_audit()
    assert report.passed, report.summary()
    assert report.critical_failures == ()


def test_every_check_is_critical_when_it_passes():
    """An advisory pass would mean the check did not test what it claims."""
    for check in run_integrity_audit().checks:
        assert check.severity is Severity.CRITICAL, check.name
        assert check.detail, check.name


def test_the_audit_is_read_only_and_repeatable():
    first = run_integrity_audit().to_dict()
    assert run_integrity_audit().to_dict() == first


def test_the_audit_detects_a_live_broker():
    """The live-execution check is real: define an adapter and it fails.

    ``__subclasses__`` holds weak references, so dropping the only reference
    and collecting restores the registry. The test asserts the restoration
    rather than assuming it -- a leaked probe class would silently fail every
    later run of this check.
    """
    import gc

    from ai_trading.execution.broker import Broker

    def probe():
        class PretendLiveBroker(Broker):
            def submit(self, *args, **kwargs): raise NotImplementedError
            def cancel(self, *args, **kwargs): raise NotImplementedError

        # Pretend it ships: only in-package adapters count, since test doubles
        # subclass Broker too and must not trip the check.
        PretendLiveBroker.__module__ = "ai_trading.execution.pretend_live"

        report = run_integrity_audit()
        check = next(c for c in report.checks
                     if c.name == "no_live_execution_route")
        assert not check.passed
        assert "PretendLiveBroker" in check.detail
        assert not report.passed

    probe()
    gc.collect()
    restored = next(c for c in run_integrity_audit().checks
                    if c.name == "no_live_execution_route")
    assert restored.passed
    assert "PaperBroker" in restored.detail
    assert Broker.__subclasses__()


def test_the_audit_detects_family_drift(monkeypatch):
    from ai_trading.research.ict_family import ICT_FAMILY_V1

    monkeypatch.setitem(ICT_FAMILY_V1.fixed_parameters, "atr_period", 20)
    check = next(c for c in run_integrity_audit().checks
                 if c.name == "no_mutable_v1_family")
    assert not check.passed
