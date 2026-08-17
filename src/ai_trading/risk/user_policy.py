"""User risk objectives, kept strictly separate from firm requirements.

A trader's daily target and a prop firm's loss limit are different kinds of
object and must never share a field. The firm's rules are external facts,
verified against documentation, and breaching one ends the account. The user's
target is a *preference*: it can be changed, ignored, or missed entirely with
no consequence beyond a slower day. Merging them produces a system that treats
a personal goal with the authority of a contractual limit, which is precisely
backwards.

Two safeguards carry most of the weight here.

**A target is an objective, not a command.** :func:`resolve_risk` does not
accept daily-target progress as an input. Not "ignores it" -- cannot see it.
There is no argument to pass, so no code path exists in which being behind
target increases size, loosens a threshold, or manufactures a trade. When no
valid setup occurs, ``NO TRADE`` is correct at any P&L.

**The ceiling is not the default.** ``max_risk_per_trade_pct`` is an absolute
user cap, and ``risk_per_trade`` is chosen beneath it from strategy evidence
and account capacity. A system that defaults to its own ceiling has no
headroom left to express that one setup is better than another.

The hierarchy, highest authority first::

    firm hard limits
        -> system risk limits
        -> user maximum risk
        -> strategy risk budget
        -> individual trade risk

Resolution is a minimum across every layer, so a lower layer can only ever
tighten. The 2% user ceiling cannot loosen a firm limit of 0.5%, and nothing
in the API offers a way to try.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

__all__ = [
    "DailyTargetMode", "TargetReachedAction", "StrategyQualityTier",
    "TargetSemantics", "RiskEligibility",
    "RiskLayer", "RiskConstraint", "ResolvedRisk", "UserRiskPolicy",
    "DailyTargetState", "TargetFeasibility", "FeasibilityVerdict",
    "DailyMetrics", "resolve_risk", "UserPolicyError",
]


class UserPolicyError(RuntimeError):
    """A user policy was configured or used incoherently."""


class TargetSemantics(str, Enum):
    """What a daily target *means*. There is exactly one legal value.

    The enum has one member because the alternative reading -- that an unmet
    target obliges the system to trade -- is not a configuration option this
    system offers. Naming the correct semantics explicitly, and leaving no
    symbol for ``MANDATORY_TRADE_TARGET``, means a caller cannot express the
    wrong interpretation even by mistake: there is nothing to pass.
    """

    USER_DESIRED_DAILY_RETURN = "user_desired_daily_return"

    @property
    def obliges_trading(self) -> bool:
        """Always ``False``. A desired return is never a command to trade."""
        return False


class RiskEligibility(str, Enum):
    """What a strategy's research evidence entitles it to.

    Separate from :class:`StrategyQualityTier` because the tier is a *finding*
    about research and the eligibility is a *permission* granted on the back of
    it. Keeping them apart means a tier can be re-defined without silently
    re-authorising capital.
    """

    NO_LIVE_RISK = "no_live_risk"
    PAPER_ONLY = "paper_only"
    LIMITED_RISK_ELIGIBLE = "limited_risk_eligible"
    FULL_RISK_POLICY_ELIGIBLE = "full_risk_policy_eligible"

    @property
    def permits_live_capital(self) -> bool:
        return self in (RiskEligibility.LIMITED_RISK_ELIGIBLE,
                        RiskEligibility.FULL_RISK_POLICY_ELIGIBLE)

    @property
    def permits_paper(self) -> bool:
        return self is not RiskEligibility.NO_LIVE_RISK


class DailyTargetMode(str, Enum):
    """Where the user's daily target applies."""

    #: Tracked and reported; nothing is gated on it.
    OPTIONAL = "optional"
    #: Enforced in evaluation simulation, where modelling the user's own
    #: stopping behaviour changes the pass-rate estimate materially.
    ENFORCED_FOR_EVALUATION_SIM = "enforced_for_evaluation_sim"
    #: Not tracked at all.
    INACTIVE = "inactive"

    @property
    def is_tracked(self) -> bool:
        return self is not DailyTargetMode.INACTIVE

    @property
    def gates_new_trades(self) -> bool:
        return self is DailyTargetMode.ENFORCED_FOR_EVALUATION_SIM


