"""Firm profile definitions, with per-field provenance.

**What changed and what did not.** Network access to every firm's documentation
is still blocked in this environment; nothing below was fetched by this code.
The operator reviewed the firms' official current documentation outside the
coding environment and attested to the values, so those values are recorded as
``OFFICIAL_SOURCE_VERIFIED`` with ``verification_method="official_source_review"``
and ``verified_at=2026-08-15``, alongside the source URL and document title.

That status is deliberately distinct from ``VERIFIED_OFFICIAL``, which means the
code fetched the page itself. Both are sufficient to back a compliance claim;
only one of them can be re-derived automatically, and the record says which is
which.

**Everything not on the attested list stays unverified.** Where the source
review covered a value, it is verified. Where it did not -- Topstep's session
boundary, which the instruction explicitly flagged as needing verification
before promotion; Topstep's Express Funded risk rules; Apex's daily loss limit
and lock behaviour; MFFU's position limits and funded-stage rules; all of Alpha
Futures -- the field stays ``UNKNOWN`` and the capabilities that depend on it
still refuse. Inheriting a Combine value into an Express Funded profile because
it "probably carries over" is exactly the failure mode this module exists to
prevent.

Rules that genuinely do not exist for a program are ``NOT_APPLICABLE``, not
``UNKNOWN``: MFFU Rapid has no daily loss limit, and an Express Funded account
has no evaluation profit target. Those are facts, and treating them as gaps
would block adjudication on a fully specified ruleset.
"""

from __future__ import annotations

from datetime import date, time

from .hierarchy import PayoutPolicy, Stage, XFAParameters
from .limits import DailyLossLimitMode, MLLMode
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
from .verification import (
    VerificationStatus,
    not_applicable,
    official_verified,
    unknown,
    user_supplied,
)

__all__ = ["build_all", "TOPSTEP_PRACTICES", "PRIMARY_AUTOMATION_TARGET",
           "SOURCES", "VERIFIED_AT", "RULESET_VERSION"]

PRIMARY_AUTOMATION_TARGET = "topstep"

#: Date the operator reviewed the official sources, outside this environment.
VERIFIED_AT = date(2026, 8, 15)
RULESET_VERSION = "2026.08"

#: Every source consulted in the review, as (document title, URL).
SOURCES: dict[str, tuple[str, str]] = {
    "topstep_combine": (
        "Trading Combine Parameters",
        "https://help.topstep.com/en/articles/8284197-trading-combine-parameters",
    ),
    "topstep_mll": (
        "What is the Maximum Loss Limit?",
        "https://help.topstep.com/en/articles/8284204-what-is-the-maximum-loss-limit",
    ),
    "topstep_dll": (
        "Daily Loss Limit in the Trading Combine and Express Funded Account",
        "https://help.topstep.com/en/articles/"
        "10490293-daily-loss-limit-in-the-trading-combine-and-express-funded-account",
    ),
    "topstep_consistency": (
        "Consistency at Topstep",
        "https://help.topstep.com/en/articles/8284208-consistency-at-topstep",
    ),
    "topstep_api": (
        "TopstepX API Access",
        "https://help.topstep.com/en/articles/11187768-topstepx-api-access",
    ),
    "topstep_prohibited": (
        "Prohibited Trading Strategies at Topstep",
        "https://help.topstep.com/en/articles/"
        "10305426-prohibited-trading-strategies-at-topstep",
    ),
    "topstep_xfa": (
        "Express Funded Account Parameters",
        "https://help.topstep.com/en/articles/8284215-express-funded-account-parameters",
    ),
    "topstep_payout": (
        "Topstep Payout Policy",
        "https://help.topstep.com/en/articles/8284233-topstep-payout-policy",
    ),
    "apex_eod": (
        "EOD Performance Accounts (PA)",
        "https://apextraderfunding.com/help-center/eod-trailing-drawdown-accounts/"
        "eod-performance-accounts-pa/",
    ),
    "mffu_rapid": (
        "My Funded Futures - Rapid Plans",
        "https://myfundedfutures.com/plans/rapid",
    ),
    "mffu_consistency": (
        "Consistency Rule at My Funded Futures",
        "https://help.myfundedfutures.com/en/articles/"
        "11994562-consistency-rule-at-my-funded-futures",
    ),
}

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

