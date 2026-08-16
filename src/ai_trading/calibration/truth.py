"""Ground truth, sealed away from the research that is supposed to find it.

The point of a calibration dataset is that the answer is known. The hazard is
that the answer is *available*: if the generating parameters sit on the same
object the detector reads, sooner or later a detector reads them, and the
calibration proves nothing except that the code can copy a number.

So the truth is sealed. :class:`SealedTruth` carries the parameters and refuses
to hand them over until :meth:`reveal` is called with a purpose, and every
reveal is logged. A detector that reveals is not thereby prevented from
cheating -- nothing can prevent that -- but it leaves a record, and
:meth:`CalibrationRun.assert_blind` fails when the record is non-empty at
detection time.

The dataset a detector receives is a plain sequence of bars with no reference
back to the generator. That is the real defence; the seal is what makes a
breach visible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

__all__ = ["EdgeKind", "GroundTruth", "SealedTruth", "TruthRevealed", "RevealLog"]


class EdgeKind(str, Enum):
    """What the generator actually put into the data."""

    NONE = "none"
    MOMENTUM = "momentum"
    MEAN_REVERSION = "mean_reversion"
    REGIME_DEPENDENT = "regime_dependent"
    SUB_COST = "sub_cost"

    @property
    def has_gross_edge(self) -> bool:
        """Whether a gross (pre-cost) relationship exists to be found.

        ``SUB_COST`` does have one -- that is the entire point of the dataset.
        The edge is real and too small to trade, and a research system that
        cannot tell those apart will trade it.
        """
        return self is not EdgeKind.NONE


@dataclass(frozen=True)
class GroundTruth:
    """The generator's actual parameters.

    Never passed to a detector. Held inside a :class:`SealedTruth` and used
    only by the scorer, after a detection result has been committed.
    """

    edge_kind: EdgeKind
    #: The parameter that creates the edge, in the generator's own units.
    #: Momentum: AR(1) coefficient. Mean reversion: reversal probability.
    effect_size: float
    #: Expected gross per-trade return in basis points, where the generator can
    #: state one. ``None`` when the relationship is not expressible that way.
    expected_gross_bps: float | None = None
    seed: int = 0
    regimes: tuple[str, ...] = ()
    regime_effects: dict[str, float] = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "edge_kind": self.edge_kind.value,
            "effect_size": self.effect_size,
            "expected_gross_bps": self.expected_gross_bps,
            "seed": self.seed,
            "regimes": list(self.regimes),
            "regime_effects": dict(self.regime_effects),
            "note": self.note,
        }


@dataclass(frozen=True)
class TruthRevealed:
    """One access to sealed truth, with who asked and why."""

    at: datetime
    purpose: str

    def to_dict(self) -> dict:
        return {"at": self.at.isoformat(), "purpose": self.purpose}


class RevealLog:
    """Append-only record of truth accesses."""

    def __init__(self) -> None:
        self._entries: list[TruthRevealed] = []

    def record(self, purpose: str) -> TruthRevealed:
        entry = TruthRevealed(datetime.now(timezone.utc), purpose)
        self._entries.append(entry)
        return entry

    @property
    def entries(self) -> list[TruthRevealed]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def to_dict(self) -> dict:
        return {"reveals": [e.to_dict() for e in self._entries],
                "count": len(self._entries)}


class SealedTruth:
    """Holds a :class:`GroundTruth` and logs every access to it.

    Deliberately not a dataclass and deliberately without ``__repr__`` showing
    the payload: printing a sealed object in a debug session should not spill
    the answer into a log the detector's author then reads.
    """

    __slots__ = ("_truth", "_log", "label")

    def __init__(self, truth: GroundTruth, label: str = "") -> None:
        self._truth = truth
        self._log = RevealLog()
        self.label = label

    def reveal(self, purpose: str) -> GroundTruth:
        """Return the truth, recording that it was accessed."""
        if not purpose:
            raise ValueError(
                "revealing ground truth requires a stated purpose, so the reveal "
                "log says what the access was for"
            )
        self._log.record(purpose)
        return self._truth

    @property
    def log(self) -> RevealLog:
        return self._log

    @property
    def was_revealed(self) -> bool:
        return len(self._log) > 0

    def __repr__(self) -> str:
        return (f"SealedTruth({self.label!r}, sealed, "
                f"reveals={len(self._log)})")
