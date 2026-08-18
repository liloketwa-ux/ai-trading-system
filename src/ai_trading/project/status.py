"""Machine-readable project status, derived rather than declared.

Every field below is computed from something the system can actually check: the
frozen family's fingerprint is verified, not quoted; the provider registry is
inspected, not described; the live-execution verdict comes from enumerating
``Broker`` subclasses; the prop-firm target is resolved out of the rules
registry and fails loudly if it is not there.

The one field that cannot be derived from an import is the test count, and it
is derived by *running pytest's collector* rather than by writing a number
down. A status object with a hand-typed test count starts drifting the moment
someone adds a test, and a status report that can drift is worse than none --
it is a number people trust.

**Determinism.** There is no timestamp anywhere in the payload. Two runs at the
same commit with the same tree produce byte-identical output, which is what
makes the report diffable and what makes ``system:status`` usable in CI.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..propfirm import REGISTRY as PROPFIRM_REGISTRY, Stage
from ..research.ict_family import ICT_FAMILY_V1, PROTOCOL_VERSION
from ..research.ict_freeze import (
    FAMILY_LABEL,
    NEXT_PERMITTED_ACTION,
    FamilyStatus,
    family_status,
    verify_frozen,
)
from ..storage.dataset import code_commit

__all__ = [
    "ProjectPhase", "RealDataStatus", "MarketClaimStatus",
    "LiveExecutionStatus", "ExternalAction", "ProjectStatus",
    "resolve_status", "collect_test_count", "PRIMARY_PROP_TARGET",
    "TargetUnresolved",
]

#: The engineering target, named once. Resolved against the rules registry
#: below rather than trusted as a string.
PRIMARY_PROP_TARGET = "TOPSTEP_COMBINE_100K"
_TARGET_KEY = ("topstep", "trading_combine", Stage.EVALUATION, 100_000)


class TargetUnresolved(RuntimeError):
    """The declared prop-firm target is not in the rules registry."""


# =========================================================================
# The vocabulary
# =========================================================================


class ProjectPhase(str, Enum):
    """Where the project is, as a whole.

    Two members. There is no ``PARTIALLY_VALIDATED`` or ``PROMISING``: the
    project either has market evidence or it does not, and a middle rung would
    be filled in by whoever most wanted to believe it.
    """

    EVIDENCE_PENDING = "EVIDENCE_PENDING"
    EVIDENCE_AVAILABLE = "EVIDENCE_AVAILABLE"

    @property
    def permits_market_claims(self) -> bool:
        return self is ProjectPhase.EVIDENCE_AVAILABLE


class RealDataStatus(str, Enum):
    """Whether real market data has arrived, and how far it has got."""

    #: No provider is registered. Nothing can be fetched.
    NOT_AVAILABLE = "NOT_AVAILABLE"
    #: A provider exists; no dataset has cleared the grade ladder.
    PENDING_APPROVAL = "PENDING_APPROVAL"
    #: A REAL_MARKET dataset has reached MARKET_CLAIM_ALLOWED.
    APPROVED = "APPROVED"


class MarketClaimStatus(str, Enum):
    BLOCKED = "BLOCKED"
    ALLOWED = "ALLOWED"


class LiveExecutionStatus(str, Enum):
    DISABLED = "DISABLED"
    ENABLED = "ENABLED"


class ExternalAction(str, Enum):
    """What is needed from outside the system. At most one at a time."""

    PROVIDE_APPROVED_REAL_NQ_DATA = "PROVIDE_APPROVED_REAL_NQ_DATA"
    NONE_REQUIRED = "NONE_REQUIRED"


# =========================================================================
# Derivations
# =========================================================================


def _resolve_prop_target() -> str:
    """Confirm the declared target exists in the verified rules registry.

    A target string nothing checks is a label. This resolves it, so removing
    or renaming the Topstep 100K Combine ruleset breaks the status report
    rather than leaving it quietly describing an account that no longer exists.
    """
    firm, program, stage, size = _TARGET_KEY
    profile = PROPFIRM_REGISTRY.resolve(firm, program, stage, size)
    if profile is None:
        raise TargetUnresolved(
            f"{PRIMARY_PROP_TARGET} does not resolve to a ruleset: "
            f"{firm}/{program}/{stage.value}/{size}. The declared engineering "
            "target must exist in the rules registry."
        )
    return PRIMARY_PROP_TARGET


#: The package a shipped broker adapter would live in.
_PACKAGE = __name__.split(".")[0]


def _live_execution_status() -> tuple[LiveExecutionStatus, tuple[str, ...]]:
    """Enumerate broker implementations that **ship** in the package.

    Derived from the class hierarchy rather than from a config flag: a flag
    defaults to off, and a live adapter, once written, is one environment
    variable away from being used. The question worth asking is whether a live
    adapter exists at all.

    Subclasses defined outside ``ai_trading`` are excluded, because test doubles
    subclass ``Broker`` too and a status that flips to ENABLED when the
    execution tests happen to have been imported would report on the test suite
    rather than on the system.
    """
    from ..execution.broker import Broker, PaperBroker

    def descendants(cls) -> set:
        found = set(cls.__subclasses__())
        for child in tuple(found):
            found |= descendants(child)
        return found

    shipped = sorted(
        cls.__name__ for cls in descendants(Broker)
        if not issubclass(cls, PaperBroker)
        and cls.__module__.split(".")[0] == _PACKAGE
    )
    status = (LiveExecutionStatus.ENABLED if shipped
              else LiveExecutionStatus.DISABLED)
    return status, tuple(shipped)


def _real_data_status(dataset) -> tuple[RealDataStatus, tuple[str, ...]]:
    from ..history.cli import PROVIDER_REGISTRY

    providers = tuple(sorted(PROVIDER_REGISTRY))
    if family_status(dataset=dataset) is FamilyStatus.APPROVED_FOR_REAL_DATA:
        return RealDataStatus.APPROVED, providers
    if not providers:
        return RealDataStatus.NOT_AVAILABLE, providers
    return RealDataStatus.PENDING_APPROVAL, providers


_COLLECTED = re.compile(r"(\d+)\s*/?\s*\d*\s*tests? collected")


def collect_test_count(root: Path | None = None) -> int | None:
    """Count tests by running pytest's collector. ``None`` if it cannot run.

    Returns ``None`` rather than a remembered number when collection fails.
    A status field that falls back to a literal is a status field that lies
    exactly when something is broken.
    """
    root = Path(__file__).resolve().parents[3] if root is None else root
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            cwd=root, capture_output=True, text=True, timeout=300, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in reversed(result.stdout.splitlines()):
        match = _COLLECTED.search(line)
        if match:
            return int(match.group(1))
    return None


# =========================================================================
# The status object
# =========================================================================


@dataclass(frozen=True)
class ProjectStatus:
    """The project's state, as data. No timestamps, so it is diffable."""

    project_status: ProjectPhase
    research_protocol_version: str
    ict_family_version: str
    ict_family_fingerprint: str
    test_count: int | None
    real_data_status: RealDataStatus
    market_claim_status: MarketClaimStatus
    live_execution_status: LiveExecutionStatus
    primary_prop_target: str
    next_required_external_action: ExternalAction
    # -- supporting detail, all derived --------------------------------
    ict_family_label: str = FAMILY_LABEL
    ict_family_locked: bool = True
    declared_trials: int = 0
    registered_providers: tuple[str, ...] = ()
    live_broker_implementations: tuple[str, ...] = ()
    next_permitted_research_action: str = NEXT_PERMITTED_ACTION
    code_commit: str = ""

    @property
    def is_blocked(self) -> bool:
        return self.next_required_external_action is not \
            ExternalAction.NONE_REQUIRED

    def to_dict(self) -> dict:
        """The ten declared fields first, then supporting detail."""
        return {
            "project_status": self.project_status.value,
            "research_protocol_version": self.research_protocol_version,
            "ict_family_version": self.ict_family_version,
            "ict_family_fingerprint": self.ict_family_fingerprint,
            "test_count": self.test_count,
            "real_data_status": self.real_data_status.value,
            "market_claim_status": self.market_claim_status.value,
            "live_execution_status": self.live_execution_status.value,
            "primary_prop_target": self.primary_prop_target,
            "next_required_external_action":
                self.next_required_external_action.value,
            "ict_family_label": self.ict_family_label,
            "ict_family_locked": self.ict_family_locked,
            "declared_trials": self.declared_trials,
            "registered_providers": list(self.registered_providers),
            "live_broker_implementations":
                list(self.live_broker_implementations),
            "next_permitted_research_action":
                self.next_permitted_research_action,
            "code_commit": self.code_commit,
        }