_UNVERIFIED_NOTE = "not covered by the 2026-08-15 official source review"


def _v(source_key: str):
    """``official_verified`` bound to one source document."""
    title, url = SOURCES[source_key]

    def make(value, label: str):
        return official_verified(value, url=url, title=title,
                                 verified_at=VERIFIED_AT, label=label)

    return make


# =========================================================================
# Topstep -- Trading Combine (evaluation)
# =========================================================================

#: profit target, max minis, max micros, MLL, purchase-set DLL, recommended best day
_TOPSTEP_COMBINE = {
    50_000: (3_000, 5, 50, 2_000, 1_000, 1_500),
    100_000: (6_000, 10, 100, 3_000, 2_000, 3_000),
    150_000: (9_000, 15, 150, 4_500, 3_000, 4_500),
}


def _topstep_automation() -> AutomationPolicy:
    """Automated strategies are allowed; the constraints are on where and how.

    The prohibition is on using technology to exploit the platform, not on
    automation itself, and conflating the two would refuse a permitted activity.
    What is genuinely prohibited is the deployment topology: execution must
    originate from the trader's own device, which rules out a VPS, a VPN, or a
    remote server -- including the container this code was written in.
    """
    api = _v("topstep_api")
    prohibited = _v("topstep_prohibited")
    return AutomationPolicy(
        stance=prohibited(AutomationStance.ALLOWED, "automation_stance"),
        api_available=api(True, "api_available"),
        api_provider=api("TopstepX", "api_provider"),
        requires_local_execution=api(True, "requires_local_execution"),
        prohibits_vps=api(True, "prohibits_vps"),
        prohibited_practices=TOPSTEP_PRACTICES,
    )


def _topstep_session_rules() -> dict:
    """Session boundary, deliberately left unverified.

    The instruction on this field was explicit: the 17:00-15:10 CT boundary is
    the value already implemented, and it is to be verified against the official
    source *before* being promoted to adjudication-ready. So it stays
    ``USER_SUPPLIED`` and the session-boundary capability keeps refusing.

    This one field is what stands between the Combine profiles and full
    adjudication readiness, and that is the more useful state: the boundary
    decides when an end-of-day trailing threshold advances, so a quiet promotion
    here would put an unverified value at the centre of the MLL.
    """
    note = ("session boundary pending verification against Topstep's Permitted "
            "Products and Trading Hours; explicitly excluded from the "
            "2026-08-15 review")
    return {
        "trading_day_start": user_supplied(time(17, 0), label="trading_day_start",
                                           note=note),
        "trading_day_end": user_supplied(time(15, 10), label="trading_day_end",
                                         note=note),
        "forced_flat_time": user_supplied(time(15, 10), label="forced_flat_time",
                                          note=note),
        "session_reopen": user_supplied(time(17, 0), label="session_reopen",
                                        note=note),
        "overnight_allowed": user_supplied(False, label="overnight_allowed",
                                           note=note),
    }


