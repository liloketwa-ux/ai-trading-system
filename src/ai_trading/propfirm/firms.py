"""Firm profile definitions.

**Verification status of this entire module: UNVERIFIED.**

Network access to every firm's documentation is blocked in this environment --
topstep.com, help.topstep.com, apextraderfunding.com, myfundedfutures.com,
alphafutures.com and gateway.projectx.com all return connect_rejected. Not one
value below was read from a firm's own current documentation.

Values supplied by the operator are recorded as ``USER_SUPPLIED``. Rules whose
exact behaviour was not stated -- notably Topstep's Maximum Loss Limit
calculation, Apex's daily loss limits, and account-size specifics for MFFU
Rapid -- are ``UNKNOWN``. Both refuse to back a compliance assertion.

This is the instructed behaviour, not a limitation being worked around: a
plausible number recorded as fact is worse than an explicit gap, because the gap
gets filled and the fact does not get checked.
"""

from __future__ import annotations

from datetime import date, time

from .profiles import (
    REGISTRY,
    AutomationPolicy,
    AutomationStance,
    ConsistencyRule,
    DrawdownBasis,
    DrawdownTiming,
    FirmProfile,
    MaxLossLimit,
    PositionLimits,
    ProhibitedPractice,
)
from .verification import VerificationStatus, unknown, user_supplied

__all__ = ["build_all", "TOPSTEP_PRACTICES", "PRIMARY_AUTOMATION_TARGET"]

PRIMARY_AUTOMATION_TARGET = "topstep"

TOPSTEP_PRACTICES = (
    ProhibitedPractice.SIMULATOR_EXPLOITATION,
    ProhibitedPractice.STALE_FEED_EXPLOITATION,
    ProhibitedPractice.PRICE_DISPLAY_EXPLOITATION,
    ProhibitedPractice.SPOOFING,
    ProhibitedPractice.TRADING_OUTSIDE_BBO,
    ProhibitedPractice.UNREALISTIC_SIM_FILLS,
    ProhibitedPractice.CROSS_ACCOUNT_HEDGING,
    ProhibitedPractice.PROHIBITED_HFT,
    ProhibitedPractice.UNFAIR_ADVANTAGE_TECH,
    ProhibitedPractice.MAX_SIZE_INTO_NEWS,
)

_UNVERIFIED_NOTE = (
    "operator-supplied; firm documentation unreachable from this environment"
)


def _topstep_mll() -> MaxLossLimit:
    """Topstep's hard failure rule.

    Deliberately almost entirely UNKNOWN. The operator instructed that the MLL
    must be resolved from the official source and not inferred from third-party
    articles, and it could not be reached. The threshold and calculation method
    are exactly the values that decide whether an open loser fails an account,
    so guessing them would produce confident and possibly wrong adjudications.
    """
    return MaxLossLimit(
        drawdown_type=unknown("mll_drawdown_type",
                              "Topstep MLL trailing behaviour not verified"),
        threshold=unknown("mll_threshold", "per-account-size MLL amount not verified"),
        calculation_method=unknown(
            "mll_calculation_method",
            "whether the limit trails on intraday equity or end-of-day closed "
            "balance is unverified, and the two differ materially",
        ),
        timing=unknown("mll_timing", "intraday vs end-of-day not verified"),
        basis=unknown("mll_basis", "balance vs equity not verified"),
        locks_at=unknown("mll_locks_at", "lock threshold not verified"),
    )


