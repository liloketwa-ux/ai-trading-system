"""The one door the real ICT research campaign has to come through.

The point of this module is *scope*. It blocks exactly one thing -- executing
``ICT-FAMILY-V1`` for a market claim -- and nothing else. Unit tests run.
Synthetic calibration runs. The backtester, the walk-forward machinery, the
robustness matrix and the prop-firm simulator all run, because a gate that
stopped those would be turned off within a week and would then be protecting
nothing.

What it stops is the one action whose output would be a statement about NQ.

    >>> require_real_data_approved(None)
    Traceback (most recent call last):
    ai_trading.project.gate.RealDataPending: REAL_DATA_PENDING: ...

The message is fixed text, not a formatted summary, so it is greppable and so
the same words appear in the CLI, the exception and the documentation.
"""

from __future__ import annotations

from ..research.ict_family import ICT_FAMILY_V1
from ..research.ict_freeze import (
    FAMILY_LABEL,
    NEXT_PERMITTED_ACTION,
    FamilyStatus,
    family_status,
)

__all__ = ["RealDataPending", "REAL_DATA_PENDING_MESSAGE",
           "require_real_data_approved", "may_run_ict_family",
           "run_ict_family_campaign"]


#: The exact wording, in one place.
REAL_DATA_PENDING_MESSAGE = (
    "REAL_DATA_PENDING:\n"
    "ICT-FAMILY-V1 is frozen and cannot be evaluated until a dataset reaches "
    "MARKET_CLAIM_ALLOWED."
)


class RealDataPending(RuntimeError):
    """The ICT campaign was invoked without an approved real dataset."""


def _detail(dataset) -> str:
    """Why the gate is closed, appended below the fixed message."""
    if dataset is None:
        return ("No dataset was supplied. The required external action is "
                "PROVIDE_APPROVED_REAL_NQ_DATA -- see "
                "docs/real-data-handoff.md.")
    try:
        ICT_FAMILY_V1.require_market_claim_allowed(dataset)
    except PermissionError as error:
        return str(error)
    return ""


def may_run_ict_family(dataset=None) -> bool:
    """Whether the campaign may run. No side effects, raises nothing."""
    return family_status(dataset=dataset) is FamilyStatus.APPROVED_FOR_REAL_DATA


def require_real_data_approved(dataset=None) -> None:
    """Refuse the campaign unless the dataset has cleared MARKET_CLAIM_ALLOWED.

    Call this from anything whose output would be a claim about a real market.
    Do not call it from unit tests, calibration, or synthetic experiments --
    those are legitimate work on synthetic data and blocking them would only
    teach people to route around the gate.
    """
    if may_run_ict_family(dataset):
        return
    detail = _detail(dataset)
    raise RealDataPending(
        f"{REAL_DATA_PENDING_MESSAGE}\n\n{detail}\n\n"
        f"{FAMILY_LABEL} stays frozen either way. The next permitted research "
        f"action is: {NEXT_PERMITTED_ACTION}."
    )


def run_ict_family_campaign(dataset=None):
    """The campaign entry point. Currently reaches the gate and stops there.

    It exists now, before there is anything behind it, so that the refusal has
    an obvious address. Someone looking for "how do I run the ICT research"
    finds this function and gets the reason, instead of finding nothing and
    assembling an ungated run out of the pieces.
    """
    require_real_data_approved(dataset)
    raise NotImplementedError(          # pragma: no cover - unreachable today
        "The gate opened but no campaign runner is wired up yet. Executing "
        f"{FAMILY_LABEL} is the next permitted research action and needs its "
        "own implementation phase."
    )