def _topstep_mll(threshold: int, starting_balance: int) -> MaxLossLimit:
    """Topstep's Maximum Loss Limit, modelled exactly as documented.

    The calculation method is recorded as prose *and* as an executable mode: the
    prose is what a human checks against the source, the mode is what the
    tracker runs. Verifying only one of them leaves the other free to drift.
    """
    v = _v("topstep_mll")
    return MaxLossLimit(
        drawdown_type=v("eod_trailing", "mll_drawdown_type"),
        threshold=v(threshold, "mll_threshold"),
        calculation_method=v(
            "The MLL is the lowest account balance permitted. It trails upward as "
            "end-of-day balance increases, never moves downward, and locks once it "
            "reaches the starting balance. It is enforced during the trading day: "
            "touching it causes immediate liquidation, and unrealised P&L counts "
            "toward the violation.",
            "mll_calculation_method",
        ),
        # The threshold advances at the close but bites on any tick, so neither
        # DrawdownTiming value alone describes it. INTRADAY records the half that
        # decides account survival; `mode` carries the whole rule.
        timing=v(DrawdownTiming.INTRADAY, "mll_timing"),
        basis=v(DrawdownBasis.EQUITY, "mll_basis"),
        locks_at=v(starting_balance, "mll_locks_at"),
        mode=v(MLLMode.EOD_TRAILING_INTRADAY_ENFORCED, "mll_mode"),
    )


def _topstep_combine(size: int) -> FirmProfile:
    target, minis, micros, mll, dll, best_day = _TOPSTEP_COMBINE[size]
    combine = _v("topstep_combine")
    consistency_src = _v("topstep_consistency")
    dll_src = _v("topstep_dll")

    return FirmProfile(
        firm_id="topstep",
        program_id="trading_combine",
        program_name="Trading Combine",
        stage=Stage.EVALUATION,
        account_size=size,
        ruleset_version=RULESET_VERSION,
        effective_from=VERIFIED_AT,
        source_url=SOURCES["topstep_combine"][1],
        retrieved_at=None,
        verification_status=VerificationStatus.OFFICIAL_SOURCE_VERIFIED,
        initial_balance=combine(size, "initial_balance"),
        profit_target=combine(target, "profit_target"),
        max_loss_limit=_topstep_mll(mll, size),
        position_limits=PositionLimits(
            max_minis=combine(minis, "max_minis"),
            max_micros=combine(micros, "max_micros"),
            micro_to_mini_ratio=combine(10, "micro_to_mini_ratio"),
        ),
        automation=_topstep_automation(),
        consistency=ConsistencyRule(
            max_best_day_fraction=consistency_src(0.50, "max_best_day_fraction"),
            applies_to=consistency_src("evaluation", "consistency_applies_to"),
            target_increase_effect=consistency_src(
                "exceeding the guideline raises the profit target; the account is "
                "not failed",
                "target_increase_effect",
            ),
            recommended_max_best_day=consistency_src(
                best_day, "recommended_max_best_day"),
        ),
        # The Combine ships without a daily loss limit; one may be purchased.
        daily_loss_limit=not_applicable(
            "daily_loss_limit",
            "the Trading Combine has no daily loss limit unless one is purchased",
        ),
        daily_loss_limit_mode=dll_src(DailyLossLimitMode.NONE,
                                      "daily_loss_limit_mode"),
        purchase_set_daily_loss_limit=dll_src(dll, "purchase_set_daily_loss_limit"),
        min_trading_days=combine(2, "min_trading_days"),
        profit_split=not_applicable(
            "profit_split", "the Combine is an evaluation; no split until funded"),
        payout_cadence=not_applicable("payout_cadence",
                                      "no payouts during an evaluation"),
        activation_fee=unknown("activation_fee", _UNVERIFIED_NOTE),
        notes=(
            "PRIMARY_AUTOMATION_TARGET. Hitting a purchased daily loss limit "
            "flattens positions and locks the session; it is not a Combine "
            "eligibility violation."
        ),
        **_topstep_session_rules(),
    )


# =========================================================================
# Topstep -- Express Funded (funded simulated)
# =========================================================================

#: Standard payout cap, Consistency payout cap
_TOPSTEP_XFA_CAPS = {
    50_000: (2_000, 3_000),
    100_000: (3_000, 4_000),
    150_000: (5_000, 6_000),
}


