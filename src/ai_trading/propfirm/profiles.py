"""Versioned prop-firm profiles.

Extends the generic ``FirmRuleset`` rather than replacing it: the Phase 0
adjudicator still owns drawdown policy, session boundaries and day counting.
What lives here is firm-specific structure the generic model has no place for --
Topstep's Maximum Loss Limit as its own rule type, consistency ratios, position
limits in minis and micros, and automation policy.

Every value carries its own provenance and nothing is believed by default. The
verification architecture is unchanged from the build in which no rule could be
checked at all: :meth:`RuleValue.require` still refuses, and a profile is
adjudication-ready only when every field backing a decision is sourced. What
changed is the *inputs*, not the gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from enum import Enum

from .hierarchy import (
    CAPABILITY_FIELDS,
    Capability,
    FieldProvenance,
    PayoutPolicy,
    RulesetKey,
    Stage,
    VerificationLevel,
)
from .limits import (
    AccountLimitMonitor,
    DailyLossLimitMode,
    DailyLossLimitTracker,
    EligibilityOutcome,
    MaximumLossLimitTracker,
)
from .verification import (
    RuleValue,
    UnverifiedRuleError,
    VerificationStatus,
    unknown,
    user_supplied,
)

__all__ = [
    "DrawdownBasis", "DrawdownTiming", "MaxLossLimit", "ConsistencyRule",
    "PositionLimits", "AutomationPolicy", "ProhibitedPractice", "FirmProfile",
    "PropFirmRegistry", "REGISTRY", "AutomationStance", "ConsistencyResult",
]


class DrawdownBasis(str, Enum):
    BALANCE = "balance"
    EQUITY = "equity"
    UNKNOWN = "unknown"


class DrawdownTiming(str, Enum):
    INTRADAY = "intraday"
    END_OF_DAY = "end_of_day"
    UNKNOWN = "unknown"


class AutomationStance(str, Enum):
    ALLOWED = "allowed"
    SEMI_ONLY = "semi_automation_only"
    PROHIBITED = "prohibited"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MaxLossLimit:
    """A firm's hard failure rule, modelled as its own type.

    Topstep's Maximum Loss Limit is not the FTMO-style "5% daily / 10% total"
    pair, and reusing those defaults would silently adjudicate a Combine under
    rules it does not have. The calculation method matters as much as the
    threshold: a limit that trails intraday unrealised equity is materially
    harsher than one computed on end-of-day closed balance, and the difference
    decides whether an open loser fails the account.
    """

    drawdown_type: RuleValue          # "trailing" | "static" | "eod_trailing"
    threshold: RuleValue              # currency amount
    calculation_method: RuleValue     # prose description of the exact rule
    timing: RuleValue                 # DrawdownTiming
    basis: RuleValue                  # DrawdownBasis
    locks_at: RuleValue = field(default_factory=lambda: unknown("locks_at"))
    #: The executable form of ``calculation_method``. Prose describes the rule;
    #: this is what the tracker actually runs, so it is verified separately.
    mode: RuleValue = field(default_factory=lambda: unknown("mll_mode"))

    @property
    def fully_verified(self) -> bool:
        return all(r.is_verified for r in
                   (self.drawdown_type, self.threshold, self.calculation_method,
                    self.timing, self.basis, self.mode))

    @property
    def unresolved(self) -> list[str]:
        return [r.label for r in (self.drawdown_type, self.threshold,
                                  self.calculation_method, self.timing,
                                  self.basis, self.locks_at, self.mode)
                if not r.is_verified]

    def build_tracker(self, starting_balance: float) -> MaximumLossLimitTracker:
        """Instantiate a live tracker, refusing on anything unverified.

        The refusal is the point. A tracker built from a guessed threshold runs
        happily and reports failures that never happened, or misses ones that
        did -- and its output looks identical either way.
        """
        self.require_for_adjudication()
        if not self.locks_at.is_verified:
            raise UnverifiedRuleError(
                "mll_locks_at is unverified: whether the threshold freezes once it "
                "reaches the starting balance decides whether further profit keeps "
                "tightening the account, and assuming either way is a guess"
            )
        return MaximumLossLimitTracker(
            starting_balance=float(starting_balance),
            trailing_amount=float(self.threshold.require("loss-limit tracking")),
            mode=self.mode.require("loss-limit tracking"),
            locks_at_starting_balance=(
                self.locks_at.get() is not None
                and float(self.locks_at.get()) == float(starting_balance)
            ),
        )

    def require_for_adjudication(self) -> None:
        """Refuse to adjudicate on an unverified loss limit."""
        if not self.fully_verified:
            raise UnverifiedRuleError(
                "cannot adjudicate a Maximum Loss Limit whose "
                f"{', '.join(self.unresolved)} {'is' if len(self.unresolved) == 1 else 'are'} "
                "unverified -- the calculation method decides whether an open loser "
                "fails the account, so a guess here is not conservative in either direction"
            )

    def to_dict(self) -> dict:
        return {
            "drawdown_type": self.drawdown_type.to_dict(),
            "threshold": self.threshold.to_dict(),
            "calculation_method": self.calculation_method.to_dict(),
            "timing": self.timing.to_dict(),
            "basis": self.basis.to_dict(),
            "locks_at": self.locks_at.to_dict(),
            "fully_verified": self.fully_verified,
            "unresolved": self.unresolved,
        }


@dataclass(frozen=True)
class ConsistencyResult:
    """Outcome of a consistency check, kept distinct from a rule violation."""

    best_day_profit: float
    total_profit: float
    best_day_percentage: float | None
    threshold: float | None
    threshold_verified: bool
    outcome: EligibilityOutcome
    reason: str
    #: Profit target the trader must now reach for the best day to be within
    #: the guideline. ``None`` when the guideline is met or undecidable.
    required_total_profit: float | None = None
    #: Currency amount the firm recommends as a per-day ceiling, where stated.
    recommended_max_best_day: float | None = None

    @property
    def passes(self) -> bool | None:
        """Kept for callers that only want the tri-state answer."""
        if self.outcome is EligibilityOutcome.UNDETERMINED:
            return None
        return self.outcome is not EligibilityOutcome.CONSISTENCY_NOT_MET

    def __getitem__(self, key: str):
        """Mapping access, so existing report code keeps working."""
        return self.to_dict()[key]

    def get(self, key: str, default=None):
        return self.to_dict().get(key, default)

    def to_dict(self) -> dict:
        return {
            "best_day_profit": self.best_day_profit,
            "total_profit": self.total_profit,
            "best_day_percentage": self.best_day_percentage,
            "threshold": self.threshold,
            "threshold_verified": self.threshold_verified,
            "outcome": self.outcome.value,
            "passes": self.passes,
            "reason": self.reason,
            "required_total_profit": self.required_total_profit,
            "recommended_max_best_day": self.recommended_max_best_day,
        }


@dataclass(frozen=True)
class ConsistencyRule:
    """Best-day-versus-total profit constraint.

    Missing it is **not** a rule violation and the distinction is load-bearing.
    A consistency breach does not end an evaluation -- it raises the profit
    target until the best day is a small enough share of the total. Modelling it
    as a failure would tell a trader their account is dead when it is merely
    slower, and would make the simulator's pass rate wrong in the pessimistic
    direction for exactly the strategies that produce one large winner.
    """

    max_best_day_fraction: RuleValue      # e.g. 0.50
    applies_to: RuleValue                 # "evaluation" | "funded" | "both"
    target_increase_effect: RuleValue = field(
        default_factory=lambda: unknown("target_increase_effect")
    )
    #: Currency ceiling the firm publishes as guidance, where one exists. Advice
    #: rather than a threshold, so it never drives the outcome.
    recommended_max_best_day: RuleValue = field(
        default_factory=lambda: unknown("recommended_max_best_day")
    )

    def required_total_for(self, best_day_profit: float) -> float | None:
        """Total profit at which ``best_day_profit`` satisfies the guideline."""
        threshold = self.max_best_day_fraction.get()
        if threshold is None or threshold <= 0 or best_day_profit <= 0:
            return None
        return best_day_profit / threshold

    def evaluate(self, best_day_profit: float, total_profit: float) -> ConsistencyResult:
        """Compute the ratio and, where verified, the outcome.

        The ratio is always reported. The *decision* is withheld unless the
        threshold is verified, because a consistency call made against a guessed
        percentage is worse than no call at all.
        """
        ratio = best_day_profit / total_profit if total_profit > 0 else None
        threshold = self.max_best_day_fraction.get()
        verified_threshold = self.max_best_day_fraction.is_verified
        recommended = self.recommended_max_best_day.get()

        if not verified_threshold or ratio is None:
            return ConsistencyResult(
                best_day_profit, total_profit, ratio, threshold, verified_threshold,
                EligibilityOutcome.UNDETERMINED,
                ("consistency threshold unverified" if ratio is not None
                 else "no positive total profit"),
                recommended_max_best_day=recommended,
            )

        if ratio < threshold:
            return ConsistencyResult(
                best_day_profit, total_profit, ratio, threshold, True,
                EligibilityOutcome.ELIGIBLE,
                f"best day {ratio:.1%} of total, guideline {threshold:.0%}",
                recommended_max_best_day=recommended,
            )

        required = self.required_total_for(best_day_profit)
        return ConsistencyResult(
            best_day_profit, total_profit, ratio, threshold, True,
            EligibilityOutcome.CONSISTENCY_NOT_MET,
            (f"best day {ratio:.1%} of total exceeds the {threshold:.0%} guideline; "
             f"the profit target rises to {required:,.2f} rather than the account "
             "failing"),
            required_total_profit=required,
            recommended_max_best_day=recommended,
        )

    def to_dict(self) -> dict:
        return {
            "max_best_day_fraction": self.max_best_day_fraction.to_dict(),
            "applies_to": self.applies_to.to_dict(),
            "target_increase_effect": self.target_increase_effect.to_dict(),
            "recommended_max_best_day": self.recommended_max_best_day.to_dict(),
        }


@dataclass(frozen=True)
class PositionLimits:
    """Contract limits, normalized across minis and micros."""

    max_minis: RuleValue
    max_micros: RuleValue
    micro_to_mini_ratio: RuleValue = field(
        default_factory=lambda: user_supplied(10, label="micro_to_mini_ratio",
                                              note="documented 10:1 relationship")
    )

    def mini_equivalents(self, minis: int = 0, micros: int = 0) -> float:
        """Total exposure in mini-equivalents."""
        ratio = self.micro_to_mini_ratio.get(10)
        return minis + micros / ratio

    def within_limit(self, minis: int = 0, micros: int = 0) -> bool | None:
        """Whether a position fits. ``None`` when the limit is unverified."""
        limit = self.max_minis.get()
        if limit is None:
            return None
        return self.mini_equivalents(minis, micros) <= limit + 1e-9

    def cap_mini_equivalents(self) -> float | None:
        return self.max_minis.get()

    def to_dict(self) -> dict:
        return {
            "max_minis": self.max_minis.to_dict(),
            "max_micros": self.max_micros.to_dict(),
            "micro_to_mini_ratio": self.micro_to_mini_ratio.to_dict(),
        }


class ProhibitedPractice(str, Enum):
    """Practices firms commonly forbid. Enforced by the compliance gate."""

    SIMULATOR_EXPLOITATION = "simulator_exploitation"
    STALE_FEED_EXPLOITATION = "stale_feed_exploitation"
    PRICE_DISPLAY_EXPLOITATION = "price_display_exploitation"
    SPOOFING = "spoofing"
    TRADING_OUTSIDE_BBO = "trading_outside_best_bid_offer"
    UNREALISTIC_SIM_FILLS = "unrealistic_sim_fill_exploitation"
    CROSS_ACCOUNT_HEDGING = "coordinated_cross_account_hedging"
    PROHIBITED_HFT = "prohibited_high_frequency_behaviour"
    UNFAIR_ADVANTAGE_TECH = "technology_for_unfair_advantage"
    MAX_SIZE_INTO_NEWS = "max_size_into_scheduled_news"


@dataclass(frozen=True)
class AutomationPolicy:
    """Whether and how a firm permits automated trading."""

    stance: RuleValue                      # AutomationStance
    api_available: RuleValue
    api_provider: RuleValue = field(default_factory=lambda: unknown("api_provider"))
    requires_local_execution: RuleValue = field(
        default_factory=lambda: unknown("requires_local_execution")
    )
    prohibits_vps: RuleValue = field(default_factory=lambda: unknown("prohibits_vps"))
    prohibited_practices: tuple[ProhibitedPractice, ...] = ()

    @property
    def permits_full_automation(self) -> bool | None:
        stance = self.stance.get()
        if stance is None:
            return None
        return stance == AutomationStance.ALLOWED

    def to_dict(self) -> dict:
        return {
            "stance": self.stance.to_dict(),
            "api_available": self.api_available.to_dict(),
            "api_provider": self.api_provider.to_dict(),
            "requires_local_execution": self.requires_local_execution.to_dict(),
            "prohibits_vps": self.prohibits_vps.to_dict(),
            "prohibited_practices": [p.value for p in self.prohibited_practices],
        }


@dataclass(frozen=True)
class FirmProfile:
    """One firm/program/account-size ruleset, versioned and immutable."""

    firm_id: str
    program_id: str
    account_size: int
    ruleset_version: str
    effective_from: date
    source_url: str
    retrieved_at: date | None
    verification_status: VerificationStatus

    initial_balance: RuleValue
    profit_target: RuleValue
    max_loss_limit: MaxLossLimit
    position_limits: PositionLimits
    automation: AutomationPolicy
    stage: Stage = Stage.EVALUATION
    program_name: str = ""
    consistency: ConsistencyRule | None = None
    daily_loss_limit: RuleValue = field(default_factory=lambda: unknown("daily_loss_limit"))
    #: Whether a DLL exists on this account and who set it. Separate from the
    #: amount, because "no limit" and "limit of unknown size" are different
    #: facts and only one of them blocks adjudication.
    daily_loss_limit_mode: RuleValue = field(
        default_factory=lambda: unknown("daily_loss_limit_mode")
    )
    #: Amount of the optional limit the firm sells with the account, where one
    #: exists. Published by the firm but not applied unless the trader elects it.
    purchase_set_daily_loss_limit: RuleValue = field(
        default_factory=lambda: unknown("purchase_set_daily_loss_limit")
    )
    payout_policy: PayoutPolicy | None = None
    min_trading_days: RuleValue = field(default_factory=lambda: unknown("min_trading_days"))
    trading_day_start: RuleValue = field(default_factory=lambda: unknown("trading_day_start"))
    trading_day_end: RuleValue = field(default_factory=lambda: unknown("trading_day_end"))
    forced_flat_time: RuleValue = field(default_factory=lambda: unknown("forced_flat_time"))
    session_reopen: RuleValue = field(default_factory=lambda: unknown("session_reopen"))
    overnight_allowed: RuleValue = field(default_factory=lambda: unknown("overnight_allowed"))
    timezone: str = "America/Chicago"
    profit_split: RuleValue = field(default_factory=lambda: unknown("profit_split"))
    payout_cadence: RuleValue = field(default_factory=lambda: unknown("payout_cadence"))
    activation_fee: RuleValue = field(default_factory=lambda: unknown("activation_fee"))
    notes: str = ""

    @property
    def ruleset_key(self) -> RulesetKey:
        return RulesetKey(self.firm_id, self.program_id, self.stage,
                          self.account_size, self.ruleset_version)

    @property
    def key(self) -> str:
        return str(self.ruleset_key)

    @property
    def all_rules(self) -> dict[str, RuleValue]:
        rules = {
            "initial_balance": self.initial_balance,
            "profit_target": self.profit_target,
            "daily_loss_limit": self.daily_loss_limit,
            "daily_loss_limit_mode": self.daily_loss_limit_mode,
            "min_trading_days": self.min_trading_days,
            "trading_day_start": self.trading_day_start,
            "trading_day_end": self.trading_day_end,
            "forced_flat_time": self.forced_flat_time,
            "session_reopen": self.session_reopen,
            "overnight_allowed": self.overnight_allowed,
            "profit_split": self.profit_split,
            "payout_cadence": self.payout_cadence,
            "activation_fee": self.activation_fee,
            "mll_drawdown_type": self.max_loss_limit.drawdown_type,
            "mll_threshold": self.max_loss_limit.threshold,
            "mll_calculation_method": self.max_loss_limit.calculation_method,
            "mll_timing": self.max_loss_limit.timing,
            "mll_basis": self.max_loss_limit.basis,
            "mll_mode": self.max_loss_limit.mode,
            "mll_locks_at": self.max_loss_limit.locks_at,
            "max_minis": self.position_limits.max_minis,
            "max_micros": self.position_limits.max_micros,
            "automation_stance": self.automation.stance,
            "api_available": self.automation.api_available,
        }
        if self.consistency is not None:
            rules["max_best_day_fraction"] = self.consistency.max_best_day_fraction
            rules["consistency_applies_to"] = self.consistency.applies_to
        return rules

    @property
    def unresolved_rules(self) -> list[str]:
        return sorted(name for name, rule in self.all_rules.items() if not rule.is_verified)

    @property
    def fully_verified(self) -> bool:
        return not self.unresolved_rules

    @property
    def verification_level(self) -> VerificationLevel:
        """Three-way, because "mostly verified" is its own hazard."""
        unresolved = self.unresolved_rules
        if not unresolved:
            return VerificationLevel.ADJUDICATION_READY
        if len(unresolved) == len(self.all_rules):
            return VerificationLevel.UNVERIFIED
        return VerificationLevel.PARTIALLY_VERIFIED

    # -- capability-scoped readiness --------------------------------------
    def required_fields(self, capability: Capability) -> tuple[str, ...]:
        """Fields this capability needs, restricted to ones this profile has.

        A program without a consistency rule has no consistency fields, and
        demanding them would block a capability that is simply not part of the
        program.
        """
        if capability is Capability.FULL_ADJUDICATION:
            names: list[str] = []
            for fields in CAPABILITY_FIELDS.values():
                names.extend(fields)
        else:
            names = list(CAPABILITY_FIELDS[capability])
        present = self.all_rules
        return tuple(sorted({n for n in names if n in present}))

    def missing_for(self, capability: Capability) -> list[str]:
        rules = self.all_rules
        return [name for name in self.required_fields(capability)
                if not rules[name].is_verified]

    def supports(self, capability: Capability) -> bool:
        return not self.missing_for(capability)

    def readiness(self, capability: Capability) -> VerificationLevel:
        required = self.required_fields(capability)
        missing = self.missing_for(capability)
        if not missing:
            return VerificationLevel.ADJUDICATION_READY
        if len(missing) == len(required):
            return VerificationLevel.UNVERIFIED
        return VerificationLevel.PARTIALLY_VERIFIED

    def require_capability(self, capability: Capability) -> None:
        missing = self.missing_for(capability)
        if missing:
            raise UnverifiedRuleError(
                f"{self.key} cannot support {capability.value}: "
                f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} "
                "not verified against the firm's official current documentation"
            )

    def capability_report(self) -> dict[str, dict]:
        return {
            capability.value: {
                "readiness": self.readiness(capability).value,
                "required": list(self.required_fields(capability)),
                "missing": self.missing_for(capability),
            }
            for capability in Capability
        }

    def field_provenance(self) -> list[FieldProvenance]:
        """One record per rule, verified or not.

        Emitted for every field rather than only the sourced ones: an audit
        trail that omits the gaps cannot answer the question it exists for.
        """
        records: list[FieldProvenance] = []
        for name, rule in sorted(self.all_rules.items()):
            source = rule.source
            records.append(FieldProvenance(
                field_name=name,
                value=rule.value,
                status=rule.status.value,
                source_url=source.url,
                source_title=source.document_title,
                retrieved_at=source.retrieved_at,
                verified_at=source.verified_at,
                verification_method=source.verification_method.value,
                ruleset_version=self.ruleset_version,
            ))
        return records

    def with_daily_loss_limit(self, mode: DailyLossLimitMode,
                              amount: float | None = None) -> "FirmProfile":
        """Derive the same ruleset with a daily loss limit configured.

        Optional limits are a per-account choice, not a property of the program,
        so the registry publishes the program without one and callers opt in.
        A ``PURCHASE_SET`` limit uses the firm's published amount; a
        ``PERSONAL_MANUAL`` one requires the trader to state their own, and is
        recorded as user-supplied because the firm never published it.
        """
        from dataclasses import replace

        if mode is DailyLossLimitMode.NONE:
            return replace(
                self,
                daily_loss_limit_mode=self.daily_loss_limit_mode,
                daily_loss_limit=self.daily_loss_limit,
            )
        if mode is DailyLossLimitMode.PURCHASE_SET:
            published = self.purchase_set_daily_loss_limit
            if not published.is_verified or published.get() is None:
                raise UnverifiedRuleError(
                    f"{self.key} has no verified purchase-set daily loss limit to apply"
                )
            limit = published
        else:
            if amount is None:
                raise ValueError(
                    "a PERSONAL_MANUAL daily loss limit needs the amount the trader "
                    "set in the platform -- the firm does not publish it"
                )
            limit = user_supplied(
                float(amount), label="daily_loss_limit",
                note="set by the trader in the platform; not a published firm rule",
            )
        return replace(
            self,
            daily_loss_limit=limit,
            daily_loss_limit_mode=user_supplied(
                mode, label="daily_loss_limit_mode",
                note="account-level election; the program itself makes it optional",
            ) if mode is DailyLossLimitMode.PERSONAL_MANUAL
            else self.purchase_set_daily_loss_limit_mode(),
        )

    def purchase_set_daily_loss_limit_mode(self) -> RuleValue:
        """The ``PURCHASE_SET`` mode value, carrying the published amount's source."""
        source = self.purchase_set_daily_loss_limit.source
        return RuleValue(DailyLossLimitMode.PURCHASE_SET,
                         self.purchase_set_daily_loss_limit.status, source,
                         "daily_loss_limit_mode")

    def build_limit_monitor(self) -> AccountLimitMonitor:
        """Construct the runtime loss-limit monitor for this ruleset.

        Refuses on anything unverified, via the same gate as everything else.
        """
        self.require_capability(Capability.LOSS_LIMIT_TRACKING)
        starting = float(self.initial_balance.require("loss-limit tracking"))
        mll = self.max_loss_limit.build_tracker(starting)

        mode = self.daily_loss_limit_mode.require("loss-limit tracking")
        if mode is DailyLossLimitMode.NONE:
            dll = DailyLossLimitTracker(amount=None, mode=mode)
        else:
            amount = self.daily_loss_limit.require("loss-limit tracking")
            dll = DailyLossLimitTracker(amount=float(amount), mode=mode)
        return AccountLimitMonitor(mll=mll, dll=dll)

    def require_adjudication_ready(self) -> None:
        """Refuse to adjudicate an account against unverified rules."""
        if not self.fully_verified:
            raise UnverifiedRuleError(
                f"{self.key} has {len(self.unresolved_rules)} unverified rule(s): "
                f"{', '.join(self.unresolved_rules[:6])}"
                f"{'...' if len(self.unresolved_rules) > 6 else ''}. "
                "Verify against the firm's official current documentation before "
                "adjudicating a real evaluation."
            )

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "firm_id": self.firm_id,
            "program_id": self.program_id,
            "program_name": self.program_name,
            "stage": self.stage.value,
            "account_size": self.account_size,
            "ruleset_version": self.ruleset_version,
            "effective_from": self.effective_from.isoformat(),
            "source_url": self.source_url,
            "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at else None,
            "verification_status": self.verification_status.value,
            "verification_level": self.verification_level.value,
            "fully_verified": self.fully_verified,
            "unresolved_rules": self.unresolved_rules,
            "max_loss_limit": self.max_loss_limit.to_dict(),
            "position_limits": self.position_limits.to_dict(),
            "automation": self.automation.to_dict(),
            "consistency": self.consistency.to_dict() if self.consistency else None,
            "payout_policy": self.payout_policy.to_dict() if self.payout_policy else None,
            "capabilities": self.capability_report(),
            "rules": {name: rule.to_dict() for name, rule in self.all_rules.items()},
            "field_provenance": [p.to_dict() for p in self.field_provenance()],
            "notes": self.notes,
        }