class TargetReachedAction(str, Enum):
    """What happens once the daily target is met.

    ``STOP_NEW_TRADES`` is the default because the alternative -- continuing to
    take risk purely to enlarge a day that already met its objective -- adds
    variance for no stated purpose. It is configurable because some firm
    programs require continued activity, and a user policy must never override
    a firm requirement.
    """

    STOP_NEW_TRADES = "stop_new_trades"
    CONTINUE_TRADING = "continue_trading"
    REDUCE_RISK = "reduce_risk"


class StrategyQualityTier(str, Enum):
    """How much research evidence stands behind a strategy.

    The mapping from tier to risk is deliberately expressed as a *multiplier of
    the user ceiling* rather than as fixed percentages. Assigning "0.75% for a
    robust strategy" would be a number with no derivation; a fraction of the
    user's own stated ceiling at least inherits its justification.

    The two failing tiers return zero, not a small number. A strategy that
    failed out of sample does not get a reduced allocation; it gets none.
    """

    OUT_OF_SAMPLE_FAILURE = "out_of_sample_failure"
    INSUFFICIENT_SAMPLE = "insufficient_sample"
    PROMISING = "promising"
    SURVIVES_ROBUSTNESS = "survives_robustness"
    ROBUST_CANDIDATE = "robust_candidate"

    @property
    def rank(self) -> int:
        return _TIER_RANK[self]

    @property
    def permits_live_risk(self) -> bool:
        """Whether any live capital may be allocated at all."""
        return self.rank >= StrategyQualityTier.SURVIVES_ROBUSTNESS.rank

    @property
    def permits_paper_only(self) -> bool:
        return self is StrategyQualityTier.PROMISING

    def ceiling_fraction(self) -> float:
        """Share of the user's maximum risk this tier may use."""
        return _TIER_FRACTION[self]

    def budget_pct(self, user_max_risk_pct: float) -> float:
        return user_max_risk_pct * self.ceiling_fraction()

    @property
    def eligibility(self) -> "RiskEligibility":
        """The permission this tier grants.

        ``FULL_RISK_POLICY_ELIGIBLE`` means eligible for the *policy* ceiling,
        not entitled to it. The resolved risk is still the minimum across firm,
        system, user, strategy and trade layers, and this permission cannot
        raise any of them.
        """
        return _TIER_ELIGIBILITY[self]


_TIER_RANK = {
    StrategyQualityTier.OUT_OF_SAMPLE_FAILURE: 0,
    StrategyQualityTier.INSUFFICIENT_SAMPLE: 1,
    StrategyQualityTier.PROMISING: 2,
    StrategyQualityTier.SURVIVES_ROBUSTNESS: 3,
    StrategyQualityTier.ROBUST_CANDIDATE: 4,
}

#: Fractions, not percentages. See StrategyQualityTier's docstring.
_TIER_FRACTION = {
    StrategyQualityTier.OUT_OF_SAMPLE_FAILURE: 0.0,
    StrategyQualityTier.INSUFFICIENT_SAMPLE: 0.0,
    StrategyQualityTier.PROMISING: 0.0,          # research and paper only
    StrategyQualityTier.SURVIVES_ROBUSTNESS: 0.25,
    StrategyQualityTier.ROBUST_CANDIDATE: 1.0,
}


_TIER_ELIGIBILITY = {
    StrategyQualityTier.OUT_OF_SAMPLE_FAILURE: RiskEligibility.NO_LIVE_RISK,
    StrategyQualityTier.INSUFFICIENT_SAMPLE: RiskEligibility.NO_LIVE_RISK,
    StrategyQualityTier.PROMISING: RiskEligibility.PAPER_ONLY,
    StrategyQualityTier.SURVIVES_ROBUSTNESS: RiskEligibility.LIMITED_RISK_ELIGIBLE,
    StrategyQualityTier.ROBUST_CANDIDATE: RiskEligibility.FULL_RISK_POLICY_ELIGIBLE,
}