def _topstep_xfa(size: int, variant: str) -> FirmProfile:
    """An Express Funded account.

    Combine objectives are **not** inherited. The profit target and minimum
    trading days are evaluation objectives; an account that has already passed
    the Combine does not carry them, so they are ``NOT_APPLICABLE`` rather than
    copied across. The risk rules an XFA does have -- its own MLL and optional
    DLL -- were not part of the source review and stay ``UNKNOWN``, which is why
    these profiles cannot track a loss limit. Copying the Combine's $2,000 MLL
    onto a $50K XFA would produce a simulator that runs perfectly and is wrong.
    """
    standard_cap, consistency_cap = _TOPSTEP_XFA_CAPS[size]
    payout = _v("topstep_payout")
    xfa = _v("topstep_xfa")
    cap = standard_cap if variant == "standard" else consistency_cap

    return FirmProfile(
        firm_id="topstep",
        program_id=f"express_funded_{variant}",
        program_name=f"Express Funded Account ({variant.title()})",
        stage=Stage.FUNDED_SIM,
        account_size=size,
        ruleset_version=RULESET_VERSION,
        effective_from=VERIFIED_AT,
        source_url=SOURCES["topstep_xfa"][1],
        retrieved_at=None,
        verification_status=VerificationStatus.OFFICIAL_SOURCE_VERIFIED,
        initial_balance=xfa(size, "initial_balance"),
        profit_target=not_applicable(
            "profit_target",
            "an Express Funded account has no evaluation profit target; the "
            "Combine objective is not inherited",
        ),
        max_loss_limit=MaxLossLimit(
            drawdown_type=unknown("mll_drawdown_type",
                                  "XFA drawdown type not covered by the review"),
            threshold=unknown("mll_threshold",
                              "XFA MLL amounts were not verified and must not be "
                              "inherited from the Combine"),
            calculation_method=unknown("mll_calculation_method", _UNVERIFIED_NOTE),
            timing=unknown("mll_timing", _UNVERIFIED_NOTE),
            basis=unknown("mll_basis", _UNVERIFIED_NOTE),
            locks_at=unknown("mll_locks_at", _UNVERIFIED_NOTE),
            mode=unknown("mll_mode", _UNVERIFIED_NOTE),
        ),
        position_limits=PositionLimits(
            max_minis=unknown("max_minis", "XFA scaling plan not verified"),
            max_micros=unknown("max_micros", "XFA scaling plan not verified"),
        ),
        automation=_topstep_automation(),
        consistency=(
            ConsistencyRule(
                max_best_day_fraction=unknown(
                    "max_best_day_fraction",
                    "the Consistency payout structure's threshold was not verified",
                ),
                applies_to=payout("payout", "consistency_applies_to"),
                target_increase_effect=unknown("target_increase_effect",
                                               _UNVERIFIED_NOTE),
            ) if variant == "consistency" else None
        ),
        daily_loss_limit=unknown(
            "daily_loss_limit",
            "the XFA daily loss limit is optional; amounts were not verified"),
        daily_loss_limit_mode=unknown("daily_loss_limit_mode", _UNVERIFIED_NOTE),
        min_trading_days=not_applicable(
            "min_trading_days",
            "no evaluation minimum on a funded account; winning-day requirements "
            "are a payout condition and live in the payout policy",
        ),
        profit_split=payout(0.90, "profit_split"),
        payout_cadence=unknown("payout_cadence", _UNVERIFIED_NOTE),
        activation_fee=unknown("activation_fee", _UNVERIFIED_NOTE),
        payout_policy=PayoutPolicy(
            xfa=XFAParameters(
                first_payout_cap=payout(cap, "first_payout_cap"),
                subsequent_payout_cap=payout(cap, "subsequent_payout_cap"),
                min_trading_days_for_payout=unknown(
                    "min_winning_days_for_payout",
                    "winning-day requirement not covered by the review"),
                profit_split=payout(0.90, "payout_profit_split"),
                note=(f"{variant} payout structure cap for the {size:,} account; a "
                      "payout rule, not a Combine profit target"),
            ),
            cadence=unknown("payout_cadence", _UNVERIFIED_NOTE),
        ),
        notes=(
            "Express Funded parameters were only partially covered by the review: "
            "payout caps and the 90/10 split are verified, the risk rules are not. "
            "Combine objectives are deliberately not inherited."
        ),
        **_topstep_session_rules(),
    )


