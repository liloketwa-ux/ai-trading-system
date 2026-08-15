"""Prop-firm rulesets, comparison, and compliance gating (Phase 8).

Importing this package publishes every firm profile into :data:`REGISTRY`, so
the registry is never silently empty because a submodule went unimported.

**No live execution.** No firm API client ships. Rules covered by the
2026-08-15 official source review carry ``OFFICIAL_SOURCE_VERIFIED`` provenance;
everything else still refuses to back a compliance claim.
"""

from . import firms as _firms  # noqa: F401 - imported for its registration side effect
from .compare import FirmOutcome, StrategyRun, compare_strategy_across_firms
from .execution import (
    ComplianceGate,
    ComplianceViolation,
    DeploymentLocation,
    ExecutionTopology,
    FirmExecutionProvider,
    LiveExecutionPrerequisites,
    PracticeDeclaration,
)
from .firms import (
    PRIMARY_AUTOMATION_TARGET,
    RULESET_VERSION,
    SOURCES,
    TOPSTEP_PRACTICES,
    VERIFIED_AT,
)
from .hierarchy import (
    CAPABILITY_FIELDS,
    Capability,
    FieldProvenance,
    PayoutPolicy,
    RulesetKey,
    Stage,
    VerificationLevel,
    XFAParameters,
)
from .limits import (
    AccountLimitMonitor,
    DailyLossLimitMode,
    DailyLossLimitTracker,
    EligibilityOutcome,
    LimitAction,
    LimitEvent,
    LimitEventType,
    MaximumLossLimitTracker,
    MLLMode,
)
from .profiles import (
    REGISTRY,
    AutomationPolicy,
    AutomationStance,
    ConsistencyResult,
    ConsistencyRule,
    DrawdownBasis,
    DrawdownTiming,
    FirmProfile,
    MaxLossLimit,
    PositionLimits,
    ProhibitedPractice,
    PropFirmRegistry,
)
from .verification import (
    STALENESS_WINDOW,
    RuleValue,
    SourceRef,
    UnverifiedRuleError,
    VerificationMethod,
    VerificationStatus,
    not_applicable,
    official_verified,
    unknown,
    user_supplied,
    verified,
)

__all__ = [
    "CAPABILITY_FIELDS", "PRIMARY_AUTOMATION_TARGET", "REGISTRY",
    "RULESET_VERSION", "SOURCES", "STALENESS_WINDOW", "TOPSTEP_PRACTICES",
    "VERIFIED_AT", "AccountLimitMonitor", "AutomationPolicy", "AutomationStance",
    "Capability", "ComplianceGate", "ComplianceViolation", "ConsistencyResult",
    "ConsistencyRule", "DailyLossLimitMode", "DailyLossLimitTracker",
    "DeploymentLocation", "DrawdownBasis", "DrawdownTiming",
    "EligibilityOutcome", "ExecutionTopology", "FieldProvenance",
    "FirmExecutionProvider", "FirmOutcome", "FirmProfile", "LimitAction",
    "LimitEvent", "LimitEventType", "LiveExecutionPrerequisites", "MLLMode",
    "MaxLossLimit", "MaximumLossLimitTracker", "PayoutPolicy", "PositionLimits",
    "PracticeDeclaration", "ProhibitedPractice", "PropFirmRegistry",
    "RuleValue", "RulesetKey", "SourceRef", "Stage", "StrategyRun",
    "UnverifiedRuleError", "VerificationLevel", "VerificationMethod",
    "VerificationStatus", "XFAParameters", "compare_strategy_across_firms",
    "not_applicable", "official_verified", "unknown", "user_supplied",
    "verified",
]
