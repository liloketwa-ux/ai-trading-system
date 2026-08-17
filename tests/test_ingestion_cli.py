"""``data:ingest:futures`` refusals, target semantics, and risk eligibility.

The CLI is the only door real market data comes through, so its refusals are
tested before any provider exists — that is the point of writing them first.
"""

from datetime import date, datetime, timezone

import pytest

from ai_trading.history import (
    DataKind,
    ProviderManifest,
    bar_close_availability,
)
from ai_trading.history.cli import (
    IngestRefusal,
    IngestRequest,
    build_parser,
    preflight,
)
from ai_trading.history.futures_provider import (
    ContractRecord,
    FuturesDataProvider,
    InstrumentMetadata,
    SessionMetadata,
)
from ai_trading.risk import (
    RiskEligibility,
    StrategyQualityTier,
    TargetSemantics,
    UserRiskPolicy,
)

UTC = timezone.utc


class StubProvider(FuturesDataProvider):
    """Minimal provider used only to exercise the refusals."""

    def __init__(self, **manifest_kw):
        defaults = dict(
            provider="stub", dataset="STUB.TEST",
            kinds=frozenset({DataKind.BARS}),
            availability_policy=bar_close_availability("stub bar completion"),
            credential_env_vars=("STUB_API_KEY",),
        )
        self._manifest = ProviderManifest(**{**defaults, **manifest_kw})

    @property
    def manifest(self):
        return self._manifest

    def instrument_metadata(self, instrument):
        return InstrumentMetadata(instrument, "CME", "USD", 0.25, 5.0, 20.0)

    def session_metadata(self, instrument):
        return SessionMetadata(instrument, "UTC", frozenset({0, 1, 2, 3, 4, 6}),
                               0, 1_380)

    def list_contracts(self, instrument, *, start, end):
        return [ContractRecord(instrument, "NQM26", date(2026, 6, 19))]

    def coverage(self, kind, instrument, contract):
        raise NotImplementedError

    def fetch_bars(self, *, instrument, contract, timeframe, start, end):
        raise NotImplementedError


def a_request(**kw):
    defaults = dict(
        provider="stub", contract="NQM26",
        start=datetime(2026, 3, 1, tzinfo=UTC),
        end=datetime(2026, 4, 1, tzinfo=UTC),
        timeframe="1m", instrument="NQ", expiry=date(2026, 6, 19),
    )
    return IngestRequest(**{**defaults, **kw})


def a_registry(provider=None):
    return {"stub": provider or StubProvider()}


CREDS = {"STUB_API_KEY": "value-never-read"}


# =========================================================================
# Required arguments
# =========================================================================


@pytest.mark.parametrize("missing",
                         ["--provider", "--contract", "--start", "--end",
                          "--timeframe"])
def test_every_required_argument_is_required(missing):
    """No defaults: a command that guesses a date range guesses wrong."""
    argv = ["data:ingest:futures", "--provider", "stub", "--contract", "NQM26",
            "--start", "2026-03-01", "--end", "2026-04-01", "--timeframe", "1m"]
    index = argv.index(missing)
    del argv[index:index + 2]
    with pytest.raises(SystemExit):
        build_parser().parse_args(argv)


def test_a_complete_command_parses():
    args = build_parser().parse_args([
        "data:ingest:futures", "--provider", "databento", "--contract", "NQM26",
        "--start", "2026-03-01", "--end", "2026-04-01", "--timeframe", "1m",
        "--instrument", "NQ", "--expiry", "2026-06-19"])
    assert args.provider == "databento"
    assert args.contract == "NQM26"


def test_an_end_before_the_start_is_refused():
    with pytest.raises(IngestRefusal, match="does not follow"):
        a_request(start=datetime(2026, 4, 1, tzinfo=UTC),
                  end=datetime(2026, 3, 1, tzinfo=UTC))


def test_a_blank_contract_is_refused():
    with pytest.raises(IngestRefusal, match="contract is required"):
        a_request(contract="")


# =========================================================================
# Refusal 1 -- unverified source provenance
# =========================================================================


def test_an_unregistered_provider_is_refused():
    with pytest.raises(IngestRefusal, match="unverified source provenance"):
        preflight(a_request(provider="some-guys-csv"), registry={},
                  environ=CREDS)


def test_the_refusal_lists_what_is_registered():
    with pytest.raises(IngestRefusal, match="Registered: none"):
        preflight(a_request(), registry={}, environ=CREDS)


def test_no_real_provider_is_registered_yet():
    """An empty registry is what makes the command refuse rather than improvise."""
    from ai_trading.history.cli import PROVIDER_REGISTRY

    assert PROVIDER_REGISTRY == {}


# =========================================================================
# Refusal 2 -- continuous-only data
# =========================================================================


def test_a_continuous_only_provider_is_refused():
    provider = StubProvider(serves_continuous_only=True)
    with pytest.raises(IngestRefusal, match="individual contracts"):
        preflight(a_request(), registry=a_registry(provider), environ=CREDS)


def test_a_contract_level_provider_passes_that_check():
    preflight(a_request(), registry=a_registry(), environ=CREDS)


def test_a_provider_without_bars_is_refused():
    provider = StubProvider(kinds=frozenset({DataKind.TRADES}))
    with pytest.raises(IngestRefusal, match="does not serve bars"):
        preflight(a_request(), registry=a_registry(provider), environ=CREDS)


# =========================================================================
# Refusal 3 -- missing expiry
# =========================================================================


def test_a_missing_expiry_is_refused():
    with pytest.raises(IngestRefusal, match="no roll can be justified"):
        preflight(a_request(expiry=None), registry=a_registry(), environ=CREDS)