def _topstep_live_funded(size: int) -> FirmProfile:
    """Live Funded account.

    Registered so live-funded rules cannot be silently confused with an XFA's,
    and left entirely unverified so nothing can adjudicate against them. This
    system does not submit orders anywhere, least of all here.
    """
    return FirmProfile(
        firm_id="topstep",
        program_id="live_funded",
        program_name="Live Funded Account",
        stage=Stage.LIVE_FUNDED,
        account_size=size,
        ruleset_version=RULESET_VERSION,
        effective_from=VERIFIED_AT,
        source_url="https://help.topstep.com/ (not covered by the 2026-08-15 review)",
        retrieved_at=None,
        verification_status=VerificationStatus.UNKNOWN,
        initial_balance=unknown("initial_balance", _UNVERIFIED_NOTE),
        profit_target=not_applicable("profit_target",
                                     "a live funded account has no evaluation target"),
        max_loss_limit=MaxLossLimit(
            drawdown_type=unknown("mll_drawdown_type", _UNVERIFIED_NOTE),
            threshold=unknown("mll_threshold", _UNVERIFIED_NOTE),
            calculation_method=unknown("mll_calculation_method", _UNVERIFIED_NOTE),
            timing=unknown("mll_timing", _UNVERIFIED_NOTE),
            basis=unknown("mll_basis", _UNVERIFIED_NOTE),
            locks_at=unknown("mll_locks_at", _UNVERIFIED_NOTE),
            mode=unknown("mll_mode", _UNVERIFIED_NOTE),
        ),
        position_limits=PositionLimits(
            max_minis=unknown("max_minis", _UNVERIFIED_NOTE),
            max_micros=unknown("max_micros", _UNVERIFIED_NOTE),
        ),
        automation=_topstep_automation(),
        daily_loss_limit=unknown("daily_loss_limit", _UNVERIFIED_NOTE),
        daily_loss_limit_mode=unknown("daily_loss_limit_mode", _UNVERIFIED_NOTE),
        min_trading_days=not_applicable("min_trading_days",
                                        "no evaluation minimum on a funded account"),
        notes=(
            "LIVE CAPITAL. No risk rule in this profile is verified. Registered only "
            "so live-funded rules cannot be mistaken for Express Funded ones."
        ),
        **_topstep_session_rules(),
    )


# =========================================================================
# Apex -- EOD Performance Accounts
# =========================================================================

#: EOD drawdown, max contracts
_APEX_EOD = {
    25_000: (1_000, 2),
    50_000: (2_000, 4),
    100_000: (3_000, 6),
    150_000: (4_000, 10),
}