def _topstep(account_size: int, target: int, minis: int, micros: int) -> FirmProfile:
    return FirmProfile(
        firm_id="topstep",
        program_id="trading_combine",
        account_size=account_size,
        ruleset_version="2026.06-unverified",
        effective_from=date(2026, 6, 1),
        source_url="https://www.topstep.com/ (UNREACHABLE from this environment)",
        retrieved_at=None,
        verification_status=VerificationStatus.USER_SUPPLIED,
        initial_balance=user_supplied(account_size, label="initial_balance",
                                      note=_UNVERIFIED_NOTE),
        profit_target=user_supplied(target, label="profit_target", note=_UNVERIFIED_NOTE),
        max_loss_limit=_topstep_mll(),
        position_limits=PositionLimits(
            max_minis=user_supplied(minis, label="max_minis", note=_UNVERIFIED_NOTE),
            max_micros=user_supplied(micros, label="max_micros", note=_UNVERIFIED_NOTE),
        ),
        automation=AutomationPolicy(
            stance=user_supplied(AutomationStance.ALLOWED, label="automation_stance",
                                 note=_UNVERIFIED_NOTE),
            api_available=user_supplied(True, label="api_available", note=_UNVERIFIED_NOTE),
            api_provider=user_supplied("TopstepX/ProjectX", label="api_provider",
                                       note=_UNVERIFIED_NOTE),
            requires_local_execution=user_supplied(
                True, label="requires_local_execution",
                note="operator states API docs require the user's personal device",
            ),
            prohibits_vps=user_supplied(
                True, label="prohibits_vps",
                note="operator states VPS/VPN/remote-server use is prohibited",
            ),
            prohibited_practices=TOPSTEP_PRACTICES,
        ),
        consistency=ConsistencyRule(
            max_best_day_fraction=user_supplied(0.50, label="max_best_day_fraction",
                                                note=_UNVERIFIED_NOTE),
            applies_to=user_supplied("evaluation", label="consistency_applies_to",
                                     note=_UNVERIFIED_NOTE),
            target_increase_effect=unknown(
                "target_increase_effect",
                "effect of a consistency-target increase not verified",
            ),
        ),
        daily_loss_limit=unknown(
            "daily_loss_limit",
            "Combine's hard rule is the MLL; whether a separate DLL applies is unverified",
        ),
        min_trading_days=user_supplied(2, label="min_trading_days", note=_UNVERIFIED_NOTE),
        trading_day_start=user_supplied(time(17, 0), label="trading_day_start",
                                        note="17:00 CT per operator"),
        trading_day_end=user_supplied(time(15, 10), label="trading_day_end",
                                      note="15:10 CT next calendar day per operator"),
        forced_flat_time=user_supplied(time(15, 10), label="forced_flat_time",
                                       note=_UNVERIFIED_NOTE),
        session_reopen=user_supplied(time(17, 0), label="session_reopen",
                                     note=_UNVERIFIED_NOTE),
        overnight_allowed=user_supplied(False, label="overnight_allowed",
                                        note=_UNVERIFIED_NOTE),
        timezone="America/Chicago",
        notes="PRIMARY_AUTOMATION_TARGET. MLL entirely unverified -- adjudication refused.",
    )


def _apex(account_size: int, max_drawdown: int, max_contracts: int) -> FirmProfile:
    return FirmProfile(
        firm_id="apex",
        program_id="eod_pa",
        account_size=account_size,
        ruleset_version="2026.06-unverified",
        effective_from=date(2026, 6, 1),
        source_url="https://apextraderfunding.com/ (UNREACHABLE from this environment)",
        retrieved_at=None,
        verification_status=VerificationStatus.USER_SUPPLIED,
        initial_balance=user_supplied(account_size, label="initial_balance",
                                      note=_UNVERIFIED_NOTE),
        profit_target=unknown("profit_target", "Apex EOD PA target not supplied or verified"),
        max_loss_limit=MaxLossLimit(
            drawdown_type=user_supplied("eod_trailing", label="mll_drawdown_type",
                                        note=_UNVERIFIED_NOTE),
            threshold=user_supplied(max_drawdown, label="mll_threshold",
                                    note=_UNVERIFIED_NOTE),
            calculation_method=user_supplied(
                "end-of-day trailing; intraday trailing drawdown does not apply",
                label="mll_calculation_method", note=_UNVERIFIED_NOTE,
            ),
            timing=user_supplied(DrawdownTiming.END_OF_DAY, label="mll_timing",
                                 note=_UNVERIFIED_NOTE),
            basis=unknown("mll_basis", "balance vs equity basis not verified"),
        ),
        position_limits=PositionLimits(
            max_minis=user_supplied(max_contracts, label="max_minis", note=_UNVERIFIED_NOTE),
            max_micros=user_supplied(max_contracts * 10, label="max_micros",
                                     note="derived from the 10:1 relationship, not verified"),
        ),
        automation=AutomationPolicy(
            stance=unknown("automation_stance", "Apex automation policy not verified"),
            api_available=unknown("api_available"),
        ),
        daily_loss_limit=unknown(
            "daily_loss_limit",
            "operator explicitly instructed NOT to invent Apex DLL values",
        ),
        profit_split=user_supplied(1.00, label="profit_split", note=_UNVERIFIED_NOTE),
        notes="Comparison profile. DLL and target unresolved by instruction.",
    )


