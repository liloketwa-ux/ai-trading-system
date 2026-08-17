"""The ICT hypothesis family, frozen before any real data is observed.

A pre-registration is only worth something if it is written down *first* and
cannot be edited afterwards. That is the whole content of this module: six
hypotheses over the five implemented objective features, with explicit event
ordering, explicit windows, one decision event each, and a lock that makes the
family immutable.

Three design decisions carry the weight.

**Conjunctions are ordered, not sets.** "Sweep and displacement and FVG" is not
a hypothesis -- it does not say whether the displacement preceded the sweep or
followed it, and the two are different claims about the market. Every multi-
event hypothesis therefore declares a chain with a maximum bar gap between each
link, and the chain is validated at construction.

**Nothing after the decision time may contribute.** Each hypothesis names one
decision event, and its ``decision_time`` is the ``available_at`` of the last
contributing feature. A feature whose availability falls after that instant is
an *outcome*, not an input, and :meth:`Hypothesis.validate_contribution`
refuses it. This is where a conjunction most easily leaks: the FVG that
"confirms" a setup is available one bar after the displacement, and using it at
the displacement's timestamp buys a free look.

**The family is nested.** Each hypothesis names its parent, so the incremental
information added by each concept is measurable rather than only the final
conjunction being reported. A four-condition setup that performs identically to
its one-condition parent has told you the three extra conditions are decoration.

Windows are pre-registered, not searched. Changing one produces a new family
version and a new trial budget -- there is no sweep, and
:func:`build_family_v1` takes no parameters through which one could be
introduced.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

from ..storage.dataset import code_commit
from .costs import REALISTIC
from .labels import FORWARD_RETURNS, R_LABELS, LabelDefinition
from .sampling import SamplingPolicy

__all__ = [
    "EventRole", "FeatureEvent", "TemporalLink", "Hypothesis",
    "HypothesisFamily", "FamilyLockError", "TemporalOrderError",
    "build_family_v1", "ICT_FAMILY_V1", "FAMILY_ID", "PROTOCOL_VERSION",
    "LABEL_FAMILY", "BASELINES", "FIXED_PARAMETERS", "SWEEP_TO_DISPLACEMENT_MAX_BARS",
    "DISPLACEMENT_TO_FVG_MAX_BARS", "EQUALITY_LOOKBACK_BARS",
]

PROTOCOL_VERSION = "research-protocol-v1"
FAMILY_ID = "ict-objective-family-v1"

# -- pre-registered temporal windows -------------------------------------
#: Chosen once, before data. Not searched. Changing one is a new family.
SWEEP_TO_DISPLACEMENT_MAX_BARS = 3
DISPLACEMENT_TO_FVG_MAX_BARS = 2
#: How far back an equality level may sit and still count as context.
EQUALITY_LOOKBACK_BARS = 50

#: Feature parameters, fixed for v1. Reproduced here so the family record is
#: self-contained -- a reader should not have to open the feature module to
#: know what was tested.
FIXED_PARAMETERS = {
    "fvg_min_size_atr": 0.2,
    "displacement_threshold_atr": 2.0,
    "equal_tolerance_atr": 0.1,
    "equal_min_separation_bars": 3,
    "atr_period": 14,
    "swing_left": 2,
    "swing_right": 2,
    "sweep_to_displacement_max_bars": SWEEP_TO_DISPLACEMENT_MAX_BARS,
    "displacement_to_fvg_max_bars": DISPLACEMENT_TO_FVG_MAX_BARS,
    "equality_lookback_bars": EQUALITY_LOOKBACK_BARS,
}

#: The pre-registered outcome family. Existing definitions, unmodified.
LABEL_FAMILY: tuple[LabelDefinition, ...] = (
    FORWARD_RETURNS["5m"], FORWARD_RETURNS["15m"],
    FORWARD_RETURNS["30m"], FORWARD_RETURNS["1h"],
    R_LABELS["hit_1R"], R_LABELS["hit_2R"],
)

#: MAE and MFE are reported for every R-multiple label by compute_r_multiple;
#: they are outcome diagnostics rather than separate labels.
EXCURSION_DIAGNOSTICS = ("mae_r", "mfe_r")

#: Every hypothesis is compared against all four. Fixed order for comparability.
BASELINES = ("random", "hold_matched_random", "momentum", "mean_reversion")


class FamilyLockError(RuntimeError):
    """A locked family was modified."""


class TemporalOrderError(RuntimeError):
    """An event chain is not a valid ordering."""


class EventRole(str, Enum):
    """What an event does in a hypothesis.

    ``CONTEXT`` is the interesting one. An equal-high level is not a trigger --
    it exists before the sweep and conditions it, rather than sequencing after
    it. Modelling it as another link in the chain would impose an ordering the
    concept does not have.
    """

    TRIGGER = "trigger"
    SEQUENCE = "sequence"
    CONTEXT = "context"
    DECISION = "decision"

    @property
    def contributes_to_signal(self) -> bool:
        return True


@dataclass(frozen=True)
class FeatureEvent:
    """One feature's participation in a hypothesis."""

    feature_key: str            # e.g. "liquidity_sweep:v1"
    role: EventRole
    description: str = ""

    def __post_init__(self) -> None:
        if ":" not in self.feature_key:
            raise ValueError(
                f"{self.feature_key!r} must name a version, e.g. 'fvg:v1' -- an "
                "unversioned feature cannot be traced to the code that computed it"
            )

    @property
    def feature_name(self) -> str:
        return self.feature_key.split(":", 1)[0]

    @property
    def feature_version(self) -> str:
        return self.feature_key.split(":", 1)[1]

    def to_dict(self) -> dict:
        return {"feature_key": self.feature_key, "role": self.role.value,
                "description": self.description}