def _apex_eod(size: int) -> FirmProfile:
    """Apex EOD Performance Account.

    Structurally the same shape as Topstep's MLL -- a threshold recomputed once
    per day from end-of-day balance, then enforced during the following session
    -- with a different trailing amount and, crucially, unverified lock
    behaviour. Whether an EOD threshold stops trailing at the starting balance
    is the difference between a drawdown that eventually stops tightening and
    one that never does, so it stays ``UNKNOWN`` and the tracker refuses.
    """
    drawdown, contracts = _APEX_EOD[size]
    v = _v("apex_eod")
    return FirmProfile(
        firm_id="apex",
        program_id="eod_pa",
        program_name="EOD Performance Account",
        stage=Stage.FUNDED_SIM,
        account_size=size,
        ruleset_version=RULESET_VERSION,
        effective_from=VERIFIED_AT,
        source_url=SOURCES["apex_eod"][1],
        retrieved_at=None,
        verification_status=VerificationStatus.OFFICIAL_SOURCE_VERIFIED,
        initial_balance=v(size, "initial_balance"),
        profit_target=not_applicable(
            "profit_target",
            "a Performance Account is post-evaluation; the evaluation values were "
            "not verified and are deliberately not invented",
        ),
        max_loss_limit=MaxLossLimit(
            drawdown_type=v("eod_trailing", "mll_drawdown_type"),
            threshold=v(drawdown, "mll_threshold"),
            calculation_method=v(
                "The drawdown is calculated once per day from end-of-day balance; "
                "intraday trailing drawdown does not apply. The resulting level is "
                "enforced during the trading day.",
                "mll_calculation_method",
            ),
            timing=v(DrawdownTiming.END_OF_DAY, "mll_timing"),
            basis=v(DrawdownBasis.BALANCE, "mll_basis"),
            locks_at=unknown("mll_locks_at",
                             "Apex lock behaviour was not covered by the review"),
            mode=v(MLLMode.EOD_TRAILING_INTRADAY_ENFORCED, "mll_mode"),
        ),
        position_limits=PositionLimits(
            max_minis=v(contracts, "max_minis"),
            max_micros=unknown("max_micros", "Apex micro limits were not verified"),
        ),
        automation=AutomationPolicy(
            stance=unknown("automation_stance", _UNVERIFIED_NOTE),
            api_available=unknown("api_available", _UNVERIFIED_NOTE),
            requires_local_execution=unknown("requires_local_execution",
                                             _UNVERIFIED_NOTE),
            prohibits_vps=unknown("prohibits_vps", _UNVERIFIED_NOTE),
        ),
        daily_loss_limit=unknown(
            "daily_loss_limit",
            "the source confirms a DLL is enforced intraday, but the amounts were "
            "not verified and the instruction was NOT to invent them",
        ),
        daily_loss_limit_mode=unknown("daily_loss_limit_mode",
                                      "Apex DLL amounts and mode not verified"),
        min_trading_days=unknown("min_trading_days", _UNVERIFIED_NOTE),
        profit_split=v(1.00, "profit_split"),
        payout_cadence=unknown("payout_cadence", _UNVERIFIED_NOTE),
        activation_fee=unknown("activation_fee", _UNVERIFIED_NOTE),
        notes=(
            "Tier-based scaling; 100% payout split subject to eligibility. No "
            "intraday trailing: the threshold moves once per day and is then "
            "enforced continuously."
        ),
    )


# =========================================================================
# My Funded Futures -- Rapid
# =========================================================================

#: profit target, EOD max loss
_MFFU_RAPID = {
    25_000: (1_500, 1_000),
    50_000: (3_000, 2_000),
    100_000: (6_000, 3_000),
    150_000: (9_000, 4_500),
}