class RiskLayer(str, Enum):
    """Authority ranking. Lower layers may tighten, never loosen."""

    FIRM_HARD_LIMIT = "firm_hard_limit"
    SYSTEM_RISK_LIMIT = "system_risk_limit"
    USER_MAX_RISK = "user_max_risk"
    STRATEGY_BUDGET = "strategy_budget"
    TRADE_RISK = "trade_risk"

    @property
    def authority(self) -> int:
        """Lower number means higher authority."""
        return _LAYER_AUTHORITY[self]


_LAYER_AUTHORITY = {
    RiskLayer.FIRM_HARD_LIMIT: 0,
    RiskLayer.SYSTEM_RISK_LIMIT: 1,
    RiskLayer.USER_MAX_RISK: 2,
    RiskLayer.STRATEGY_BUDGET: 3,
    RiskLayer.TRADE_RISK: 4,
}


@dataclass(frozen=True)
class RiskConstraint:
    """One limit, expressed as a percentage of equity, with its provenance."""

    layer: RiskLayer
    name: str
    limit_pct: float
    reason: str = ""

    def __post_init__(self) -> None:
        if self.limit_pct < 0:
            raise UserPolicyError(
                f"{self.name}: a risk limit cannot be negative"
            )
        if not self.name:
            raise UserPolicyError("a risk constraint must be named")

    def to_dict(self) -> dict:
        return {"layer": self.layer.value, "name": self.name,
                "limit_pct": self.limit_pct, "reason": self.reason}


@dataclass(frozen=True)
class ResolvedRisk:
    """The final permitted risk, and which constraint produced it."""

    allowed_pct: float
    binding: RiskConstraint
    constraints: tuple[RiskConstraint, ...]

    @property
    def is_zero(self) -> bool:
        return self.allowed_pct <= 0.0

    @property
    def binding_layer(self) -> RiskLayer:
        return self.binding.layer

    def constraint(self, name: str) -> RiskConstraint | None:
        for item in self.constraints:
            if item.name == name:
                return item
        return None

    def explain(self) -> str:
        return (f"{self.allowed_pct:.4f}% allowed; bound by "
                f"{self.binding.name} ({self.binding.layer.value})"
                + (f" -- {self.binding.reason}" if self.binding.reason else ""))

    def to_dict(self) -> dict:
        return {
            "allowed_pct": self.allowed_pct,
            "binding": self.binding.to_dict(),
            "binding_layer": self.binding_layer.value,
            "constraints": [c.to_dict() for c in self.constraints],
            "explanation": self.explain(),
        }


def resolve_risk(constraints: Sequence[RiskConstraint]) -> ResolvedRisk:
    """Take the minimum across every layer.

    Note what this function does **not** take: daily-target progress, P&L,
    time of day, or how far the account is from its objective. There is no
    parameter through which being behind target could increase the result, so
    no such code path can be written against this API.

    Ties break toward the higher-authority layer, so an explanation attributes
    a shared limit to the firm rather than to a coincidentally equal user
    setting.
    """
    if not constraints:
        raise UserPolicyError(
            "risk resolution needs at least one constraint; an unconstrained "
            "size is not a default, it is a missing limit"
        )
    ordered = sorted(constraints,
                     key=lambda c: (c.limit_pct, c.layer.authority))
    binding = ordered[0]
    return ResolvedRisk(binding.limit_pct, binding, tuple(constraints))