@dataclass(frozen=True)
class TemporalLink:
    """An ordered relationship between two events, with a maximum gap.

    ``max_bars`` is inclusive and ``min_bars`` defaults to 0, meaning the
    follower may occur on the same bar as the leader. Same-bar is permitted
    because a displacement bar can itself complete a sweep; what is never
    permitted is the follower preceding the leader.
    """

    from_feature: str
    to_feature: str
    max_bars: int
    min_bars: int = 0

    def __post_init__(self) -> None:
        if self.max_bars < 0 or self.min_bars < 0:
            raise TemporalOrderError("bar gaps cannot be negative")
        if self.max_bars < self.min_bars:
            raise TemporalOrderError(
                f"{self.from_feature} -> {self.to_feature}: max_bars "
                f"{self.max_bars} is below min_bars {self.min_bars}"
            )

    def permits(self, from_index: int, to_index: int) -> bool:
        """Whether an observed pair satisfies the ordering and the window."""
        gap = to_index - from_index
        return self.min_bars <= gap <= self.max_bars

    def describe(self) -> str:
        return (f"{self.from_feature} -> {self.to_feature} within "
                f"{self.max_bars} bar(s)")

    def to_dict(self) -> dict:
        return {"from": self.from_feature, "to": self.to_feature,
                "max_bars": self.max_bars, "min_bars": self.min_bars,
                "description": self.describe()}


