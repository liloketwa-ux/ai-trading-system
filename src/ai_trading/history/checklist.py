"""The fourteen-item checklist a dataset clears before research approval.

A quality report answers "are the rows well formed". This answers the broader
question: "do we know what this data *is*". They fail differently. A file can
be structurally immaculate and still be unusable because nobody recorded which
contract it covers, what timezone its stamps are in, or whether prices were
adjusted.

Every item is tri-state. ``UNKNOWN`` is not ``FAIL`` -- an unverified roll
policy is a gap to close, a wrong one is a defect to fix, and collapsing them
loses the distinction that decides what to do next. Both block approval; only
one of them means something is broken.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

__all__ = [
    "CheckOutcome", "ChecklistItem", "DatasetChecklist", "CHECKLIST_ITEMS",
]

#: The required items, in the order they are reported.
CHECKLIST_ITEMS = (
    "source_identity",
    "contract_identity",
    "coverage",
    "session_calendar",
    "timezone",
    "duplicate_rows",
    "missing_intervals",
    "invalid_ohlc",
    "timestamp_anomalies",
    "contract_expiry",
    "roll_metadata",
    "adjustment_policy",
    "availability_semantics",
    "provenance",
)


class CheckOutcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"

    @property
    def blocks_approval(self) -> bool:
        return self is not CheckOutcome.PASS

    @property
    def is_defect(self) -> bool:
        """A failure is something wrong. Unknown is something absent."""
        return self is CheckOutcome.FAIL


@dataclass(frozen=True)
class ChecklistItem:
    name: str
    outcome: CheckOutcome
    detail: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "outcome": self.outcome.value,
                "detail": self.detail}


@dataclass
class DatasetChecklist:
    """Fourteen items, all of which must pass before research approval."""

    dataset_label: str
    items: dict[str, ChecklistItem] = field(default_factory=dict)
    completed_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        for name in CHECKLIST_ITEMS:
            self.items.setdefault(name, ChecklistItem(
                name, CheckOutcome.UNKNOWN, "not yet assessed"))

    def record(self, name: str, outcome: CheckOutcome,
               detail: str = "") -> ChecklistItem:
        if name not in CHECKLIST_ITEMS:
            raise KeyError(
                f"{name!r} is not a checklist item; the list is fixed so that a "
                f"dataset cannot be approved against a shortened one. Items: "
                f"{', '.join(CHECKLIST_ITEMS)}"
            )
        item = ChecklistItem(name, outcome, detail)
        self.items[name] = item
        return item

    @property
    def failures(self) -> list[ChecklistItem]:
        return [i for i in self.items.values() if i.outcome is CheckOutcome.FAIL]

    @property
    def unknowns(self) -> list[ChecklistItem]:
        return [i for i in self.items.values() if i.outcome is CheckOutcome.UNKNOWN]

    @property
    def is_complete(self) -> bool:
        """Every item passes. The only state that permits approval."""
        return all(i.outcome is CheckOutcome.PASS for i in self.items.values())

    @property
    def blocking(self) -> list[str]:
        return sorted(i.name for i in self.items.values()
                      if i.outcome.blocks_approval)

    def summary(self) -> str:
        passed = sum(1 for i in self.items.values()
                     if i.outcome is CheckOutcome.PASS)
        return (f"{self.dataset_label}: {passed}/{len(CHECKLIST_ITEMS)} passed, "
                f"{len(self.failures)} failed, {len(self.unknowns)} unknown")

    def require_complete(self) -> None:
        if self.is_complete:
            return
        parts = []
        if self.failures:
            parts.append("defects: " + ", ".join(sorted(i.name for i in self.failures)))
        if self.unknowns:
            parts.append("unverified: " + ", ".join(sorted(i.name for i in self.unknowns)))
        raise RuntimeError(
            f"{self.dataset_label} is not approvable -- " + "; ".join(parts)
        )

    def to_dict(self) -> dict:
        return {
            "dataset_label": self.dataset_label,
            "is_complete": self.is_complete,
            "blocking": self.blocking,
            "summary": self.summary(),
            "items": [self.items[name].to_dict() for name in CHECKLIST_ITEMS],
            "completed_at": self.completed_at.isoformat(),
        }