def test_a_supplied_expiry_passes():
    provider = preflight(a_request(expiry=date(2026, 6, 19)),
                         registry=a_registry(), environ=CREDS)
    assert provider.manifest.provider == "stub"


# =========================================================================
# Refusal 4 -- credentials
# =========================================================================


def test_missing_credentials_are_refused_by_name():
    with pytest.raises(IngestRefusal, match="STUB_API_KEY"):
        preflight(a_request(), registry=a_registry(), environ={})


def test_the_credential_refusal_never_echoes_a_value():
    provider = StubProvider()
    try:
        preflight(a_request(), registry=a_registry(provider),
                  environ={"STUB_API_KEY": ""})
    except IngestRefusal as error:
        assert "never in a commit" in str(error)
    else:
        pytest.fail("expected a refusal")


def test_credentials_are_checked_from_the_environment_not_arguments():
    """No CLI flag accepts a key: a secret on a command line is in shell history."""
    parser_actions = {a.dest for a in build_parser()._subparsers._group_actions[0]
                      .choices["data:ingest:futures"]._actions}
    for forbidden in ("api_key", "key", "token", "secret", "password"):
        assert forbidden not in parser_actions


def test_refusals_are_ordered_cheapest_first():
    """A missing provider reports before a credential check that could not matter."""
    with pytest.raises(IngestRefusal, match="unverified source provenance"):
        preflight(a_request(provider="absent"), registry={}, environ={})


# =========================================================================
# User target semantics
# =========================================================================


def test_the_daily_target_is_a_desired_return():
    policy = UserRiskPolicy()
    assert policy.target_semantics is TargetSemantics.USER_DESIRED_DAILY_RETURN
    assert policy.to_dict()["target_semantics"] == "user_desired_daily_return"


def test_there_is_no_mandatory_trade_target():
    """The wrong interpretation has no symbol, so it cannot be expressed."""
    assert not hasattr(TargetSemantics, "MANDATORY_TRADE_TARGET")
    assert [s.value for s in TargetSemantics] == ["user_desired_daily_return"]


def test_a_desired_return_never_obliges_trading():
    assert not UserRiskPolicy().target_obliges_trading
    assert not TargetSemantics.USER_DESIRED_DAILY_RETURN.obliges_trading


def test_the_semantics_are_not_configurable():
    """Fixed on the type, not a constructor argument."""
    with pytest.raises(TypeError):
        UserRiskPolicy(target_semantics="mandatory")   # type: ignore[call-arg]


def test_the_ceiling_and_baseline_keep_their_frozen_values():
    policy = UserRiskPolicy()
    assert policy.max_risk_per_trade_pct == 2.0
    assert policy.baseline_risk_per_trade_pct == 0.25


# =========================================================================
# Risk eligibility
# =========================================================================


@pytest.mark.parametrize(("tier", "eligibility"), [
    (StrategyQualityTier.INSUFFICIENT_SAMPLE, RiskEligibility.NO_LIVE_RISK),
    (StrategyQualityTier.OUT_OF_SAMPLE_FAILURE, RiskEligibility.NO_LIVE_RISK),
    (StrategyQualityTier.PROMISING, RiskEligibility.PAPER_ONLY),
    (StrategyQualityTier.SURVIVES_ROBUSTNESS,
     RiskEligibility.LIMITED_RISK_ELIGIBLE),
    (StrategyQualityTier.ROBUST_CANDIDATE,
     RiskEligibility.FULL_RISK_POLICY_ELIGIBLE),
])
def test_each_tier_maps_to_its_eligibility(tier, eligibility):
    assert tier.eligibility is eligibility


def test_only_the_top_two_tiers_permit_live_capital():
    permitted = [t for t in StrategyQualityTier
                 if t.eligibility.permits_live_capital]
    assert permitted == [StrategyQualityTier.SURVIVES_ROBUSTNESS,
                         StrategyQualityTier.ROBUST_CANDIDATE]


def test_no_live_risk_permits_nothing_including_paper():
    assert not RiskEligibility.NO_LIVE_RISK.permits_paper
    assert not RiskEligibility.NO_LIVE_RISK.permits_live_capital


def test_paper_only_permits_paper_and_not_capital():
    assert RiskEligibility.PAPER_ONLY.permits_paper
    assert not RiskEligibility.PAPER_ONLY.permits_live_capital


def test_full_eligibility_does_not_assign_a_larger_percentage():
    """Eligible for the policy ceiling is not entitled to it."""
    tier = StrategyQualityTier.ROBUST_CANDIDATE
    assert tier.eligibility is RiskEligibility.FULL_RISK_POLICY_ELIGIBLE
    assert tier.budget_pct(2.0) == 2.0        # the ceiling, not more
    assert tier.budget_pct(0.5) == 0.5        # tracks whatever the ceiling is


def test_eligibility_cannot_raise_a_firm_limit():
    from ai_trading.risk import RiskConstraint, RiskLayer, resolve_risk

    resolved = resolve_risk([
        RiskConstraint(RiskLayer.FIRM_HARD_LIMIT, "firm_mll", 0.4),
        UserRiskPolicy().ceiling_constraint(),
        RiskConstraint(RiskLayer.STRATEGY_BUDGET, "robust",
                       StrategyQualityTier.ROBUST_CANDIDATE.budget_pct(2.0)),
    ])
    assert resolved.allowed_pct == 0.4
    assert resolved.binding_layer is RiskLayer.FIRM_HARD_LIMIT


def test_tier_and_eligibility_are_separate_concepts():
    """A finding about research, versus a permission granted on the back of it."""
    assert StrategyQualityTier.PROMISING.value != \
        StrategyQualityTier.PROMISING.eligibility.value
    assert len(list(RiskEligibility)) == 4
    assert len(list(StrategyQualityTier)) == 5