@dataclass(frozen=True)
class UserRiskPolicy:
    """The user's own objectives and ceilings. Never a firm rule.

    Defaults follow the stated preferences: a 10% daily target enforced in
    evaluation simulation, and a 2% absolute per-trade ceiling. The *working*
    risk per trade is deliberately much lower and is resolved per trade.
    """

    daily_target_pct: float = 10.0
    daily_target_mode: DailyTargetMode = DailyTargetMode.ENFORCED_FOR_EVALUATION_SIM
    on_target_reached: TargetReachedAction = TargetReachedAction.STOP_NEW_TRADES
    #: Absolute user ceiling. Not the default trade risk.
    max_risk_per_trade_pct: float = 2.0
    #: Starting point beneath the ceiling, before strategy and account
    #: constraints tighten it further.
    baseline_risk_per_trade_pct: float = 0.25
    label: str = "user_policy"

    def __post_init__(self) -> None:
        if self.max_risk_per_trade_pct <= 0:
            raise UserPolicyError("max_risk_per_trade_pct must be positive")
        if self.daily_target_pct < 0:
            raise UserPolicyError("daily_target_pct cannot be negative")
        if self.baseline_risk_per_trade_pct <= 0:
            raise UserPolicyError("baseline_risk_per_trade_pct must be positive")
        if self.baseline_risk_per_trade_pct > self.max_risk_per_trade_pct:
            raise UserPolicyError(
                f"baseline risk {self.baseline_risk_per_trade_pct}% exceeds the "
                f"user ceiling {self.max_risk_per_trade_pct}%; the ceiling is a cap, "
                "not a target"
            )

    @property
    def is_user_policy(self) -> bool:
        """Always true. Present so a caller can assert what it is holding."""
        return True

    @property
    def target_semantics(self) -> TargetSemantics:
        """A desired return, never a mandatory one. Fixed, not configurable."""
        return TargetSemantics.USER_DESIRED_DAILY_RETURN

    @property
    def target_obliges_trading(self) -> bool:
        """Always ``False``. An unmet target never requires a position."""
        return self.target_semantics.obliges_trading

    def daily_target_amount(self, starting_daily_equity: float) -> float:
        """Target in currency, from the day's *starting* equity.

        Starting equity, not current: a target computed from a rising balance
        recedes as the day goes well, which no trader means by "10% a day".
        """
        if starting_daily_equity <= 0:
            raise UserPolicyError("starting daily equity must be positive")
        return starting_daily_equity * self.daily_target_pct / 100.0

    def ceiling_constraint(self) -> RiskConstraint:
        return RiskConstraint(
            RiskLayer.USER_MAX_RISK, "user_max_risk_per_trade",
            self.max_risk_per_trade_pct,
            "absolute user ceiling; never loosens a firm or system limit",
        )

    def to_dict(self) -> dict:
        return {
            "kind": "user_policy",
            "daily_target_pct": self.daily_target_pct,
            "target_semantics": self.target_semantics.value,
            "target_obliges_trading": self.target_obliges_trading,
            "daily_target_mode": self.daily_target_mode.value,
            "on_target_reached": self.on_target_reached.value,
            "max_risk_per_trade_pct": self.max_risk_per_trade_pct,
            "baseline_risk_per_trade_pct": self.baseline_risk_per_trade_pct,
            "label": self.label,
        }


