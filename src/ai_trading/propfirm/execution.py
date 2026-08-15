"""Firm execution provider interface, topology constraint, and compliance gate.

**No live execution is enabled.** This module defines the contract a local
execution agent would implement and the gates it must pass. There is no working
TopstepX client, and adding one is a separate decision.

**The topology constraint is architectural, not advisory.** The operator states
Topstep's API documentation requires trading activity to originate from the
user's personal device and prohibits VPS/VPN/remote-server use. A cloud-hosted
executor would therefore breach the firm's terms regardless of how well it
traded, and a rules breach voids an account no matter what the equity curve
says. So the research system and the executor are separate processes with a
one-way boundary:

    cloud research  --signals-->  LOCAL execution agent  -->  firm API

:class:`ExecutionTopology` refuses to mark a cloud deployment eligible when the
profile prohibits remote servers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from typing import Any

from .profiles import FirmProfile, ProhibitedPractice
from .verification import UnverifiedRuleError

__all__ = [
    "DeploymentLocation", "ExecutionTopology", "ComplianceGate",
    "ComplianceViolation", "FirmExecutionProvider", "LiveExecutionPrerequisites",
    "PracticeDeclaration",
]


class DeploymentLocation(str, Enum):
    LOCAL_DEVICE = "local_device"
    VPS = "vps"
    CLOUD = "cloud"
    UNKNOWN = "unknown"

    @property
    def is_remote(self) -> bool:
        return self in (DeploymentLocation.VPS, DeploymentLocation.CLOUD,
                        DeploymentLocation.UNKNOWN)


class ComplianceViolation(RuntimeError):
    """A prohibited practice or topology was detected."""


@dataclass(frozen=True)
class ExecutionTopology:
    """Where the executor runs, checked against the firm's policy."""

    location: DeploymentLocation
    description: str = ""

    def check(self, profile: FirmProfile) -> tuple[bool, str]:
        """Whether this deployment is permitted. Fails closed when unverified."""
        prohibits = profile.automation.prohibits_vps
        if prohibits.is_unknown:
            return False, (
                f"{profile.firm_id}: VPS/remote-server policy is unverified; "
                "cannot certify any deployment topology"
            )
        if prohibits.get() and self.location.is_remote:
            return False, (
                f"{profile.firm_id} prohibits remote-server execution; "
                f"deployment is {self.location.value}. Execution must originate "
                "from the user's personal device."
            )
        if self.location is DeploymentLocation.UNKNOWN:
            return False, "deployment location unknown; refusing to certify"
        return True, f"{self.location.value} deployment permitted"


@dataclass(frozen=True)
class PracticeDeclaration:
    """A strategy's declared behaviour, checked against prohibited practices."""

    uses_stale_feed: bool = False
    trades_outside_bbo: bool = False
    places_and_cancels_rapidly: bool = False
    hedges_across_accounts: bool = False
    exploits_sim_fills: bool = False
    max_size_into_scheduled_news: bool = False
    high_frequency: bool = False
    latency_advantage_tech: bool = False

    def violations(self) -> list[ProhibitedPractice]:
        mapping = [
            (self.uses_stale_feed, ProhibitedPractice.STALE_FEED_EXPLOITATION),
            (self.trades_outside_bbo, ProhibitedPractice.TRADING_OUTSIDE_BBO),
            (self.places_and_cancels_rapidly, ProhibitedPractice.SPOOFING),
            (self.hedges_across_accounts, ProhibitedPractice.CROSS_ACCOUNT_HEDGING),
            (self.exploits_sim_fills, ProhibitedPractice.UNREALISTIC_SIM_FILLS),
            (self.max_size_into_scheduled_news, ProhibitedPractice.MAX_SIZE_INTO_NEWS),
            (self.high_frequency, ProhibitedPractice.PROHIBITED_HFT),
            (self.latency_advantage_tech, ProhibitedPractice.UNFAIR_ADVANTAGE_TECH),
        ]
        return [practice for declared, practice in mapping if declared]


@dataclass
class ComplianceGate:
    """Configurable gate every order must clear before reaching a firm API."""

    profile: FirmProfile
    topology: ExecutionTopology
    declaration: PracticeDeclaration = field(default_factory=PracticeDeclaration)
    enabled: bool = True

    def evaluate(self) -> tuple[bool, list[str]]:
        """Whether execution may proceed, and why not."""
        blockers: list[str] = []

        permitted, reason = self.topology.check(self.profile)
        if not permitted:
            blockers.append(reason)

        stance = self.profile.automation.stance
        if stance.is_unknown:
            blockers.append(f"{self.profile.firm_id}: automation stance unverified")
        elif self.profile.automation.permits_full_automation is False:
            blockers.append(
                f"{self.profile.firm_id} does not permit full automation "
                f"(stance={stance.get()})"
            )

        declared = self.declaration.violations()
        prohibited = set(self.profile.automation.prohibited_practices)
        for practice in declared:
            if practice in prohibited:
                blockers.append(f"prohibited practice declared: {practice.value}")

        if not self.profile.fully_verified:
            blockers.append(
                f"{len(self.profile.unresolved_rules)} firm rule(s) unverified; "
                "cannot certify compliance"
            )

        return not blockers, blockers

    def require(self) -> None:
        """Raise unless execution may proceed."""
        permitted, blockers = self.evaluate()
        if not permitted:
            raise ComplianceViolation(
                f"execution refused for {self.profile.key}: " + "; ".join(blockers)
            )


class FirmExecutionProvider(ABC):
    """Contract a local execution agent implements (e.g. TopstepX/ProjectX).

    No implementation ships. Credentials are never embedded: an implementation
    receives them from the local environment or a secret manager at construction.
    """

    provider_name: str = "abstract"
    supports_rest: bool = False
    supports_websocket: bool = False

    @abstractmethod
    def account_state(self) -> dict[str, Any]: ...

    @abstractmethod
    def positions(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def orders(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def market_data(self, symbol: str) -> dict[str, Any]: ...

    @abstractmethod
    def submit_order(self, order: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def flatten_all(self) -> list[dict[str, Any]]:
        """Close every position and cancel every working order.

        Required for the forced-flat rule: a firm that flattens at a fixed time
        expects the account to be flat, and leaving a resting order behind is a
        breach even when no position remains.
        """


@dataclass
class LiveExecutionPrerequisites:
    """Every gate that must clear before live execution is even considered."""

    strategy_has_out_of_sample_evidence: bool = False
    firm_rules_verified: bool = False
    compliance_policy_verified: bool = False
    api_behaviour_tested: bool = False
    execution_reliability_tested: bool = False
    local_execution_operational: bool = False

    @property
    def outstanding(self) -> list[str]:
        return [name for name, done in self.__dict__.items() if not done]

    @property
    def ready(self) -> bool:
        return not self.outstanding

    def require(self) -> None:
        if not self.ready:
            raise ComplianceViolation(
                "live execution prerequisites outstanding: "
                + ", ".join(self.outstanding)
            )

    def to_dict(self) -> dict:
        return {"ready": self.ready, "outstanding": self.outstanding,
                **{k: v for k, v in self.__dict__.items()}}