@dataclass(frozen=True)
class Hypothesis:
    """One pre-registered question. Never a signal."""

    hypothesis_id: str
    statement: str
    parent_id: str | None
    events: tuple[FeatureEvent, ...]
    temporal_relationships: tuple[TemporalLink, ...]
    decision_event: str                     # feature key that fixes the decision
    label_versions: tuple[str, ...]
    cost_model: str
    sampling_policy_version: str
    protocol_version: str = PROTOCOL_VERSION
    creation_commit: str = ""

    def __post_init__(self) -> None:
        keys = {e.feature_key for e in self.events}
        if self.decision_event not in keys:
            raise TemporalOrderError(
                f"{self.hypothesis_id}: decision event {self.decision_event!r} "
                f"is not among the hypothesis's events {sorted(keys)}"
            )
        for link in self.temporal_relationships:
            for endpoint in (link.from_feature, link.to_feature):
                if endpoint not in keys:
                    raise TemporalOrderError(
                        f"{self.hypothesis_id}: link references {endpoint!r}, "
                        "which is not one of this hypothesis's events"
                    )
        sequenced = [e for e in self.events
                     if e.role in (EventRole.TRIGGER, EventRole.SEQUENCE)]
        if len(sequenced) > 1 and not self.temporal_relationships:
            raise TemporalOrderError(
                f"{self.hypothesis_id} has {len(sequenced)} sequenced events and "
                "no ordering. A conjunction without an ordering is an unordered "
                "set, and 'sweep then displacement' is a different claim from "
                "'displacement then sweep'."
            )

    @property
    def feature_versions(self) -> tuple[str, ...]:
        return tuple(sorted(e.feature_key for e in self.events))

    @property
    def conditions(self) -> tuple[str, ...]:
        """Human-readable conditions, in declared order."""
        return tuple(f"{e.role.value}: {e.feature_key}" for e in self.events)

    @property
    def condition_count(self) -> int:
        return len(self.events)

    @property
    def is_signal(self) -> bool:
        """Always ``False``. A hypothesis is a question."""
        return False

    @property
    def trial_count(self) -> int:
        """Tests this hypothesis contributes: one per label."""
        return len(self.label_versions)

    def decision_time(self, availabilities: dict[str, datetime]) -> datetime:
        """When this hypothesis becomes eligible for evaluation.

        The availability of the decision event. Every contributing feature must
        already be available by then, which :meth:`validate_contribution`
        checks.
        """
        if self.decision_event not in availabilities:
            raise TemporalOrderError(
                f"{self.hypothesis_id}: no availability supplied for the "
                f"decision event {self.decision_event!r}"
            )
        return availabilities[self.decision_event]

    def validate_contribution(self, availabilities: dict[str, datetime]) -> None:
        """Refuse any contributing feature available after the decision.

        The core temporal guard. A feature that becomes knowable after the
        decision instant is an outcome, and letting it condition the signal is
        look-ahead wearing a conjunction.
        """
        decision = self.decision_time(availabilities)
        late = {
            key: moment for key, moment in availabilities.items()
            if key in {e.feature_key for e in self.events} and moment > decision
        }
        if late:
            detail = ", ".join(
                f"{key} available {moment.isoformat()}"
                for key, moment in sorted(late.items()))
            raise TemporalOrderError(
                f"{self.hypothesis_id}: {detail}, after the decision time "
                f"{decision.isoformat()}. A feature available after the decision "
                "may only be an outcome label, never an input."
            )

    def validate_ordering(self, bar_indices: dict[str, int]) -> None:
        """Check an observed event set against the declared chain."""
        for link in self.temporal_relationships:
            if link.from_feature not in bar_indices or link.to_feature not in bar_indices:
                continue
            if not link.permits(bar_indices[link.from_feature],
                                bar_indices[link.to_feature]):
                raise TemporalOrderError(
                    f"{self.hypothesis_id}: {link.describe()} violated -- "
                    f"{link.from_feature} at bar {bar_indices[link.from_feature]}, "
                    f"{link.to_feature} at bar {bar_indices[link.to_feature]}"
                )

    @property
    def fingerprint(self) -> str:
        """Content hash. Any change makes a different hypothesis."""
        payload = {
            "id": self.hypothesis_id, "statement": self.statement,
            "parent": self.parent_id,
            "events": [e.to_dict() for e in self.events],
            "links": [l.to_dict() for l in self.temporal_relationships],
            "decision_event": self.decision_event,
            "labels": sorted(self.label_versions),
            "cost_model": self.cost_model,
            "sampling_policy_version": self.sampling_policy_version,
            "protocol_version": self.protocol_version,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "hypothesis_id": self.hypothesis_id, "statement": self.statement,
            "parent_id": self.parent_id,
            "feature_versions": list(self.feature_versions),
            "conditions": list(self.conditions),
            "temporal_relationships": [l.to_dict()
                                       for l in self.temporal_relationships],
            "decision_event": self.decision_event,
            "label_version": list(self.label_versions),
            "cost_model": self.cost_model,
            "sampling_policy": self.sampling_policy_version,
            "protocol_version": self.protocol_version,
            "creation_commit": self.creation_commit,
            "condition_count": self.condition_count,
            "trial_count": self.trial_count,
            "fingerprint": self.fingerprint,
            "is_signal": False,
        }