def resolve_status(*, dataset=None, test_count: int | None = None,
                   include_test_count: bool = True) -> ProjectStatus:
    """Compute the project's status from the system itself.

    ``dataset`` is the candidate real dataset, if one exists. Passing ``None``
    -- the situation today -- is not a special case: the same derivations run
    and land on ``EVIDENCE_PENDING``.

    ``include_test_count=False`` skips the pytest subprocess, for callers that
    want the status without paying for collection.
    """
    fingerprint = verify_frozen()          # verifies, does not merely report
    live_status, live_impls = _live_execution_status()
    real_data, providers = _real_data_status(dataset)

    market_claim = (MarketClaimStatus.ALLOWED
                    if real_data is RealDataStatus.APPROVED
                    else MarketClaimStatus.BLOCKED)
    phase = (ProjectPhase.EVIDENCE_AVAILABLE
             if market_claim is MarketClaimStatus.ALLOWED
             else ProjectPhase.EVIDENCE_PENDING)
    external = (ExternalAction.NONE_REQUIRED
                if real_data is RealDataStatus.APPROVED
                else ExternalAction.PROVIDE_APPROVED_REAL_NQ_DATA)

    if test_count is None and include_test_count:
        test_count = collect_test_count()

    return ProjectStatus(
        project_status=phase,
        research_protocol_version=PROTOCOL_VERSION,
        ict_family_version=ICT_FAMILY_V1.version,
        ict_family_fingerprint=fingerprint,
        test_count=test_count,
        real_data_status=real_data,
        market_claim_status=market_claim,
        live_execution_status=live_status,
        primary_prop_target=_resolve_prop_target(),
        next_required_external_action=external,
        ict_family_locked=ICT_FAMILY_V1.is_locked,
        declared_trials=ICT_FAMILY_V1.trial_count,
        registered_providers=providers,
        live_broker_implementations=live_impls,
        code_commit=code_commit(),
    )