def _mffu_rapid_evaluation(size: int) -> FirmProfile:
    target, max_loss = _MFFU_RAPID[size]
    v = _v("mffu_rapid")
    consistency_src = _v("mffu_consistency")
    return FirmProfile(
        firm_id="mffu",
        program_id="rapid",
        program_name="Rapid",
        stage=Stage.EVALUATION,
        account_size=size,
        ruleset_version=RULESET_VERSION,
        effective_from=VERIFIED_AT,
        source_url=SOURCES["mffu_rapid"][1],
        retrieved_at=None,
        verification_status=VerificationStatus.OFFICIAL_SOURCE_VERIFIED,
        initial_balance=v(size, "initial_balance"),
        profit_target=v(target, "profit_target"),
        max_loss_limit=MaxLossLimit(
            drawdown_type=v("eod", "mll_drawdown_type"),
            threshold=v(max_loss, "mll_threshold"),
            calculation_method=v(
                "End-of-day maximum loss for the Rapid evaluation. Whether the "
                "level is additionally enforced intraday was not covered by the "
                "review and is not assumed.",
                "mll_calculation_method",
            ),
            timing=v(DrawdownTiming.END_OF_DAY, "mll_timing"),
            basis=unknown("mll_basis",
                          "balance vs equity was not covered by the review"),
            locks_at=unknown("mll_locks_at", _UNVERIFIED_NOTE),
            mode=unknown(
                "mll_mode",
                "the drawdown type is verified as EOD, but whether it is also "
                "enforced intraday is not -- and that difference decides whether an "
                "open loser ends the evaluation",
            ),
        ),
        position_limits=PositionLimits(
            max_minis=unknown("max_minis", "Rapid contract limits were not verified"),
            max_micros=unknown("max_micros", "Rapid contract limits were not verified"),
        ),
        automation=AutomationPolicy(
            stance=unknown("automation_stance", _UNVERIFIED_NOTE),
            api_available=unknown("api_available", _UNVERIFIED_NOTE),
        ),
        consistency=ConsistencyRule(
            max_best_day_fraction=consistency_src(0.50, "max_best_day_fraction"),
            applies_to=consistency_src("evaluation", "consistency_applies_to"),
            target_increase_effect=consistency_src(
                "exceeding the threshold does not fail the account; further trading "
                "can restore compliance",
                "target_increase_effect",
            ),
        ),
        daily_loss_limit=not_applicable(
            "daily_loss_limit",
            "Rapid evaluations have no daily loss limit at any account size",
        ),
        daily_loss_limit_mode=v(DailyLossLimitMode.NONE, "daily_loss_limit_mode"),
        min_trading_days=v(2, "min_trading_days"),
        profit_split=not_applicable("profit_split", "no split during an evaluation"),
        payout_cadence=not_applicable("payout_cadence",
                                      "no payouts during an evaluation"),
        activation_fee=unknown("activation_fee", _UNVERIFIED_NOTE),
        notes=(
            "Rapid EVALUATION rules only. Funded-stage rules are registered "
            "separately and are not derived from these."
        ),
    )


def _mffu_rapid_funded(size: int) -> FirmProfile:
    """Funded stage, stored separately and left unverified.

    The instruction was to keep funded-stage rules apart from the evaluation's,
    and the review did not cover them. Registering the stage with everything
    unknown is what makes the separation enforceable: a lookup for the funded
    stage cannot quietly return the evaluation's EOD numbers, which is what
    would happen if the stage simply did not exist in the registry.
    """
    return FirmProfile(
        firm_id="mffu",
        program_id="rapid",
        program_name="Rapid",
        stage=Stage.FUNDED_SIM,
        account_size=size,
        ruleset_version=RULESET_VERSION,
        effective_from=VERIFIED_AT,
        source_url="https://myfundedfutures.com/ (funded stage not covered by review)",
        retrieved_at=None,
        verification_status=VerificationStatus.UNKNOWN,
        initial_balance=unknown("initial_balance", _UNVERIFIED_NOTE),
        profit_target=not_applicable("profit_target",
                                     "no evaluation target once funded"),
        max_loss_limit=MaxLossLimit(
            drawdown_type=unknown(
                "mll_drawdown_type",
                "the funded stage's drawdown type was not verified and must not be "
                "assumed to match the evaluation's EOD rule",
            ),
            threshold=unknown("mll_threshold", _UNVERIFIED_NOTE),
            calculation_method=unknown("mll_calculation_method", _UNVERIFIED_NOTE),
            timing=unknown("mll_timing", _UNVERIFIED_NOTE),
            basis=unknown("mll_basis", _UNVERIFIED_NOTE),
            locks_at=unknown("mll_locks_at", _UNVERIFIED_NOTE),
            mode=unknown("mll_mode", _UNVERIFIED_NOTE),
        ),
        position_limits=PositionLimits(
            max_minis=unknown("max_minis", _UNVERIFIED_NOTE),
            max_micros=unknown("max_micros", _UNVERIFIED_NOTE),
        ),
        automation=AutomationPolicy(
            stance=unknown("automation_stance", _UNVERIFIED_NOTE),
            api_available=unknown("api_available", _UNVERIFIED_NOTE),
        ),
        daily_loss_limit=unknown("daily_loss_limit", _UNVERIFIED_NOTE),
        daily_loss_limit_mode=unknown("daily_loss_limit_mode", _UNVERIFIED_NOTE),
        min_trading_days=not_applicable("min_trading_days",
                                        "no evaluation minimum once funded"),
        notes=("Rapid FUNDED stage. Nothing verified; evaluation rules do not "
               "carry over."),
    )