# =========================================================================
# The family
# =========================================================================


@dataclass
class HypothesisFamily:
    """A finite set of pre-registered hypotheses, lockable.

    Locking is one-way. There is no ``unlock``, because the value of a
    pre-registration is precisely that it cannot be revised once results
    arrive -- an unlock method would be the only thing anyone ever reached for.
    """

    family_id: str
    version: str
    protocol_version: str = PROTOCOL_VERSION
    hypotheses: dict[str, Hypothesis] = field(default_factory=dict)
    label_family: tuple[LabelDefinition, ...] = LABEL_FAMILY
    baselines: tuple[str, ...] = BASELINES
    fixed_parameters: dict = field(default_factory=lambda: dict(FIXED_PARAMETERS))
    sampling_policy: SamplingPolicy | None = None
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))
    creation_commit: str = field(default_factory=code_commit)
    _locked: bool = False

    # -- construction -----------------------------------------------------
    def add(self, hypothesis: Hypothesis) -> Hypothesis:
        if self._locked:
            raise FamilyLockError(
                f"{self.family_id}@{self.version} is locked. Adding a "
                "hypothesis after locking would change the trial count that "
                "earlier results were corrected against. Create a new family "
                "version instead."
            )
        if hypothesis.hypothesis_id in self.hypotheses:
            raise FamilyLockError(
                f"{hypothesis.hypothesis_id} is already in this family"
            )
        if hypothesis.parent_id and hypothesis.parent_id not in self.hypotheses:
            raise TemporalOrderError(
                f"{hypothesis.hypothesis_id} names parent "
                f"{hypothesis.parent_id!r}, which is not in the family. The "
                "nesting must be declarable in order so incremental "
                "contribution is measurable."
            )
        self.hypotheses[hypothesis.hypothesis_id] = hypothesis
        return hypothesis

    def lock(self) -> "HypothesisFamily":
        """Freeze the family. One-way."""
        if not self.hypotheses:
            raise FamilyLockError("refusing to lock an empty family")
        self._locked = True
        return self

    @property
    def is_locked(self) -> bool:
        return self._locked

    def require_unlocked(self) -> None:
        if self._locked:
            raise FamilyLockError(f"{self.family_id}@{self.version} is locked")

    # -- queries ----------------------------------------------------------
    def get(self, hypothesis_id: str) -> Hypothesis | None:
        return self.hypotheses.get(hypothesis_id)

    def require(self, hypothesis_id: str) -> Hypothesis:
        found = self.hypotheses.get(hypothesis_id)
        if found is None:
            raise KeyError(
                f"{hypothesis_id} is not in {self.family_id}@{self.version}; "
                f"members: {', '.join(sorted(self.hypotheses))}"
            )
        return found

    def all(self) -> list[Hypothesis]:
        return [self.hypotheses[k] for k in sorted(self.hypotheses)]

    def roots(self) -> list[Hypothesis]:
        return [h for h in self.all() if h.parent_id is None]

    def children_of(self, hypothesis_id: str) -> list[Hypothesis]:
        return [h for h in self.all() if h.parent_id == hypothesis_id]

    def lineage(self, hypothesis_id: str) -> list[Hypothesis]:
        """The nesting chain from root to this hypothesis.

        What makes incremental contribution measurable: comparing a hypothesis
        against its own parent isolates the condition that was added.
        """
        chain: list[Hypothesis] = []
        current = self.require(hypothesis_id)
        while True:
            chain.append(current)
            if current.parent_id is None:
                break
            current = self.require(current.parent_id)
        return list(reversed(chain))

    def incremental_comparisons(self) -> list[tuple[str, str]]:
        """Every (parent, child) pair the report must show.

        Reporting only the final conjunction hides which concept did the work.
        """
        return [(h.parent_id, h.hypothesis_id)
                for h in self.all() if h.parent_id is not None]

    # -- trial accounting -------------------------------------------------
    @property
    def trial_count(self) -> int:
        """Total tests declared: hypotheses x labels.

        Fixed at lock time and used by every multiple-testing correction. It
        counts labels because testing one hypothesis against six outcomes is
        six looks at the data, not one.
        """
        return sum(h.trial_count for h in self.all())

    @property
    def baseline_comparison_count(self) -> int:
        return len(self.all()) * len(self.baselines)

    def feature_versions(self) -> list[str]:
        found: set[str] = set()
        for hypothesis in self.all():
            found.update(hypothesis.feature_versions)
        return sorted(found)

    @property
    def fingerprint(self) -> str:
        payload = {
            "family_id": self.family_id, "version": self.version,
            "protocol_version": self.protocol_version,
            "hypotheses": [h.fingerprint for h in self.all()],
            "labels": sorted(l.name for l in self.label_family),
            "baselines": list(self.baselines),
            "parameters": self.fixed_parameters,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]

    # -- the data gate ----------------------------------------------------
    def require_market_claim_allowed(self, dataset) -> None:
        """Refuse to execute against data that has not cleared the gate.

        The family may exist without real data -- that is the point of
        pre-registration. It may not *run* against market data until the
        dataset reaches MARKET_CLAIM_ALLOWED.
        """
        from ..history.grades import DatasetGrade

        grades = getattr(dataset, "grades", None)
        if grades is None or not grades.granted(DatasetGrade.MARKET_CLAIM_ALLOWED):
            reason = (grades.blocking_reason if grades is not None
                      else "no grade assessment attached to the dataset")
            raise PermissionError(
                f"{self.family_id}@{self.version} may not execute against this "
                f"dataset: MARKET_CLAIM_ALLOWED not granted. {reason}"
            )

    def to_dict(self) -> dict:
        return {
            "family_id": self.family_id, "version": self.version,
            "protocol_version": self.protocol_version,
            "is_locked": self._locked,
            "fingerprint": self.fingerprint,
            "hypothesis_count": len(self.hypotheses),
            "trial_count": self.trial_count,
            "baseline_comparison_count": self.baseline_comparison_count,
            "feature_versions": self.feature_versions(),
            "labels": [l.name for l in self.label_family],
            "excursion_diagnostics": list(EXCURSION_DIAGNOSTICS),
            "baselines": list(self.baselines),
            "fixed_parameters": dict(self.fixed_parameters),
            "sampling_policy": (self.sampling_policy.to_dict()
                                if self.sampling_policy else None),
            "incremental_comparisons": self.incremental_comparisons(),
            "created_at": self.created_at.isoformat(),
            "creation_commit": self.creation_commit,
            "hypotheses": [h.to_dict() for h in self.all()],
        }


