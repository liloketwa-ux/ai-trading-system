"""Development / validation / locked-holdout splits.

The holdout is not merely "the last 20%". It is a period that optimization
**cannot reach**, enforced mechanically rather than by discipline, because
discipline is exactly what fails at 2am when one more parameter sweep would
settle an argument.

Two mechanisms do the enforcing:

* :meth:`SplitDefinition.window` refuses to hand back holdout dates to any
  caller whose purpose is not an explicit, audited holdout evaluation.
* Every touch is recorded in an append-only ledger, so "we only looked once" is
  a checkable claim rather than a recollection.

If a strategy changes after holdout results are seen, the holdout is spent for
that strategy. The registry enforces this by requiring a new research version;
re-evaluating a modified strategy against an already-observed holdout is how a
number stops meaning what it appears to mean.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from ..storage.dataset import code_commit
from ..storage.records import utc

__all__ = ["Purpose", "SplitDefinition", "HoldoutViolation", "HoldoutLedger", "SplitRegistry"]


class HoldoutViolation(RuntimeError):
    """Optimization attempted to reach the locked holdout."""


class Purpose(str, Enum):
    """Why a caller wants data. Determines which windows it may see."""

    EXPLORATION = "exploration"        # feature search, EDA
    TRAINING = "training"
    VALIDATION = "validation"
    PARAMETER_SWEEP = "parameter_sweep"
    FINAL_HOLDOUT_EVAL = "final_holdout_eval"  # the one purpose that unlocks it

    @property
    def may_touch_holdout(self) -> bool:
        return self is Purpose.FINAL_HOLDOUT_EVAL


@dataclass(frozen=True)
class SplitDefinition:
    """An immutable three-way temporal split.

    Windows are contiguous and ordered: development < validation < holdout.
    Overlap is rejected at construction, since overlapping windows leak
    validation data into training silently.
    """

    split_id: str
    split_version: str
    dev_start: datetime
    dev_end: datetime
    validation_start: datetime
    validation_end: datetime
    holdout_start: datetime
    holdout_end: datetime
    created_at: datetime
    created_commit: str
    dataset_id: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        for name in ("dev_start", "dev_end", "validation_start", "validation_end",
                     "holdout_start", "holdout_end", "created_at"):
            object.__setattr__(self, name, utc(getattr(self, name)))

        if not self.dev_start < self.dev_end:
            raise ValueError("development window is empty or inverted")
        if not self.validation_start < self.validation_end:
            raise ValueError("validation window is empty or inverted")
        if not self.holdout_start < self.holdout_end:
            raise ValueError("holdout window is empty or inverted")
        if self.validation_start < self.dev_end:
            raise ValueError("validation overlaps development -- windows must be disjoint")
        if self.holdout_start < self.validation_end:
            raise ValueError("holdout overlaps validation -- windows must be disjoint")

    @property
    def checksum(self) -> str:
        payload = json.dumps(
            {
                "split_id": self.split_id,
                "split_version": self.split_version,
                "windows": [
                    self.dev_start.isoformat(), self.dev_end.isoformat(),
                    self.validation_start.isoformat(), self.validation_end.isoformat(),
                    self.holdout_start.isoformat(), self.holdout_end.isoformat(),
                ],
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    def window(self, purpose: Purpose) -> tuple[datetime, datetime]:
        """Dates a caller with this purpose may use.

        Raises:
            HoldoutViolation: if a non-holdout purpose asks for holdout dates.
        """
        if purpose in (Purpose.EXPLORATION, Purpose.TRAINING, Purpose.PARAMETER_SWEEP):
            return self.dev_start, self.dev_end
        if purpose is Purpose.VALIDATION:
            return self.validation_start, self.validation_end
        if purpose is Purpose.FINAL_HOLDOUT_EVAL:
            return self.holdout_start, self.holdout_end
        raise HoldoutViolation(f"unrecognized purpose: {purpose}")

    def contains_holdout(self, start: datetime, end: datetime) -> bool:
        """Whether a requested range intersects the locked holdout."""
        return utc(start) < self.holdout_end and utc(end) > self.holdout_start

    def assert_no_holdout(self, start: datetime, end: datetime, purpose: Purpose) -> None:
        """Guard an arbitrary date range against holdout contamination."""
        if purpose.may_touch_holdout:
            return
        if self.contains_holdout(start, end):
            raise HoldoutViolation(
                f"purpose={purpose.value} requested {utc(start).date()}..{utc(end).date()}, "
                f"which intersects the locked holdout "
                f"({self.holdout_start.date()}..{self.holdout_end.date()}). "
                "The holdout is evaluated once, after tuning is frozen."
            )

    def to_dict(self) -> dict:
        return {
            "split_id": self.split_id,
            "split_version": self.split_version,
            "dev_start": self.dev_start.isoformat(),
            "dev_end": self.dev_end.isoformat(),
            "validation_start": self.validation_start.isoformat(),
            "validation_end": self.validation_end.isoformat(),
            "holdout_start": self.holdout_start.isoformat(),
            "holdout_end": self.holdout_end.isoformat(),
            "created_at": self.created_at.isoformat(),
            "created_commit": self.created_commit,
            "dataset_id": self.dataset_id,
            "checksum": self.checksum,
            "notes": self.notes,
        }


@dataclass
class HoldoutLedger:
    """Append-only record of every holdout access.

    Written to disk so "we evaluated the holdout once" survives the session that
    made the claim.
    """

    path: Path

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(
                "# Holdout access ledger\n\n"
                "Append-only. Every read of the locked holdout is recorded here.\n"
                "A strategy modified after a holdout evaluation needs a NEW research\n"
                "version and a new holdout period -- the old one is spent.\n\n"
                "| timestamp | split | research_version | commit | reason |\n"
                "|---|---|---|---|---|\n"
            )

    def record(self, split: SplitDefinition, research_version: str, reason: str) -> None:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        row = (
            f"| {stamp} | {split.split_id}@{split.split_version} | {research_version} "
            f"| {code_commit()[:12]} | {reason} |\n"
        )
        with self.path.open("a") as handle:
            handle.write(row)

    def touches(self) -> int:
        """How many times the holdout has been read."""
        lines = self.path.read_text().splitlines()
        return sum(1 for line in lines if line.startswith("| 20"))

    def versions_evaluated(self) -> set[str]:
        versions = set()
        for line in self.path.read_text().splitlines():
            if line.startswith("| 20"):
                parts = [p.strip() for p in line.split("|")]
                if len(parts) > 3:
                    versions.add(parts[3])
        return versions


class SplitRegistry:
    """Holds the split and mediates every holdout access."""

    def __init__(self, split: SplitDefinition, ledger: HoldoutLedger) -> None:
        self.split = split
        self.ledger = ledger

    @classmethod
    def create(
        cls,
        *,
        split_id: str,
        dev: tuple[datetime, datetime],
        validation: tuple[datetime, datetime],
        holdout: tuple[datetime, datetime],
        ledger_path: str | Path,
        split_version: str = "1",
        dataset_id: str = "",
        notes: str = "",
    ) -> "SplitRegistry":
        split = SplitDefinition(
            split_id=split_id,
            split_version=split_version,
            dev_start=dev[0], dev_end=dev[1],
            validation_start=validation[0], validation_end=validation[1],
            holdout_start=holdout[0], holdout_end=holdout[1],
            created_at=datetime.now(timezone.utc),
            created_commit=code_commit(),
            dataset_id=dataset_id,
            notes=notes,
        )
        return cls(split, HoldoutLedger(Path(ledger_path)))

    def window(self, purpose: Purpose) -> tuple[datetime, datetime]:
        """Window for a purpose. Holdout access is logged."""
        if purpose.may_touch_holdout:
            raise HoldoutViolation(
                "holdout access must go through evaluate_holdout(), which records "
                "the research version and reason in the ledger"
            )
        return self.split.window(purpose)

    def evaluate_holdout(self, research_version: str, reason: str) -> tuple[datetime, datetime]:
        """Unlock the holdout once, recording who asked and why.

        Re-evaluating a research version already present in the ledger is
        refused: the holdout is spent for that version, and a modified strategy
        needs a new version and a new holdout.
        """
        if research_version in self.ledger.versions_evaluated():
            raise HoldoutViolation(
                f"research version {research_version!r} has already been evaluated against "
                f"holdout {self.split.split_id}. Modifying a strategy after seeing holdout "
                "results requires a NEW research version and a NEW holdout period."
            )
        self.ledger.record(self.split, research_version, reason)
        return self.split.holdout_start, self.split.holdout_end
