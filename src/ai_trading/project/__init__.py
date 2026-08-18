"""Project-level status, gating and integrity auditing.

Nothing in this package implements a strategy, a feature or a rule. It answers
three questions about the system as a whole: what state is it in
(:mod:`.status`), what is it not allowed to do yet (:mod:`.gate`), and are the
integrity properties it claims still true (:mod:`.audit`).
"""

from .audit import AuditCheck, AuditReport, Severity, run_integrity_audit
from .gate import (
    REAL_DATA_PENDING_MESSAGE,
    RealDataPending,
    may_run_ict_family,
    require_real_data_approved,
    run_ict_family_campaign,
)
from .status import (
    PRIMARY_PROP_TARGET,
    ExternalAction,
    LiveExecutionStatus,
    MarketClaimStatus,
    ProjectPhase,
    ProjectStatus,
    RealDataStatus,
    TargetUnresolved,
    collect_test_count,
    resolve_status,
)

__all__ = [
    "AuditCheck", "AuditReport", "Severity", "run_integrity_audit",
    "REAL_DATA_PENDING_MESSAGE", "RealDataPending", "may_run_ict_family",
    "require_real_data_approved", "run_ict_family_campaign",
    "PRIMARY_PROP_TARGET", "ExternalAction", "LiveExecutionStatus",
    "MarketClaimStatus", "ProjectPhase", "ProjectStatus", "RealDataStatus",
    "TargetUnresolved", "collect_test_count", "resolve_status",
]