class PropFirmRegistry:
    """Versioned catalogue of firm profiles, addressed by the full hierarchy.

    Lookup takes every component of the key. There is no ``get("topstep")``,
    because there is no such thing as Topstep's drawdown -- only a particular
    program's, at a particular stage, for a particular account size, as of a
    particular ruleset version. Published versions are immutable; a rule change
    is a new version, never an edit.
    """

    def __init__(self) -> None:
        self._profiles: dict[str, FirmProfile] = {}

    def register(self, profile: FirmProfile) -> FirmProfile:
        existing = self._profiles.get(profile.key)
        if existing is not None and existing.to_dict() != profile.to_dict():
            raise ValueError(
                f"{profile.key} is already published with different content. "
                "Published rulesets are immutable -- publish a new ruleset_version "
                "when a firm changes its rules."
            )
        self._profiles[profile.key] = profile
        return profile

    def get(self, key: str | RulesetKey) -> FirmProfile | None:
        return self._profiles.get(str(key))

    # -- hierarchical navigation -----------------------------------------
    def firms(self) -> list[str]:
        return sorted({p.firm_id for p in self._profiles.values()})

    def programs(self, firm_id: str) -> list[str]:
        return sorted({p.program_id for p in self._profiles.values()
                       if p.firm_id == firm_id})

    def stages(self, firm_id: str, program_id: str) -> list[Stage]:
        found = {p.stage for p in self._profiles.values()
                 if p.firm_id == firm_id and p.program_id == program_id}
        return sorted(found, key=lambda s: s.value)

    def account_sizes(self, firm_id: str, program_id: str,
                      stage: Stage) -> list[int]:
        return sorted({p.account_size for p in self._profiles.values()
                       if p.firm_id == firm_id and p.program_id == program_id
                       and p.stage is stage})

    def versions(self, firm_id: str, program_id: str, stage: Stage,
                 account_size: int) -> list[FirmProfile]:
        """All published versions for one account, oldest effective date first."""
        return sorted(
            (p for p in self._profiles.values()
             if p.firm_id == firm_id and p.program_id == program_id
             and p.stage is stage and p.account_size == account_size),
            key=lambda p: (p.effective_from, p.ruleset_version),
        )

    def resolve(self, firm_id: str, program_id: str, stage: Stage,
                account_size: int, *, ruleset_version: str | None = None,
                as_of: date | None = None) -> FirmProfile | None:
        """Look up one ruleset, defaulting to the latest effective version.

        ``as_of`` selects the ruleset that was in force on that date rather than
        the newest one, because a rule published in September does not
        retroactively govern an evaluation traded in July.
        """
        candidates = self.versions(firm_id, program_id, stage, account_size)
        if ruleset_version is not None:
            for profile in candidates:
                if profile.ruleset_version == ruleset_version:
                    return profile
            return None
        if as_of is not None:
            candidates = [p for p in candidates if p.effective_from <= as_of]
        return candidates[-1] if candidates else None

    def by_firm(self, firm_id: str) -> list[FirmProfile]:
        return sorted((p for p in self._profiles.values() if p.firm_id == firm_id),
                      key=lambda p: (p.program_id, p.stage.value, p.account_size))

    def by_program(self, firm_id: str, program_id: str) -> list[FirmProfile]:
        return sorted((p for p in self._profiles.values()
                       if p.firm_id == firm_id and p.program_id == program_id),
                      key=lambda p: (p.stage.value, p.account_size))

    def all(self) -> list[FirmProfile]:
        return sorted(self._profiles.values(), key=lambda p: p.key)

    def adjudication_ready(self) -> list[FirmProfile]:
        return [p for p in self._profiles.values() if p.fully_verified]

    def by_verification_level(self, level: VerificationLevel) -> list[FirmProfile]:
        return sorted((p for p in self._profiles.values()
                       if p.verification_level is level), key=lambda p: p.key)

    def __len__(self) -> int:
        return len(self._profiles)


REGISTRY = PropFirmRegistry()
