"""Risk management: sizing, stops, leverage, drawdown, and exposure limits.

See design-doc section 7.
"""

from .manager import RiskDecision, RiskLimits, RiskManager

from .user_policy import (
    DailyMetrics,
    DailyTargetMode,
    DailyTargetState,
    FeasibilityVerdict,
    ResolvedRisk,
    RiskConstraint,
    RiskLayer,
    StrategyQualityTier,
    TargetFeasibility,
    TargetReachedAction,
    UserPolicyError,
    UserRiskPolicy,
    assess_target_feasibility,
    compute_daily_metrics,
    resolve_risk,
)

__all__ = [
    "RiskDecision", "RiskLimits", "RiskManager",
    "DailyMetrics", "DailyTargetMode", "DailyTargetState", "FeasibilityVerdict",
    "ResolvedRisk", "RiskConstraint", "RiskLayer", "StrategyQualityTier",
    "TargetFeasibility", "TargetReachedAction", "UserPolicyError",
    "UserRiskPolicy", "assess_target_feasibility", "compute_daily_metrics",
    "resolve_risk",
]