# -- feature keys, named once --------------------------------------------
SWEEP = "liquidity_sweep:v1"
DISPLACEMENT = "displacement:v1"
FVG = "fvg:v1"
EQUAL_HIGH = "equal_high:v1"
EQUAL_LOW = "equal_low:v1"

_LABELS = tuple(l.name for l in LABEL_FAMILY)

#: Label horizons run to 4h (the R-multiple window), so events closer than that
#: share outcome bars and are not independent observations.
_SAMPLING = SamplingPolicy(
    min_spacing=timedelta(0), deduplicate_overlaps=True,
    label_horizon=timedelta(hours=4), version="1",
)


def _hypothesis(hid, statement, parent, events, links, decision) -> Hypothesis:
    return Hypothesis(
        hypothesis_id=hid, statement=statement, parent_id=parent,
        events=tuple(events), temporal_relationships=tuple(links),
        decision_event=decision, label_versions=_LABELS,
        cost_model=REALISTIC.name, sampling_policy_version=_SAMPLING.version,
        protocol_version=PROTOCOL_VERSION, creation_commit=code_commit(),
    )


def build_family_v1() -> HypothesisFamily:
    """Construct and lock the family.

    Takes no parameters. A build function with a threshold argument is a
    parameter sweep waiting to be written, and the windows here are
    pre-registered values rather than choices to be explored.
    """
    family = HypothesisFamily(
        family_id=FAMILY_ID, version="v1", sampling_policy=_SAMPLING)

    family.add(_hypothesis(
        "ICT-LS-001",
        "Forward outcomes following a liquidity sweep of a previously "
        "confirmed swing level differ from baseline selection.",
        None,
        [FeatureEvent(SWEEP, EventRole.TRIGGER,
                      "sweep of a swing confirmed at or before the prior bar")],
        [],
        SWEEP,
    ))

    family.add(_hypothesis(
        "ICT-LS-002",
        "Forward outcomes following a liquidity sweep followed within 3 bars "
        "by a displacement differ from the sweep alone.",
        "ICT-LS-001",
        [FeatureEvent(SWEEP, EventRole.TRIGGER),
         FeatureEvent(DISPLACEMENT, EventRole.SEQUENCE,
                      "displacement at or after the sweep bar")],
        [TemporalLink(SWEEP, DISPLACEMENT, SWEEP_TO_DISPLACEMENT_MAX_BARS)],
        DISPLACEMENT,
    ))

    family.add(_hypothesis(
        "ICT-FVG-001",
        "Forward outcomes following a liquidity sweep that leaves a fair value "
        "gap within 5 bars differ from the sweep alone.",
        "ICT-LS-001",
        [FeatureEvent(SWEEP, EventRole.TRIGGER),
         FeatureEvent(FVG, EventRole.SEQUENCE, "gap available after the sweep")],
        [TemporalLink(SWEEP, FVG,
                      SWEEP_TO_DISPLACEMENT_MAX_BARS + DISPLACEMENT_TO_FVG_MAX_BARS)],
        FVG,
    ))

    family.add(_hypothesis(
        "ICT-COMBO-001",
        "Forward outcomes following sweep, then displacement within 3 bars, "
        "then a fair value gap within 2 further bars differ from sweep plus "
        "displacement.",
        "ICT-LS-002",
        [FeatureEvent(SWEEP, EventRole.TRIGGER),
         FeatureEvent(DISPLACEMENT, EventRole.SEQUENCE),
         FeatureEvent(FVG, EventRole.SEQUENCE)],
        [TemporalLink(SWEEP, DISPLACEMENT, SWEEP_TO_DISPLACEMENT_MAX_BARS),
         TemporalLink(DISPLACEMENT, FVG, DISPLACEMENT_TO_FVG_MAX_BARS)],
        FVG,
    ))

    family.add(_hypothesis(
        "ICT-EQ-001",
        "Forward outcomes following a liquidity sweep of a level that formed "
        "part of an equal-high or equal-low pair within the prior 50 bars "
        "differ from the sweep alone.",
        "ICT-LS-001",
        [FeatureEvent(EQUAL_HIGH, EventRole.CONTEXT,
                      "equality established before the sweep"),
         FeatureEvent(EQUAL_LOW, EventRole.CONTEXT,
                      "equality established before the sweep"),
         FeatureEvent(SWEEP, EventRole.TRIGGER)],
        [TemporalLink(EQUAL_HIGH, SWEEP, EQUALITY_LOOKBACK_BARS),
         TemporalLink(EQUAL_LOW, SWEEP, EQUALITY_LOOKBACK_BARS)],
        SWEEP,
    ))

    family.add(_hypothesis(
        "ICT-COMBO-002",
        "Forward outcomes following the full chain -- equality context, sweep, "
        "displacement within 3 bars, fair value gap within 2 further bars -- "
        "differ from the same chain without the equality context.",
        "ICT-COMBO-001",
        [FeatureEvent(EQUAL_HIGH, EventRole.CONTEXT),
         FeatureEvent(EQUAL_LOW, EventRole.CONTEXT),
         FeatureEvent(SWEEP, EventRole.TRIGGER),
         FeatureEvent(DISPLACEMENT, EventRole.SEQUENCE),
         FeatureEvent(FVG, EventRole.SEQUENCE)],
        [TemporalLink(EQUAL_HIGH, SWEEP, EQUALITY_LOOKBACK_BARS),
         TemporalLink(EQUAL_LOW, SWEEP, EQUALITY_LOOKBACK_BARS),
         TemporalLink(SWEEP, DISPLACEMENT, SWEEP_TO_DISPLACEMENT_MAX_BARS),
         TemporalLink(DISPLACEMENT, FVG, DISPLACEMENT_TO_FVG_MAX_BARS)],
        FVG,
    ))

    return family.lock()


#: The frozen family. Locked at import.
ICT_FAMILY_V1 = build_family_v1()