@dataclass
class DailyTargetState:
    """Progress toward the user's daily target.

    Tracks and reports. It has no authority to open a position and no method
    that returns "you should trade now" -- the only decision it expresses is
    whether *new* trades are permitted once the target is already met.
    """

    policy: UserRiskPolicy
    starting_daily_equity: float
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    trades_taken: int = 0
    #: True on days where the strategy produced no valid setup. Not a failure.
    no_valid_setup: bool = False

    def __post_init__(self) -> None:
        if self.starting_daily_equity <= 0:
            raise UserPolicyError("starting daily equity must be positive")

    # -- tracking ---------------------------------------------------------
    @property
    def daily_target_amount(self) -> float:
        return self.policy.daily_target_amount(self.starting_daily_equity)

    @property
    def daily_target_progress(self) -> float:
        """Combined realized and unrealized P&L for the day."""
        return self.realized_pnl + self.unrealized_pnl

    @property
    def daily_target_progress_pct(self) -> float:
        target = self.daily_target_amount
        if target <= 0:
            return 0.0
        return self.daily_target_progress / target * 100.0

    @property
    def daily_target_remaining(self) -> float:
        return max(0.0, self.daily_target_amount - self.daily_target_progress)

    @property
    def daily_target_reached(self) -> bool:
        if not self.policy.daily_target_mode.is_tracked:
            return False
        return self.daily_target_progress >= self.daily_target_amount

    @property
    def return_pct(self) -> float:
        return self.daily_target_progress / self.starting_daily_equity * 100.0

    # -- the one decision it makes ----------------------------------------
    def may_open_new_trade(self) -> tuple[bool, str]:
        """Whether the *target* permits another position.

        Only ever returns ``False`` because the target has been **met**. Being
        behind target is never a reason to allow anything extra, and this
        method has no branch that could express it.
        """
        if not self.policy.daily_target_mode.gates_new_trades:
            return (True, f"daily target mode is "
                          f"{self.policy.daily_target_mode.value}; it gates nothing")
        if not self.daily_target_reached:
            return (True, "target not yet reached; this permits trading and does "
                          "not require it")
        if self.policy.on_target_reached is TargetReachedAction.CONTINUE_TRADING:
            return (True, "DAILY_TARGET_REACHED; policy is to continue trading")
        if self.policy.on_target_reached is TargetReachedAction.REDUCE_RISK:
            return (True, "DAILY_TARGET_REACHED; policy is to continue at reduced risk")
        return (False, "DAILY_TARGET_REACHED; policy is STOP_NEW_TRADES. Additional "
                       "risk would only enlarge a day that already met its objective.")

    @property
    def status(self) -> str:
        return "DAILY_TARGET_REACHED" if self.daily_target_reached else "IN_PROGRESS"

    def to_dict(self) -> dict:
        allowed, reason = self.may_open_new_trade()
        return {
            "starting_daily_equity": self.starting_daily_equity,
            "daily_target_amount": self.daily_target_amount,
            "daily_target_progress": self.daily_target_progress,
            "daily_target_progress_pct": self.daily_target_progress_pct,
            "daily_target_remaining": self.daily_target_remaining,
            "daily_target_reached": self.daily_target_reached,
            "status": self.status,
            "return_pct": self.return_pct,
            "trades_taken": self.trades_taken,
            "no_valid_setup": self.no_valid_setup,
            "may_open_new_trade": allowed,
            "reason": reason,
        }


class FeasibilityVerdict(str, Enum):
    TARGET_PLAUSIBLE = "target_plausible"
    TARGET_MAY_BE_INFEASIBLE = "target_may_be_infeasible"
    INSUFFICIENT_HISTORY = "insufficient_history"

    @property
    def is_warning(self) -> bool:
        return self is FeasibilityVerdict.TARGET_MAY_BE_INFEASIBLE


@dataclass(frozen=True)
class TargetFeasibility:
    """Whether a daily target is reachable given observed daily returns.

    Informational only. Nothing consumes this to change position size, and the
    correct response to ``TARGET_MAY_BE_INFEASIBLE`` is to revise the target or
    accept missing it -- never to trade larger.
    """

    verdict: FeasibilityVerdict
    target_pct: float
    p95_daily_return_pct: float | None
    max_daily_return_pct: float | None
    days_observed: int
    days_target_reached: int
    note: str = ""

    @property
    def hit_rate(self) -> float | None:
        if self.days_observed == 0:
            return None
        return self.days_target_reached / self.days_observed

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value, "target_pct": self.target_pct,
            "p95_daily_return_pct": self.p95_daily_return_pct,
            "max_daily_return_pct": self.max_daily_return_pct,
            "days_observed": self.days_observed,
            "days_target_reached": self.days_target_reached,
            "hit_rate": self.hit_rate, "note": self.note,
        }


