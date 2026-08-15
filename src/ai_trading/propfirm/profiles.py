"""Versioned prop-firm profiles.

Extends the generic ``FirmRuleset`` rather than replacing it: the Phase 0
adjudicator still owns drawdown policy, session boundaries and day counting.
What lives here is firm-specific structure the generic model has no place for --
Topstep's Maximum Loss Limit as its own rule type, consistency ratios, position
limits in minis and micros, and automation policy.

**Nothing in this module is verified.** Every value was supplied by the operator
and could not be checked against firm documentation, because network access to
every firm's site is blocked in this environment. Values are therefore
``USER_SUPPLIED`` and rules whose exact behaviour was not stated are ``UNKNOWN``.
Compliance assertions refuse on both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from enum import Enum

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
    "PropFirmRegistry", "REGISTRY", "AutomationStance",
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

    @property
    def fully_verified(self) -> bool:
        return all(r.is_verified for r in
                   (self.drawdown_type, self.threshold, self.calculation_method,
                    self.timing, self.basis))

    @property
    def unresolved(self) -> list[str]:
        return [r.label for r in (self.drawdown_type, self.threshold,
                                  self.calculation_method, self.timing,
                                  self.basis, self.locks_at) if not r.is_verified]

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
class ConsistencyRule:
    """Best-day-versus-total profit constraint."""

    max_best_day_fraction: RuleValue      # e.g. 0.50
    applies_to: RuleValue                 # "evaluation" | "funded" | "both"
    target_increase_effect: RuleValue = field(
        default_factory=lambda: unknown("target_increase_effect")
    )

    def evaluate(self, best_day_profit: float, total_profit: float) -> dict:
        """Compute the ratio and, where verified, the pass decision.

        The ratio is always reported. The *decision* is withheld unless the
        threshold is verified, because a consistency call made against a guessed
        percentage is worse than no call at all.
        """
        if total_profit <= 0:
            ratio = None
        else:
            ratio = best_day_profit / total_profit

        result = {
            "best_day_profit": best_day_profit,
            "total_profit": total_profit,
            "best_day_percentage": ratio,
            "threshold": self.max_best_day_fraction.get(),
            "threshold_verified": self.max_best_day_fraction.is_verified,
        }
        if not self.max_best_day_fraction.is_verified or ratio is None:
            result["passes"] = None
            result["reason"] = (
                "consistency threshold unverified" if ratio is not None
                else "no positive total profit"
            )
        else:
            threshold = self.max_best_day_fraction.value
            result["passes"] = ratio < threshold
            result["reason"] = (
                f"best day {ratio:.1%} of total, limit {threshold:.0%}"
            )
        return result

    def to_dict(self) -> dict:
        return {
            "max_best_day_fraction": self.max_best_day_fraction.to_dict(),
            "applies_to": self.applies_to.to_dict(),
            "target_increase_effect": self.target_increase_effect.to_dict(),
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
    consistency: ConsistencyRule | None = None
    daily_loss_limit: RuleValue = field(default_factory=lambda: unknown("daily_loss_limit"))
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
    def key(self) -> str:
        return f"{self.firm_id}/{self.program_id}/{self.account_size}@v{self.ruleset_version}"

    @property
    def all_rules(self) -> dict[str, RuleValue]:
        return {
            "initial_balance": self.initial_balance,
            "profit_target": self.profit_target,
            "daily_loss_limit": self.daily_loss_limit,
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
            "max_minis": self.position_limits.max_minis,
            "max_micros": self.position_limits.max_micros,
            "automation_stance": self.automation.stance,
            "api_available": self.automation.api_available,
        }

    @property
    def unresolved_rules(self) -> list[str]:
        return sorted(name for name, rule in self.all_rules.items() if not rule.is_verified)

    @property
    def fully_verified(self) -> bool:
        return not self.unresolved_rules

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
            "account_size": self.account_size,
            "ruleset_version": self.ruleset_version,
            "effective_from": self.effective_from.isoformat(),
            "source_url": self.source_url,
            "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at else None,
            "verification_status": self.verification_status.value,
            "fully_verified": self.fully_verified,
            "unresolved_rules": self.unresolved_rules,
            "max_loss_limit": self.max_loss_limit.to_dict(),
            "position_limits": self.position_limits.to_dict(),
            "automation": self.automation.to_dict(),
            "consistency": self.consistency.to_dict() if self.consistency else None,
            "rules": {name: rule.to_dict() for name, rule in self.all_rules.items()},
            "notes": self.notes,
        }


class PropFirmRegistry:
    """Versioned catalogue of firm profiles. Published versions are immutable."""

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

    def get(self, key: str) -> FirmProfile | None:
        return self._profiles.get(key)

    def by_firm(self, firm_id: str) -> list[FirmProfile]:
        return sorted((p for p in self._profiles.values() if p.firm_id == firm_id),
                      key=lambda p: p.account_size)

    def firms(self) -> list[str]:
        return sorted({p.firm_id for p in self._profiles.values()})

    def all(self) -> list[FirmProfile]:
        return sorted(self._profiles.values(), key=lambda p: p.key)

    def adjudication_ready(self) -> list[FirmProfile]:
        return [p for p in self._profiles.values() if p.fully_verified]

    def __len__(self) -> int:
        return len(self._profiles)


REGISTRY = PropFirmRegistry()
