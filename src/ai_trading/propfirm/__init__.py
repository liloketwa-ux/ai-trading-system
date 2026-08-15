"""Prop-firm rulesets, comparison, and compliance gating (Phase 8).

Importing this package publishes every firm profile into :data:`REGISTRY`, so
the registry is never silently empty because a submodule went unimported.

**No live execution.** No firm API client ships, and every rule in this package
is unverified -- firm documentation was unreachable when it was written.
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
from .firms import PRIMARY_AUTOMATION_TARGET, TOPSTEP_PRACTICES
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
    PropFirmRegistry,
)
from .verification import (
    STALENESS_WINDOW,
    RuleValue,
    SourceRef,
    UnverifiedRuleError,
    VerificationStatus,
    unknown,
    user_supplied,
    verified,
)

__all__ = [
    "PRIMARY_AUTOMATION_TARGET", "REGISTRY", "STALENESS_WINDOW",
    "TOPSTEP_PRACTICES", "AutomationPolicy", "AutomationStance",
    "ComplianceGate", "ComplianceViolation", "ConsistencyRule",
    "DeploymentLocation", "DrawdownBasis", "DrawdownTiming",
    "ExecutionTopology", "FirmExecutionProvider", "FirmOutcome", "FirmProfile",
    "LiveExecutionPrerequisites", "MaxLossLimit", "PositionLimits",
    "PracticeDeclaration", "ProhibitedPractice", "PropFirmRegistry", "RuleValue",
    "SourceRef", "StrategyRun", "UnverifiedRuleError", "VerificationStatus",
    "compare_strategy_across_firms", "unknown", "user_supplied", "verified",
]
