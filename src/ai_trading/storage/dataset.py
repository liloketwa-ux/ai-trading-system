"""Immutable dataset versions.

A research run references exactly one dataset version. Without that, a result
cannot be reproduced: "we ran it on the BTC data" is not a statement anyone can
check a year later, because the data has since been appended to.

Versions are content-addressed. The checksum covers the provenance ids of every
member observation, so any change to membership changes the id, and a version
cannot be silently redefined under a name someone already cited.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .records import Observation, utc
from .store import ObservationStore

__all__ = ["DatasetVersion", "build_dataset_version", "code_commit"]


def code_commit(default: str = "unknown") -> str:
    """Current git commit, for reproducibility metadata."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return result.stdout.strip() or default
    except (OSError, subprocess.SubprocessError):
        return default


@dataclass(frozen=True)
class DatasetVersion:
    """An immutable, checksummed snapshot of a dataset's membership."""

    dataset_id: str
    sources: tuple[str, ...]
    start: datetime
    end: datetime
    schema_version: str
    created_at: datetime
    code_commit: str
    row_count: int
    checksum: str
    kinds: tuple[str, ...] = ()
    keys: tuple[str, ...] = ()
    notes: str = ""
    member_ids: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "start", utc(self.start))
        object.__setattr__(self, "end", utc(self.end))
        object.__setattr__(self, "created_at", utc(self.created_at))
        if self.end < self.start:
            raise ValueError("dataset end precedes start")

    def contains(self, observation: Observation) -> bool:
        return observation.provenance_id in set(self.member_ids)

    def verify(self, store: ObservationStore) -> bool:
        """Recompute the checksum from the store and compare.

        False means membership has drifted -- the version no longer describes
        what it claimed, and any result citing it is unreproducible.
        """
        present = {o.provenance_id for o in store._all()}
        if not set(self.member_ids) <= present:
            return False
        return _checksum(self.member_ids) == self.checksum

    def to_dict(self) -> dict:
        return {
            "dataset_id": self.dataset_id,
            "sources": list(self.sources),
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "schema_version": self.schema_version,
            "created_at": self.created_at.isoformat(),
            "code_commit": self.code_commit,
            "row_count": self.row_count,
            "checksum": self.checksum,
            "kinds": list(self.kinds),
            "keys": list(self.keys),
            "notes": self.notes,
        }

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict() | {"member_ids": list(self.member_ids)}
        path.write_text(json.dumps(payload, indent=2))
        return path

    @classmethod
    def load(cls, path: str | Path) -> "DatasetVersion":
        payload = json.loads(Path(path).read_text())
        member_ids = tuple(payload.pop("member_ids", []))
        return cls(
            dataset_id=payload["dataset_id"],
            sources=tuple(payload["sources"]),
            start=datetime.fromisoformat(payload["start"]),
            end=datetime.fromisoformat(payload["end"]),
            schema_version=payload["schema_version"],
            created_at=datetime.fromisoformat(payload["created_at"]),
            code_commit=payload["code_commit"],
            row_count=payload["row_count"],
            checksum=payload["checksum"],
            kinds=tuple(payload.get("kinds", ())),
            keys=tuple(payload.get("keys", ())),
            notes=payload.get("notes", ""),
            member_ids=member_ids,
        )


def _checksum(member_ids) -> str:
    joined = "\n".join(sorted(member_ids))
    return hashlib.sha256(joined.encode()).hexdigest()


def build_dataset_version(
    store: ObservationStore,
    *,
    dataset_id: str,
    as_of: datetime,
    keys: list[str] | None = None,
    kinds: list[str] | None = None,
    schema_version: str = "1",
    notes: str = "",
    include_unresolved: bool = False,
) -> DatasetVersion:
    """Freeze the store's eligible contents into an immutable version.

    Only observations available by ``as_of`` are members, so a dataset version
    is itself a point-in-time object: rebuilding it later with the same ``as_of``
    yields the same checksum even if the store has grown since.

    Records with unknown availability are excluded unless explicitly included,
    and including them makes the version unusable for point-in-time research.
    """
    members: list[Observation] = []
    for key in (keys or store.keys()):
        members.extend(
            store.query(as_of, key=key, strict=False)
            if not include_unresolved
            else [o for o in store._all() if o.key == key]
        )
    if kinds is not None:
        members = [o for o in members if o.kind in kinds]

    if not members:
        raise ValueError(f"dataset {dataset_id}: no observations matched")

    member_ids = tuple(sorted({o.provenance_id for o in members}))
    return DatasetVersion(
        dataset_id=dataset_id,
        sources=tuple(sorted({o.source for o in members})),
        start=min(o.event_time for o in members),
        end=max(o.event_time for o in members),
        schema_version=schema_version,
        created_at=datetime.now(timezone.utc),
        code_commit=code_commit(),
        row_count=len(member_ids),
        checksum=_checksum(member_ids),
        kinds=tuple(sorted({o.kind for o in members})),
        keys=tuple(sorted({o.key for o in members})),
        notes=notes,
        member_ids=member_ids,
    )
