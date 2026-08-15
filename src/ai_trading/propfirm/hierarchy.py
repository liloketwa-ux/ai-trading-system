"""The ruleset address space: firm -> program -> stage -> account size -> version.

A prop-firm rule is never a property of the firm. "Topstep's drawdown is
$2,000" is meaningless until four more things are fixed: which program, which
stage of it, what account size, and as of when. Flattening any one of those
produces a lookup that silently returns the wrong ruleset -- the evaluation
rules applied to a funded account, or last quarter's numbers applied to this
quarter's trader.

So the key is the full path, and there is no way to ask for a ruleset without
naming all five components. Version resolution is the one exception: omit it and
you get the latest ruleset effective on a given date, which is the only
defensible default because rules change and old ones do not apply retroactively.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

__all__ = [
    "Stage", "RulesetKey", "VerificationLevel", "FieldProvenance",
    "PayoutPolicy", "XFAParameters", "Capability", "CAPABILITY_FIELDS",
]


class Stage(str, Enum):
    """Where an account sits in a firm's progression.

    The stage is part of the key because the same program applies different
    rules at different stages -- a profit target that exists in evaluation and
    not once funded, a consistency guideline that gates payouts rather than
    passes.
    """

    EVALUATION = "evaluation"
    FUNDED_SIM = "funded_sim"
    LIVE_FUNDED = "live_funded"

    @property
    def has_profit_target(self) -> bool:
        """Only evaluation stages have something to pass."""
        return self is Stage.EVALUATION


@dataclass(frozen=True, order=True)
class RulesetKey:
    """Fully qualified address of one ruleset."""

    firm_id: str
    program_id: str
    stage: Stage
    account_size: int
    ruleset_version: str

    def __str__(self) -> str:
        return (f"{self.firm_id}/{self.program_id}/{self.stage.value}"
                f"/{self.account_size}@v{self.ruleset_version}")

    @property
    def unversioned(self) -> tuple[str, str, Stage, int]:
        """The address without the version, for "latest" lookups."""
        return (self.firm_id, self.program_id, self.stage, self.account_size)

    def to_dict(self) -> dict:
        return {
            "firm_id": self.firm_id,
            "program_id": self.program_id,
            "stage": self.stage.value,
            "account_size": self.account_size,
            "ruleset_version": self.ruleset_version,
            "key": str(self),
        }


class VerificationLevel(str, Enum):
    """How much of a ruleset can back a compliance claim.

    The middle level is the important one. A profile that is 90% verified is not
    "verified with caveats" -- it is a profile that will adjudicate confidently
    right up until it touches the unverified rule. ``PARTIALLY_VERIFIED`` names
    that state so callers can decide per-capability rather than per-profile.
    """

    ADJUDICATION_READY = "adjudication_ready"
    PARTIALLY_VERIFIED = "partially_verified"
    UNVERIFIED = "unverified"

    @property
    def may_adjudicate(self) -> bool:
        return self is VerificationLevel.ADJUDICATION_READY


class Capability(str, Enum):
    """A thing the simulator can do, gated on the fields it actually needs.

    Readiness is per-capability rather than per-profile because the alternative
    is wrong in both directions. Requiring every field would refuse to track a
    loss limit whose every input is sourced merely because the payout cadence is
    not; requiring none would let an unverified session boundary silently decide
    when an end-of-day trailing threshold advances.
    """

    LOSS_LIMIT_TRACKING = "loss_limit_tracking"
    POSITION_LIMIT_ENFORCEMENT = "position_limit_enforcement"
    PROFIT_TARGET_ADJUDICATION = "profit_target_adjudication"
    CONSISTENCY_EVALUATION = "consistency_evaluation"
    SESSION_BOUNDARY_ENFORCEMENT = "session_boundary_enforcement"
    AUTOMATION_COMPLIANCE = "automation_compliance"
    FULL_ADJUDICATION = "full_adjudication"


#: Fields each capability depends on. ``FULL_ADJUDICATION`` is the union of the
#: rest, computed in :mod:`profiles` rather than duplicated here.
CAPABILITY_FIELDS: dict[Capability, tuple[str, ...]] = {
    Capability.LOSS_LIMIT_TRACKING: (
        "initial_balance", "mll_threshold", "mll_mode", "mll_timing",
        "mll_basis", "mll_drawdown_type", "mll_calculation_method",
        "mll_locks_at", "daily_loss_limit_mode",
    ),
    Capability.POSITION_LIMIT_ENFORCEMENT: ("max_minis", "max_micros"),
    Capability.PROFIT_TARGET_ADJUDICATION: (
        "profit_target", "min_trading_days", "initial_balance",
    ),
    Capability.CONSISTENCY_EVALUATION: (
        "max_best_day_fraction", "consistency_applies_to",
    ),
    Capability.SESSION_BOUNDARY_ENFORCEMENT: (
        "trading_day_start", "trading_day_end", "forced_flat_time",
        "session_reopen", "overnight_allowed",
    ),
    Capability.AUTOMATION_COMPLIANCE: ("automation_stance", "api_available"),
}


@dataclass(frozen=True)
class FieldProvenance:
    """Per-field record of where one rule value came from.

    Emitted for every rule in a profile, verified or not. An audit that only
    lists the verified fields cannot answer the question it exists to answer.
    """

    field_name: str
    value: object
    status: str
    source_url: str
    source_title: str
    retrieved_at: datetime | None
    verified_at: date | None
    verification_method: str
    ruleset_version: str

    def to_dict(self) -> dict:
        return {
            "field_name": self.field_name,
            "value": self.value.value if isinstance(self.value, Enum) else self.value,
            "status": self.status,
            "source_url": self.source_url,
            "source_title": self.source_title,
            "retrieved_at": self.retrieved_at.isoformat() if self.retrieved_at else None,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "verification_method": self.verification_method,
            "ruleset_version": self.ruleset_version,
        }


@dataclass(frozen=True)
class XFAParameters:
    """Express Funded Account parameters, stored separately from eligibility.

    Payout mechanics do not decide whether an account survives, and mixing them
    into the loss-limit model is how a payout cap ends up being enforced as a
    drawdown. They are kept in their own object so that a simulation can ignore
    them entirely and still be correct about pass/fail.
    """

    first_payout_cap: object          # RuleValue
    subsequent_payout_cap: object     # RuleValue
    min_trading_days_for_payout: object = None
    profit_split: object = None
    note: str = ""

    def to_dict(self) -> dict:
        out = {
            "first_payout_cap": self.first_payout_cap.to_dict(),
            "subsequent_payout_cap": self.subsequent_payout_cap.to_dict(),
            "note": self.note,
        }
        if self.min_trading_days_for_payout is not None:
            out["min_trading_days_for_payout"] = self.min_trading_days_for_payout.to_dict()
        if self.profit_split is not None:
            out["profit_split"] = self.profit_split.to_dict()
        return out


@dataclass(frozen=True)
class PayoutPolicy:
    """Withdrawal mechanics. Never consulted by the eligibility simulator."""

    xfa: XFAParameters | None = None
    cadence: object = None            # RuleValue
    buffer_requirement: object = None  # RuleValue

    def to_dict(self) -> dict:
        return {
            "xfa": self.xfa.to_dict() if self.xfa else None,
            "cadence": self.cadence.to_dict() if self.cadence is not None else None,
            "buffer_requirement": (
                self.buffer_requirement.to_dict()
                if self.buffer_requirement is not None else None
            ),
        }
