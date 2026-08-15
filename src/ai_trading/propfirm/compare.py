"""Cross-firm comparison.

Firms are compared side by side and never collapsed into one score. A strategy
that clears Topstep's target while breaching Apex's tighter drawdown is two
different facts, and averaging them produces a number describing no firm that
exists.

Every result carries a ``decidable`` flag. Where a rule is unverified the
comparison reports the measurement and withholds the verdict, rather than
issuing a pass/fail that reads as authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .profiles import FirmProfile
from .verification import UnverifiedRuleError

__all__ = ["FirmOutcome", "StrategyRun", "compare_strategy_across_firms"]


@dataclass(frozen=True)
class StrategyRun:
    """Summary of a simulated run, expressed in firm-relevant terms."""

    strategy_id: str
    instrument: str
    starting_balance: float
    final_balance: float
    peak_equity: float
    max_drawdown_currency: float
    max_daily_loss_currency: float
    trading_days: int
    best_day_profit: float
    total_profit: float
    max_position_minis: int = 0
    max_position_micros: int = 0
    daily_losses: tuple[float, ...] = ()
    is_automated: bool = True

    @property
    def net_profit(self) -> float:
        return self.final_balance - self.starting_balance


@dataclass
class FirmOutcome:
    """How one run fares against one firm. Verdicts withheld where unverified."""

    firm_key: str
    firm_id: str
    account_size: int
    ruleset_version: str
    verification_status: str
    decidable: bool
    passed: bool | None = None
    failure_reasons: list[str] = field(default_factory=list)
    undecidable_reasons: list[str] = field(default_factory=list)
    profit_target_distance: float | None = None
    max_drawdown_currency: float = 0.0
    daily_loss_violations: int = 0
    consistency: dict | None = None
    trading_day_result: dict | None = None
    position_limit_violations: int = 0
    automation_compatible: bool | None = None

    def to_dict(self) -> dict:
        return {
            "firm_key": self.firm_key,
            "firm_id": self.firm_id,
            "account_size": self.account_size,
            "ruleset_version": self.ruleset_version,
            "verification_status": self.verification_status,
            "decidable": self.decidable,
            "passed": self.passed,
            "failure_reasons": self.failure_reasons,
            "undecidable_reasons": self.undecidable_reasons,
            "profit_target_distance": self.profit_target_distance,
            "max_drawdown_currency": self.max_drawdown_currency,
            "daily_loss_violations": self.daily_loss_violations,
            "consistency": self.consistency,
            "trading_day_result": self.trading_day_result,
            "position_limit_violations": self.position_limit_violations,
            "automation_compatible": self.automation_compatible,
        }


def _evaluate(run: StrategyRun, profile: FirmProfile) -> FirmOutcome:
    outcome = FirmOutcome(
        firm_key=profile.key, firm_id=profile.firm_id,
        account_size=profile.account_size,
        ruleset_version=profile.ruleset_version,
        verification_status=profile.verification_status.value,
        decidable=True,
        max_drawdown_currency=run.max_drawdown_currency,
    )

    # -- profit target -----------------------------------------------------
    target = profile.profit_target
    if target.is_unknown or target.get() is None:
        outcome.undecidable_reasons.append("profit_target unverified")
    else:
        outcome.profit_target_distance = target.get() - run.net_profit
        if not target.is_verified:
            outcome.undecidable_reasons.append(
                f"profit_target is {target.status.value}, not official"
            )

    # -- maximum loss limit ------------------------------------------------
    mll = profile.max_loss_limit
    if not mll.fully_verified:
        outcome.undecidable_reasons.append(
            f"max_loss_limit unresolved ({', '.join(mll.unresolved[:3])})"
        )
    else:
        threshold = mll.threshold.require("drawdown adjudication")
        if run.max_drawdown_currency >= threshold:
            outcome.failure_reasons.append(
                f"max drawdown {run.max_drawdown_currency:,.0f} reached the "
                f"{threshold:,.0f} limit"
            )

    # -- daily loss limit --------------------------------------------------
    dll = profile.daily_loss_limit
    if dll.is_unknown:
        outcome.undecidable_reasons.append("daily_loss_limit unverified")
    else:
        limit = dll.get()
        if limit:
            outcome.daily_loss_violations = sum(1 for loss in run.daily_losses
                                                if abs(loss) >= limit)
            if outcome.daily_loss_violations and dll.is_verified:
                outcome.failure_reasons.append(
                    f"{outcome.daily_loss_violations} daily-loss breach(es)"
                )

    # -- consistency -------------------------------------------------------
    if profile.consistency is not None:
        result = profile.consistency.evaluate(run.best_day_profit, run.total_profit)
        outcome.consistency = result
        if result["passes"] is None:
            outcome.undecidable_reasons.append(f"consistency: {result['reason']}")
        elif not result["passes"]:
            outcome.failure_reasons.append(f"consistency: {result['reason']}")

    # -- trading days ------------------------------------------------------
    minimum = profile.min_trading_days
    if minimum.is_unknown:
        outcome.undecidable_reasons.append("min_trading_days unverified")
    else:
        required = minimum.get(0)
        satisfied = run.trading_days >= required
        outcome.trading_day_result = {
            "trading_days": run.trading_days, "required": required,
            "satisfied": satisfied, "verified": minimum.is_verified,
        }
        if not satisfied and minimum.is_verified:
            outcome.failure_reasons.append(
                f"{run.trading_days} trading days, {required} required"
            )

    # -- position limits ---------------------------------------------------
    within = profile.position_limits.within_limit(run.max_position_minis,
                                                  run.max_position_micros)
    if within is None:
        outcome.undecidable_reasons.append("position limits unverified")
    elif not within:
        outcome.position_limit_violations = 1
        if profile.position_limits.max_minis.is_verified:
            outcome.failure_reasons.append("position exceeded the firm contract limit")

    # -- automation --------------------------------------------------------
    stance = profile.automation.stance
    if stance.is_unknown:
        outcome.undecidable_reasons.append("automation policy unverified")
    else:
        permits = profile.automation.permits_full_automation
        outcome.automation_compatible = (not run.is_automated) or bool(permits)
        if run.is_automated and permits is False:
            outcome.failure_reasons.append(
                f"firm automation stance is {stance.get()}; this run is automated"
            )

    # -- verdict -----------------------------------------------------------
    if outcome.undecidable_reasons:
        outcome.decidable = False
        outcome.passed = None
    else:
        outcome.passed = not outcome.failure_reasons

    return outcome


def compare_strategy_across_firms(
    strategy_run: StrategyRun, firm_rulesets: list[FirmProfile]
) -> dict[str, FirmOutcome]:
    """Evaluate one run against several firms, reported separately.

    Returns a mapping of firm key to outcome. Deliberately not aggregated: a
    single cross-firm score would describe no firm that exists.
    """
    return {profile.key: _evaluate(strategy_run, profile) for profile in firm_rulesets}
