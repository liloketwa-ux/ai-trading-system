"""The permanent freeze declaration for ``ICT-FAMILY-V1``.

:mod:`.ict_family` *builds* the family. This module *asserts what the family
is*, using values written down as literals rather than computed. That
duplication is the entire mechanism: if anyone edits a window, a threshold, a
label, a decision event or a hypothesis statement, the built family stops
matching the pinned record and :func:`verify_frozen` fails. A regression test
calls it, so the edit cannot reach a commit quietly.

A lock alone would not do this. ``HypothesisFamily.lock()`` prevents mutation of
a *live object*; it does nothing about someone changing the source that
constructs it and re-importing. The fingerprint pinned here is the witness that
survives a source edit, because it lives outside the code that computes it.

**Status.** The family is ``REAL_DATA_PENDING`` and stays there until a dataset
reaches ``MARKET_CLAIM_ALLOWED``. Synthetic data cannot move it: results on
synthetic data describe the generator, and the calibration work that used them
was never evidence about NQ.

**Change control.** There is no path here that edits v1. A change creates
``ICT-FAMILY-V2`` through :class:`FamilySupersession`, which refuses a
declaration that reuses v1's fingerprint, protocol version or trial count, or
that omits provenance for the change.

Nothing in this module runs a hypothesis, produces a signal, or touches an
execution path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum

from ..storage.dataset import code_commit
from .ict_family import ICT_FAMILY_V1, HypothesisFamily

__all__ = [
    "FAMILY_LABEL", "FROZEN_FINGERPRINT", "FROZEN_TRIAL_COUNT",
    "FROZEN_HYPOTHESIS_FINGERPRINTS", "FROZEN_LABELS",
    "FROZEN_FEATURE_VERSIONS", "FROZEN_BASELINES", "FROZEN_PARAMETERS",
    "FROZEN_WINDOWS", "FROZEN_DECISION_EVENTS", "FROZEN_ON",
    "FamilyStatus", "FamilyDriftError", "ProhibitedActionError",
    "PROHIBITED_ACTIONS", "NEXT_PERMITTED_ACTION",
    "FamilySupersession", "SupersessionRefused",
    "verify_frozen", "family_status", "require_action_permitted",
    "freeze_record",
]


# =========================================================================
# The pinned record
# =========================================================================

#: The name the freeze is declared under.
FAMILY_LABEL = "ICT-FAMILY-V1"

#: Frozen 2026-08-17. Every value below is a literal, deliberately not derived
#: from the family object -- a record that recomputes itself from the thing it
#: is meant to police cannot detect a change to it.
FROZEN_ON = date(2026, 8, 17)

FROZEN_FINGERPRINT = "b3ebb0af7f01b137"
FROZEN_PROTOCOL_VERSION = "research-protocol-v1"
FROZEN_FAMILY_ID = "ict-objective-family-v1"
FROZEN_VERSION = "v1"
FROZEN_HYPOTHESIS_COUNT = 6
FROZEN_LABEL_COUNT = 6
FROZEN_TRIAL_COUNT = 36
FROZEN_BASELINE_COMPARISON_COUNT = 24

FROZEN_HYPOTHESIS_FINGERPRINTS: dict[str, str] = {
    "ICT-LS-001": "f430a7cdd417e06c",
    "ICT-LS-002": "62c479a20cfe6d58",
    "ICT-FVG-001": "aa3e12b7cbeb1b2b",
    "ICT-COMBO-001": "6ee2b74f4600c477",
    "ICT-EQ-001": "73bb7dc87ba1f6eb",
    "ICT-COMBO-002": "8534861941be3739",
}

#: Which event fixes each hypothesis's decision time. Pinned because moving a
#: decision event later is the cheapest way to buy a free look at the future.
FROZEN_DECISION_EVENTS: dict[str, str] = {
    "ICT-LS-001": "liquidity_sweep:v1",
    "ICT-LS-002": "displacement:v1",
    "ICT-FVG-001": "fvg:v1",
    "ICT-COMBO-001": "fvg:v1",
    "ICT-EQ-001": "liquidity_sweep:v1",
    "ICT-COMBO-002": "fvg:v1",
}

FROZEN_PARENTS: dict[str, str | None] = {
    "ICT-LS-001": None,
    "ICT-LS-002": "ICT-LS-001",
    "ICT-FVG-001": "ICT-LS-001",
    "ICT-COMBO-001": "ICT-LS-002",
    "ICT-EQ-001": "ICT-LS-001",
    "ICT-COMBO-002": "ICT-COMBO-001",
}

FROZEN_LABELS: tuple[str, ...] = (
    "forward_return_5m", "forward_return_15m", "forward_return_30m",
    "forward_return_1h", "hit_1R_before_-1R", "hit_2R_before_-1R",
)

FROZEN_FEATURE_VERSIONS: tuple[str, ...] = (
    "displacement:v1", "equal_high:v1", "equal_low:v1", "fvg:v1",
    "liquidity_sweep:v1",
)

FROZEN_BASELINES: tuple[str, ...] = (
    "random", "hold_matched_random", "momentum", "mean_reversion",
)

FROZEN_PARAMETERS: dict[str, float | int] = {
    "fvg_min_size_atr": 0.2,
    "displacement_threshold_atr": 2.0,
    "equal_tolerance_atr": 0.1,
    "equal_min_separation_bars": 3,
    "atr_period": 14,
    "swing_left": 2,
    "swing_right": 2,
    "sweep_to_displacement_max_bars": 3,
    "displacement_to_fvg_max_bars": 2,
    "equality_lookback_bars": 50,
}

#: The temporal windows, pinned separately from the parameter block so a change
#: to either one is caught even if the other is edited to match.
FROZEN_WINDOWS: dict[str, int] = {
    "sweep_to_displacement": 3,
    "displacement_to_fvg": 2,
    "sweep_to_fvg": 5,
    "equality_to_sweep": 50,
}


# =========================================================================
# Status
# =========================================================================


class FamilyStatus(str, Enum):
    """Where the frozen family stands with respect to data.

    Note what is absent: there is no ``PARTIALLY_RUN``, no ``CALIBRATED`` and
    no ``SYNTHETIC_VALIDATED``. A status that could be advanced by synthetic
    data would let calibration masquerade as progress toward a market claim.
    """

    #: Declared, locked, and waiting. The only status v1 has ever held.
    REAL_DATA_PENDING = "real_data_pending"
    #: A dataset has reached MARKET_CLAIM_ALLOWED; the family may be run.
    APPROVED_FOR_REAL_DATA = "approved_for_real_data"
    #: A v2 has been declared. v1's results stay valid under v1.
    SUPERSEDED = "superseded"

    @property
    def permits_execution(self) -> bool:
        return self is FamilyStatus.APPROVED_FOR_REAL_DATA


class FamilyDriftError(RuntimeError):
    """The built family no longer matches the frozen record."""


class ProhibitedActionError(RuntimeError):
    """An action the freeze declaration forbids."""


#: Forbidden while v1 is frozen, with the reason each one is forbidden. Held as
#: data rather than prose so a caller can check membership instead of
#: remembering a document.
PROHIBITED_ACTIONS: dict[str, str] = {
    "run_on_synthetic_for_evidence":
        "results on synthetic data describe the generator, never NQ",
    "use_openmobius_cases_as_evidence":
        "1,282 unreviewed VLM extractions from video are terminology, not "
        "observations",
    "alter_definitions":
        "an edited definition is a different, un-pre-registered study",
    "tune_thresholds":
        "thresholds were fixed before data; tuning them after is selection",
    "tune_event_windows":
        "the windows are pre-registered values, not a search space",
    "add_features":
        "a seventh feature changes the family and its trial budget",
    "expand_family":
        "adding a hypothesis changes the count corrections are computed against",
    "reorder_family":
        "order is part of the declared record",
    "spend_holdout":
        "the holdout is spent once, after walk-forward and robustness pass",
    "create_trade_signals":
        "a hypothesis is a question; nothing here emits an order",
    "optimize_for_topstep_pass_probability":
        "fitting to an evaluation's pass criteria is optimising the scorer",
}

#: The one action the freeze permits next. Written out because "what may I do
#: now" should have a single answer rather than an inference from a list.
NEXT_PERMITTED_ACTION = (
    "run ICT-FAMILY-V1 against the first approved real NQ dataset under "
    "research-protocol-v1"
)


def require_action_permitted(action: str) -> None:
    """Refuse an action the freeze forbids.

    Unknown actions pass. This is a named-prohibition check, not an allowlist:
    pretending it authorises everything it has not heard of would be worse than
    saying plainly that it only knows these eleven.
    """
    reason = PROHIBITED_ACTIONS.get(action)
    if reason is not None:
        raise ProhibitedActionError(
            f"{action!r} is prohibited while {FAMILY_LABEL} is frozen: {reason}. "
            f"The next permitted research action is: {NEXT_PERMITTED_ACTION}."
        )


def family_status(*, dataset=None,
                  supersession: "FamilySupersession | None" = None
                  ) -> FamilyStatus:
    """Resolve the family's status from evidence, not from a stored flag.

    A status field that someone can assign is a status that will eventually be
    assigned optimistically. This recomputes from the dataset's own grade
    ladder every time it is asked.
    """
    if supersession is not None:
        return FamilyStatus.SUPERSEDED
    if dataset is None:
        return FamilyStatus.REAL_DATA_PENDING
    try:
        ICT_FAMILY_V1.require_market_claim_allowed(dataset)
    except PermissionError:
        return FamilyStatus.REAL_DATA_PENDING
    return FamilyStatus.APPROVED_FOR_REAL_DATA


# =========================================================================
# Verification
# =========================================================================


def _mismatch(what: str, expected, found) -> str:
    return f"  {what}: frozen {expected!r}, built {found!r}"


def verify_frozen(family: HypothesisFamily | None = None) -> str:
    """Check the built family against the pinned record. Returns the fingerprint.

    Raises :class:`FamilyDriftError` listing every difference. The message names
    the remedy -- declare a v2 -- because the failure is not a bug to be patched
    back to green, it is a change that needs a version.
    """
    family = ICT_FAMILY_V1 if family is None else family
    problems: list[str] = []

    if not family.is_locked:
        problems.append("  the family is not locked")
    if family.family_id != FROZEN_FAMILY_ID:
        problems.append(_mismatch("family_id", FROZEN_FAMILY_ID,
                                  family.family_id))
    if family.version != FROZEN_VERSION:
        problems.append(_mismatch("version", FROZEN_VERSION, family.version))
    if family.protocol_version != FROZEN_PROTOCOL_VERSION:
        problems.append(_mismatch("protocol_version", FROZEN_PROTOCOL_VERSION,
                                  family.protocol_version))

    built = {h.hypothesis_id: h for h in family.all()}
    if set(built) != set(FROZEN_HYPOTHESIS_FINGERPRINTS):
        added = sorted(set(built) - set(FROZEN_HYPOTHESIS_FINGERPRINTS))
        removed = sorted(set(FROZEN_HYPOTHESIS_FINGERPRINTS) - set(built))
        if added:
            problems.append(f"  hypotheses added: {added}")
        if removed:
            problems.append(f"  hypotheses removed: {removed}")

    for hid, expected in FROZEN_HYPOTHESIS_FINGERPRINTS.items():
        hypothesis = built.get(hid)
        if hypothesis is None:
            continue
        if hypothesis.fingerprint != expected:
            problems.append(_mismatch(f"{hid} fingerprint", expected,
                                      hypothesis.fingerprint))
        if hypothesis.decision_event != FROZEN_DECISION_EVENTS[hid]:
            problems.append(_mismatch(f"{hid} decision event",
                                      FROZEN_DECISION_EVENTS[hid],
                                      hypothesis.decision_event))
        if hypothesis.parent_id != FROZEN_PARENTS[hid]:
            problems.append(_mismatch(f"{hid} parent", FROZEN_PARENTS[hid],
                                      hypothesis.parent_id))

    if len(built) != FROZEN_HYPOTHESIS_COUNT:
        problems.append(_mismatch("hypothesis count", FROZEN_HYPOTHESIS_COUNT,
                                  len(built)))
    if family.trial_count != FROZEN_TRIAL_COUNT:
        problems.append(_mismatch("trial count", FROZEN_TRIAL_COUNT,
                                  family.trial_count))
    if family.baseline_comparison_count != FROZEN_BASELINE_COMPARISON_COUNT:
        problems.append(_mismatch("baseline comparisons",
                                  FROZEN_BASELINE_COMPARISON_COUNT,
                                  family.baseline_comparison_count))

    labels = tuple(label.name for label in family.label_family)
    if labels != FROZEN_LABELS:
        problems.append(_mismatch("labels", FROZEN_LABELS, labels))
    features = tuple(family.feature_versions())
    if features != FROZEN_FEATURE_VERSIONS:
        problems.append(_mismatch("feature versions", FROZEN_FEATURE_VERSIONS,
                                  features))
    if tuple(family.baselines) != FROZEN_BASELINES:
        problems.append(_mismatch("baselines", FROZEN_BASELINES,
                                  tuple(family.baselines)))
    if dict(family.fixed_parameters) != FROZEN_PARAMETERS:
        for key in sorted(set(FROZEN_PARAMETERS) | set(family.fixed_parameters)):
            expected = FROZEN_PARAMETERS.get(key)
            found = family.fixed_parameters.get(key)
            if expected != found:
                problems.append(_mismatch(f"parameter {key}", expected, found))

    declared = _declared_windows(family)
    for name, expected in FROZEN_WINDOWS.items():
        found = declared.get(name, set())
        if found != {expected}:
            problems.append(_mismatch(f"window {name}", expected,
                                      sorted(found) if found else None))

    # Last, so the itemised differences appear above it rather than being
    # summarised away by a single opaque hash mismatch.
    if family.fingerprint != FROZEN_FINGERPRINT:
        problems.append(_mismatch("fingerprint", FROZEN_FINGERPRINT,
                                  family.fingerprint))

    if problems:
        raise FamilyDriftError(
            f"{FAMILY_LABEL} no longer matches the record frozen on "
            f"{FROZEN_ON.isoformat()}:\n" + "\n".join(problems) +
            f"\n\n{FAMILY_LABEL} is permanently frozen. A change of any kind "
            "creates ICT-FAMILY-V2 via FamilySupersession, with a new "
            "fingerprint, a new protocol version, a recounted trial budget and "
            "provenance describing what changed and why. Editing v1 back into "
            "shape is the only wrong answer here."
        )
    return family.fingerprint


def _declared_windows(family: HypothesisFamily) -> dict[str, set[int]]:
    """Recover the declared windows from the links themselves.

    Read off the hypotheses rather than the parameter block, so a parameter
    edited without a corresponding link change (or the reverse) still shows up.

    Values are collected into a **set** rather than overwritten. The same window
    appears in several hypotheses -- sweep-to-displacement in three of them --
    and keeping only the last one seen would let a widening in one hypothesis
    hide behind an untouched declaration in another.
    """
    names = {
        ("liquidity_sweep:v1", "displacement:v1"): "sweep_to_displacement",
        ("displacement:v1", "fvg:v1"): "displacement_to_fvg",
        ("liquidity_sweep:v1", "fvg:v1"): "sweep_to_fvg",
        ("equal_high:v1", "liquidity_sweep:v1"): "equality_to_sweep",
        ("equal_low:v1", "liquidity_sweep:v1"): "equality_to_sweep",
    }
    found: dict[str, set[int]] = {}
    for hypothesis in family.all():
        for link in hypothesis.temporal_relationships:
            name = names.get((link.from_feature, link.to_feature))
            if name is not None:
                found.setdefault(name, set()).add(link.max_bars)
    return found


# =========================================================================
# Supersession -- the only route to a change
# =========================================================================


class SupersessionRefused(RuntimeError):
    """A v2 declaration did not meet the requirements for superseding v1."""


@dataclass(frozen=True)
class FamilySupersession:
    """A declaration that a new family version replaces the frozen one.

    Four requirements, all checked at construction: a new fingerprint, a new
    research protocol version, a recounted trial budget, and provenance saying
    what changed and why. The first three exist so a v2 cannot inherit v1's
    identity or its multiple-testing budget; the fourth exists because a change
    with no stated reason is indistinguishable, later, from a change made to
    suit a result.
    """

    family_id: str
    version: str
    fingerprint: str
    protocol_version: str
    trial_count: int
    supersedes_fingerprint: str
    change_summary: str
    reason: str
    declared_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))
    declared_commit: str = field(default_factory=code_commit)

    #: A summary shorter than this is not provenance, it is a shrug.
    MIN_SUMMARY_CHARS = 40

    def __post_init__(self) -> None:
        if self.supersedes_fingerprint != FROZEN_FINGERPRINT:
            raise SupersessionRefused(
                f"this supersession claims to replace "
                f"{self.supersedes_fingerprint!r}, but the frozen family is "
                f"{FROZEN_FINGERPRINT!r}. A supersession that does not name "
                "what it replaces cannot be audited."
            )
        if self.version == FROZEN_VERSION:
            raise SupersessionRefused(
                f"version {self.version!r} is the frozen version. A change "
                "creates a new version; it does not reuse v1."
            )
        if self.fingerprint == FROZEN_FINGERPRINT:
            raise SupersessionRefused(
                "the new family's fingerprint equals v1's, which means nothing "
                "actually changed. A supersession without a change is not a "
                "supersession."
            )
        if self.protocol_version == FROZEN_PROTOCOL_VERSION:
            raise SupersessionRefused(
                f"a v2 requires a new research protocol version; "
                f"{FROZEN_PROTOCOL_VERSION!r} is frozen and results recorded "
                "against it must stay readable under the rules they ran under."
            )
        if self.trial_count == FROZEN_TRIAL_COUNT:
            raise SupersessionRefused(
                f"the declared trial count is still {FROZEN_TRIAL_COUNT}. A v2 "
                "recounts its own trials; carrying v1's number forward would "
                "correct v2's results against v1's budget."
            )
        if self.trial_count <= 0:
            raise SupersessionRefused("trial count must be positive")
        for name in ("change_summary", "reason"):
            text = getattr(self, name).strip()
            if len(text) < self.MIN_SUMMARY_CHARS:
                raise SupersessionRefused(
                    f"{name} is {len(text)} characters; at least "
                    f"{self.MIN_SUMMARY_CHARS} are required. A version bump "
                    "with no stated provenance is indistinguishable later from "
                    "one made to suit a result."
                )

    @property
    def superseded_label(self) -> str:
        return FAMILY_LABEL

    def to_dict(self) -> dict:
        return {
            "family_id": self.family_id, "version": self.version,
            "fingerprint": self.fingerprint,
            "protocol_version": self.protocol_version,
            "trial_count": self.trial_count,
            "supersedes": self.superseded_label,
            "supersedes_fingerprint": self.supersedes_fingerprint,
            "change_summary": self.change_summary,
            "reason": self.reason,
            "declared_at": self.declared_at.isoformat(),
            "declared_commit": self.declared_commit,
        }


def freeze_record() -> dict:
    """The declaration, as data. Safe to serialise into a result header."""
    record = {
        "label": FAMILY_LABEL,
        "family_id": FROZEN_FAMILY_ID,
        "version": FROZEN_VERSION,
        "protocol_version": FROZEN_PROTOCOL_VERSION,
        "frozen_on": FROZEN_ON.isoformat(),
        "fingerprint": FROZEN_FINGERPRINT,
        "hypothesis_count": FROZEN_HYPOTHESIS_COUNT,
        "trial_count": FROZEN_TRIAL_COUNT,
        "baseline_comparison_count": FROZEN_BASELINE_COMPARISON_COUNT,
        "hypothesis_fingerprints": dict(FROZEN_HYPOTHESIS_FINGERPRINTS),
        "decision_events": dict(FROZEN_DECISION_EVENTS),
        "labels": list(FROZEN_LABELS),
        "feature_versions": list(FROZEN_FEATURE_VERSIONS),
        "baselines": list(FROZEN_BASELINES),
        "parameters": dict(FROZEN_PARAMETERS),
        "windows": dict(FROZEN_WINDOWS),
        "status": FamilyStatus.REAL_DATA_PENDING.value,
        "prohibited_actions": dict(PROHIBITED_ACTIONS),
        "next_permitted_action": NEXT_PERMITTED_ACTION,
    }
    record["record_checksum"] = hashlib.sha256(
        json.dumps(record, sort_keys=True).encode()).hexdigest()[:16]
    return record