def _mffu() -> FirmProfile:
    return FirmProfile(
        firm_id="mffu",
        program_id="rapid",
        account_size=0,     # size-specific values unverified
        ruleset_version="2026.06-unverified",
        effective_from=date(2026, 6, 1),
        source_url="https://myfundedfutures.com/ (UNREACHABLE from this environment)",
        retrieved_at=None,
        verification_status=VerificationStatus.USER_SUPPLIED,
        initial_balance=unknown("initial_balance",
                                "account-size-specific values not verified"),
        profit_target=unknown("profit_target", "size-specific target not verified"),
        max_loss_limit=MaxLossLimit(
            drawdown_type=user_supplied("eod", label="mll_drawdown_type",
                                        note=_UNVERIFIED_NOTE),
            threshold=unknown("mll_threshold", "size-specific drawdown not verified"),
            calculation_method=user_supplied("end-of-day drawdown",
                                             label="mll_calculation_method",
                                             note=_UNVERIFIED_NOTE),
            timing=user_supplied(DrawdownTiming.END_OF_DAY, label="mll_timing",
                                 note=_UNVERIFIED_NOTE),
            basis=unknown("mll_basis"),
        ),
        position_limits=PositionLimits(
            max_minis=unknown("max_minis", "size-specific contract limits not verified"),
            max_micros=unknown("max_micros"),
        ),
        automation=AutomationPolicy(
            stance=unknown("automation_stance", "MFFU automation policy not verified"),
            api_available=unknown("api_available"),
        ),
        consistency=ConsistencyRule(
            max_best_day_fraction=user_supplied(0.50, label="max_best_day_fraction",
                                                note=_UNVERIFIED_NOTE),
            applies_to=user_supplied("evaluation", label="consistency_applies_to",
                                     note=_UNVERIFIED_NOTE),
        ),
        # 0 encodes "no daily loss limit" per the operator; still unverified.
        daily_loss_limit=user_supplied(
            0, label="daily_loss_limit",
            note="operator states Rapid has no daily loss limit (0 = none); unverified",
        ),
        min_trading_days=user_supplied(2, label="min_trading_days", note=_UNVERIFIED_NOTE),
        profit_split=user_supplied(0.90, label="profit_split", note=_UNVERIFIED_NOTE),
        payout_cadence=user_supplied("daily", label="payout_cadence", note=_UNVERIFIED_NOTE),
        activation_fee=user_supplied(0, label="activation_fee", note=_UNVERIFIED_NOTE),
        notes="Comparison profile. Account-size specifics deliberately unresolved.",
    )


def _alpha() -> FirmProfile:
    return FirmProfile(
        firm_id="alpha_futures",
        program_id="research_comparison_only",
        account_size=0,
        ruleset_version="2026.06-unverified",
        effective_from=date(2026, 6, 1),
        source_url="https://alphafutures.com/ (UNREACHABLE from this environment)",
        retrieved_at=None,
        verification_status=VerificationStatus.USER_SUPPLIED,
        initial_balance=unknown("initial_balance"),
        profit_target=unknown("profit_target"),
        max_loss_limit=MaxLossLimit(
            drawdown_type=unknown("mll_drawdown_type"),
            threshold=unknown("mll_threshold"),
            calculation_method=unknown("mll_calculation_method"),
            timing=unknown("mll_timing"),
            basis=unknown("mll_basis"),
        ),
        position_limits=PositionLimits(max_minis=unknown("max_minis"),
                                       max_micros=unknown("max_micros")),
        automation=AutomationPolicy(
            stance=user_supplied(AutomationStance.SEMI_ONLY, label="automation_stance",
                                 note="operator states full automation and AI/bots prohibited; "
                                      "semi-automation allowed subject to manual execution"),
            api_available=unknown("api_available"),
        ),
        notes="RESEARCH COMPARISON ONLY. Not a live-automation target: "
              "full automation reported prohibited.",
    )


def build_all(registry=REGISTRY):
    """Publish every profile into the registry."""
    for size, target, minis, micros in [(50_000, 3_000, 5, 50),
                                        (100_000, 6_000, 10, 100),
                                        (150_000, 9_000, 15, 150)]:
        registry.register(_topstep(size, target, minis, micros))

    for size, drawdown, contracts in [(25_000, 1_000, 2), (50_000, 2_000, 4),
                                      (100_000, 3_000, 6), (150_000, 4_000, 10)]:
        registry.register(_apex(size, drawdown, contracts))

    registry.register(_mffu())
    registry.register(_alpha())
    return registry


build_all()
