"""Append-only observation store with central point-in-time enforcement.

**The filter lives here and nowhere else.** Every read path routes through
:meth:`ObservationStore.query`, which applies ``available_at <= decision_time``
before anything downstream sees a row. Enforcing it centrally is the point: a
per-call-site filter is one forgotten predicate away from silent leakage, and
silent leakage produces a number that looks like a result.

The store is append-only. :meth:`append` refuses to replace an existing
provenance id, and there is deliberately no update or delete. Enrichment adds a
later observation; it never overwrites an earlier one, so the state as it looked
at any past instant remains reconstructible.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime
from enum import Enum
from pathlib import Path

import pandas as pd

from .records import (
    Availability,
    Observation,
    TemporalIntegrityError,
    UnknownAvailabilityError,
    utc,
)

__all__ = ["ObservationStore", "InMemoryStore", "ParquetStore", "Restatements"]


class ObservationStore(ABC):
    """Append-only store of immutable observations."""

    # -- writes ------------------------------------------------------------

    @abstractmethod
    def _write(self, observations: list[Observation]) -> None: ...

    @abstractmethod
    def _all(self) -> list[Observation]: ...

    def append(self, observations: Observation | list[Observation]) -> int:
        """Append observations. Existing provenance ids are rejected.

        Returns the number written. Re-appending an identical record is a
        no-op rather than an error -- ingestion retries are expected and must be
        idempotent -- but appending a *different* record under an existing id is
        a corruption and raises.
        """
        batch = [observations] if isinstance(observations, Observation) else list(observations)
        existing = {o.provenance_id: o for o in self._all()}

        fresh: list[Observation] = []
        for observation in batch:
            prior = existing.get(observation.provenance_id)
            if prior is None:
                fresh.append(observation)
                existing[observation.provenance_id] = observation
            elif prior.to_row() != observation.to_row():
                raise TemporalIntegrityError(
                    f"provenance_id {observation.provenance_id} already exists with different "
                    "content -- the store is append-only and observations are immutable"
                )
        if fresh:
            self._write(fresh)
        return len(fresh)

    # -- point-in-time reads -----------------------------------------------

    def query(
        self,
        decision_time: datetime,
        *,
        key: str | None = None,
        kind: str | None = None,
        since: datetime | None = None,
        strict: bool = True,
    ) -> list[Observation]:
        """Observations usable for a decision made at ``decision_time``.

        Applies ``available_at <= decision_time`` centrally. Records with
        unresolved availability are excluded always; under ``strict`` their
        presence additionally raises, so a backtest **fails closed** rather than
        quietly running on a subset it did not know was filtered.

        Args:
            decision_time: The instant the decision is made.
            key: Restrict to one instrument/token.
            kind: Restrict to one observation family.
            since: Ignore observations whose event time precedes this.
            strict: Raise if any matching record has unknown availability.
        """
        cutoff = utc(decision_time)
        matched = [
            o for o in self._all()
            if (key is None or o.key == key)
            and (kind is None or o.kind == kind)
            and (since is None or o.event_time >= utc(since))
        ]

        if strict:
            unknown = [o for o in matched if o.availability is Availability.UNKNOWN]
            if unknown:
                raise UnknownAvailabilityError(
                    f"{len(unknown)} observation(s) for key={key} kind={kind} have "
                    f"UNKNOWN_AVAILABILITY (e.g. {unknown[0].kind}/{unknown[0].source}); "
                    "resolve availability or pass strict=False to exclude them explicitly"
                )

        eligible = [o for o in matched if o.is_available_at(cutoff)]
        eligible.sort(key=lambda o: (o.event_time, o.available_at or o.event_time))
        return eligible

    def reconstruct_state(
        self,
        decision_time: datetime,
        key: str,
        *,
        kinds: list[str] | None = None,
        strict: bool = True,
        restatements: "Restatements" = None,
    ) -> dict[str, Observation]:
        """The world as it was knowable for ``key`` at ``decision_time``.

        Returns the single latest eligible observation per kind -- merged views
        must keep the latest *valid* point-in-time record, not the latest record
        overall, or a later enrichment silently rewrites the past.

        Ties on event time are restatements of the same instant, resolved by
        ``restatements`` — see :class:`Restatements`. The default takes the most
        recent correction available by ``decision_time``, which is knowledge the
        decision genuinely had, not look-ahead.
        """
        policy = restatements or Restatements.LATEST_KNOWN
        eligible = self.query(decision_time, key=key, strict=strict)
        latest: dict[str, Observation] = {}
        for observation in eligible:
            if kinds is not None and observation.kind not in kinds:
                continue
            current = latest.get(observation.kind)
            if current is None or _supersedes(observation, current, policy):
                latest[observation.kind] = observation
        return latest

    def latest(
        self,
        decision_time: datetime,
        key: str,
        kind: str,
        *,
        strict: bool = True,
        restatements: "Restatements" = None,
    ) -> Observation | None:
        """Latest eligible observation of one kind, or ``None``."""
        return self.reconstruct_state(
            decision_time, key, kinds=[kind], strict=strict, restatements=restatements
        ).get(kind)

    # -- introspection -----------------------------------------------------

    def unresolved(self) -> list[Observation]:
        """Records excluded from research pending availability resolution."""
        return [o for o in self._all() if o.availability is Availability.UNKNOWN]

    def keys(self) -> list[str]:
        return sorted({o.key for o in self._all()})

    def kinds(self) -> list[str]:
        return sorted({o.kind for o in self._all()})

    def count(self) -> int:
        return len(self._all())

    def to_frame(self, **query_kwargs) -> pd.DataFrame:
        """Eligible observations as a frame. Accepts :meth:`query` arguments."""
        rows = [o.to_row() for o in self.query(**query_kwargs)]
        return pd.DataFrame(rows) if rows else pd.DataFrame()


class Restatements(str, Enum):
    """How to treat two observations describing the same instant.

    When a source restates a value -- same ``event_time``, later
    ``available_at`` -- both records are legitimate, and which one a decision
    should use depends on what is being modelled.

    ``LATEST_KNOWN`` (default) takes the most recent restatement available by
    the decision time. This is *not* look-ahead: at that decision time the
    correction genuinely was known. It answers "what did we believe about that
    instant, as of now".

    ``FIRST_KNOWN`` keeps the value first published and ignores later
    corrections. It answers "what did a live system act on", which is the right
    question when modelling a system that never revisits a decision. It is the
    more conservative choice and discards genuinely available information.
    """

    LATEST_KNOWN = "latest_known"
    FIRST_KNOWN = "first_known"


def _supersedes(
    candidate: Observation,
    current: Observation,
    restatements: Restatements = Restatements.LATEST_KNOWN,
) -> bool:
    """Whether ``candidate`` is the better point-in-time record."""
    if candidate.event_time != current.event_time:
        return candidate.event_time > current.event_time

    candidate_at = candidate.available_at or candidate.event_time
    current_at = current.available_at or current.event_time
    if restatements is Restatements.FIRST_KNOWN:
        return candidate_at < current_at
    # Both are available by the decision time, so the later one is the
    # correction the decision would actually have had.
    return candidate_at > current_at


class InMemoryStore(ObservationStore):
    """In-process store. Deterministic, used for tests and small studies."""

    def __init__(self, observations: list[Observation] | None = None) -> None:
        self._records: list[Observation] = []
        self._index: dict[tuple[str, str], list[Observation]] = defaultdict(list)
        if observations:
            self.append(observations)

    def _write(self, observations: list[Observation]) -> None:
        for observation in observations:
            self._records.append(observation)
            self._index[(observation.key, observation.kind)].append(observation)

    def _all(self) -> list[Observation]:
        return self._records


class ParquetStore(ObservationStore):
    """Parquet-backed store, one file per append batch.

    Batch files are never rewritten, which is what keeps the history immutable
    on disk as well as in the API.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._cache: list[Observation] | None = None

    def _write(self, observations: list[Observation]) -> None:
        frame = pd.DataFrame([o.to_row() for o in observations])
        frame["derived_from"] = frame["derived_from"].astype(str)
        batch = self.root / f"batch-{len(list(self.root.glob('batch-*.parquet'))):06d}.parquet"
        frame.to_parquet(batch, index=False)
        self._cache = None

    def _all(self) -> list[Observation]:
        if self._cache is not None:
            return self._cache

        records: list[Observation] = []
        for path in sorted(self.root.glob("batch-*.parquet")):
            for row in pd.read_parquet(path).to_dict("records"):
                value = {
                    k[len("value_"):]: v for k, v in row.items() if k.startswith("value_")
                }
                available = row.get("available_at")
                records.append(
                    Observation(
                        key=row["key"],
                        kind=row["kind"],
                        event_time=pd.Timestamp(row["event_time"]).to_pydatetime(),
                        available_at=(
                            None if pd.isna(available)
                            else pd.Timestamp(available).to_pydatetime()
                        ),
                        ingested_at=pd.Timestamp(row["ingested_at"]).to_pydatetime(),
                        source=row["source"],
                        value=value,
                        schema_version=str(row.get("schema_version", "1")),
                        dataset_version=row.get("dataset_version") or None,
                        provenance_id=row["provenance_id"],
                        raw_ref=row.get("raw_ref") or None,
                        timeframe=row.get("timeframe") or None,
                        derived_from=tuple(f for f in str(row.get("derived_from", "")).split(",") if f),
                    )
                )
        self._cache = records
        return records