def assess_target_feasibility(policy: UserRiskPolicy,
                              daily_returns_pct: Sequence[float],
                              *, min_days: int = 30) -> TargetFeasibility:
    """Compare a target against the strategy's own daily distribution.

    Flags when the target sits beyond the 95th percentile of observed daily
    returns -- a target that only the best day in twenty can reach is not an
    objective, it is a description of an outlier. Purely informational.
    """
    clean = [float(r) for r in daily_returns_pct]
    if len(clean) < min_days:
        return TargetFeasibility(
            FeasibilityVerdict.INSUFFICIENT_HISTORY, policy.daily_target_pct,
            None, None, len(clean), 0,
            f"{len(clean)} days observed, {min_days} required before judging "
            "feasibility",
        )

    ordered = sorted(clean)
    rank = max(1, min(len(ordered), int(-(-0.95 * len(ordered) // 1))))
    p95 = ordered[rank - 1]
    reached = sum(1 for r in clean if r >= policy.daily_target_pct)

    if policy.daily_target_pct > p95:
        return TargetFeasibility(
            FeasibilityVerdict.TARGET_MAY_BE_INFEASIBLE, policy.daily_target_pct,
            p95, max(ordered), len(clean), reached,
            f"target {policy.daily_target_pct:.1f}% exceeds the 95th percentile "
            f"daily return of {p95:.2f}%. Informational only -- do not increase "
            "position size to close the gap.",
        )
    return TargetFeasibility(
        FeasibilityVerdict.TARGET_PLAUSIBLE, policy.daily_target_pct,
        p95, max(ordered), len(clean), reached,
        f"target {policy.daily_target_pct:.1f}% sits within the observed "
        f"distribution (p95 {p95:.2f}%)",
    )


@dataclass(frozen=True)
class DailyMetrics:
    """Research metrics for a run, reported together on purpose.

    Target-hit rate is listed alongside drawdown, tail risk and losing streaks
    because optimising the first alone selects for strategies that reach 10%
    often and lose the account occasionally.
    """

    days: int
    percentage_of_days_target_reached: float
    median_daily_return_pct: float
    mean_daily_return_pct: float
    maximum_daily_return_pct: float
    maximum_daily_loss_pct: float
    days_with_no_trade: int
    days_with_overtrade_attempts: int
    expectancy_pct: float
    max_drawdown_pct: float
    daily_return_volatility_pct: float
    longest_losing_streak: int
    tail_loss_p95_pct: float

    @property
    def daily_target_hit_rate(self) -> float:
        return self.percentage_of_days_target_reached / 100.0

    def to_dict(self) -> dict:
        return {
            "days": self.days,
            "percentage_of_days_target_reached": self.percentage_of_days_target_reached,
            "daily_target_hit_rate": self.daily_target_hit_rate,
            "median_daily_return_pct": self.median_daily_return_pct,
            "mean_daily_return_pct": self.mean_daily_return_pct,
            "maximum_daily_return_pct": self.maximum_daily_return_pct,
            "maximum_daily_loss_pct": self.maximum_daily_loss_pct,
            "days_with_no_trade": self.days_with_no_trade,
            "days_with_overtrade_attempts": self.days_with_overtrade_attempts,
            "expectancy_pct": self.expectancy_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "daily_return_volatility_pct": self.daily_return_volatility_pct,
            "longest_losing_streak": self.longest_losing_streak,
            "tail_loss_p95_pct": self.tail_loss_p95_pct,
        }


def compute_daily_metrics(daily_returns_pct: Sequence[float],
                          target_pct: float, *, days_with_no_trade: int = 0,
                          days_with_overtrade_attempts: int = 0) -> DailyMetrics:
    """Summarise a run's daily returns without privileging the target."""
    from statistics import median, pstdev

    values = [float(r) for r in daily_returns_pct]
    if not values:
        raise UserPolicyError("no daily returns to summarise")

    reached = sum(1 for r in values if r >= target_pct)
    equity, peak, drawdown = 1.0, 1.0, 0.0
    for ret in values:
        equity *= (1.0 + ret / 100.0)
        peak = max(peak, equity)
        drawdown = max(drawdown, 1.0 - equity / peak)

    streak = longest = 0
    for ret in values:
        streak = streak + 1 if ret < 0 else 0
        longest = max(longest, streak)

    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-0.05 * len(ordered) // 1))))

    return DailyMetrics(
        days=len(values),
        percentage_of_days_target_reached=reached / len(values) * 100.0,
        median_daily_return_pct=float(median(values)),
        mean_daily_return_pct=sum(values) / len(values),
        maximum_daily_return_pct=max(values),
        maximum_daily_loss_pct=min(values),
        days_with_no_trade=days_with_no_trade,
        days_with_overtrade_attempts=days_with_overtrade_attempts,
        expectancy_pct=sum(values) / len(values),
        max_drawdown_pct=drawdown * 100.0,
        daily_return_volatility_pct=float(pstdev(values)) if len(values) > 1 else 0.0,
        longest_losing_streak=longest,
        tail_loss_p95_pct=ordered[rank - 1],
    )