# =========================================================================
# Alpha Futures -- research comparison only
# =========================================================================


def _alpha() -> FirmProfile:
    return FirmProfile(
        firm_id="alpha_futures",
        program_id="research_comparison_only",
        program_name="Alpha Futures (comparison)",
        stage=Stage.EVALUATION,
        account_size=0,
        ruleset_version=RULESET_VERSION,
        effective_from=VERIFIED_AT,
        source_url="https://alphafutures.com/ (not covered by the 2026-08-15 review)",
        retrieved_at=None,
        verification_status=VerificationStatus.USER_SUPPLIED,
        initial_balance=unknown("initial_balance", _UNVERIFIED_NOTE),
        profit_target=unknown("profit_target", _UNVERIFIED_NOTE),
        max_loss_limit=MaxLossLimit(
            drawdown_type=unknown("mll_drawdown_type", _UNVERIFIED_NOTE),
            threshold=unknown("mll_threshold", _UNVERIFIED_NOTE),
            calculation_method=unknown("mll_calculation_method", _UNVERIFIED_NOTE),
            timing=unknown("mll_timing", _UNVERIFIED_NOTE),
            basis=unknown("mll_basis", _UNVERIFIED_NOTE),
            locks_at=unknown("mll_locks_at", _UNVERIFIED_NOTE),
            mode=unknown("mll_mode", _UNVERIFIED_NOTE),
        ),
        position_limits=PositionLimits(
            max_minis=unknown("max_minis", _UNVERIFIED_NOTE),
            max_micros=unknown("max_micros", _UNVERIFIED_NOTE),
        ),
        automation=AutomationPolicy(
            stance=user_supplied(
                AutomationStance.SEMI_ONLY, label="automation_stance",
                note=("operator states full automation prohibited; semi-automation "
                      "allowed subject to manual execution. Retained pending "
                      "verification of the remaining rules"),
            ),
            api_available=unknown("api_available", _UNVERIFIED_NOTE),
        ),
        daily_loss_limit=unknown("daily_loss_limit", _UNVERIFIED_NOTE),
        daily_loss_limit_mode=unknown("daily_loss_limit_mode", _UNVERIFIED_NOTE),
        min_trading_days=unknown("min_trading_days", _UNVERIFIED_NOTE),
        notes=(
            "RESEARCH COMPARISON ONLY. Not an automated-execution target: the "
            "semi-automation classification stands until every remaining rule is "
            "verified."
        ),
    )


# =========================================================================


def build_all(registry=REGISTRY):
    """Publish every profile into the registry."""
    for size in _TOPSTEP_COMBINE:
        registry.register(_topstep_combine(size))
        registry.register(_topstep_xfa(size, "standard"))
        registry.register(_topstep_xfa(size, "consistency"))
        registry.register(_topstep_live_funded(size))
    for size in _APEX_EOD:
        registry.register(_apex_eod(size))
    for size in _MFFU_RAPID:
        registry.register(_mffu_rapid_evaluation(size))
        registry.register(_mffu_rapid_funded(size))
    registry.register(_alpha())
    return registry


build_all()
