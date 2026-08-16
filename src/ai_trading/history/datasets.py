"""Immutable research datasets, with lineage that reproduces.

A result is only as reproducible as the data it ran on, and "the NQ 5-minute
data" is not an identifier. :class:`ResearchDataset` pins the exact rows, the
code that built them, the quality verdict that admitted them, and the origin
that supplied them, then hashes the lot into a ``dataset_id``.

The identifier is content-derived rather than assigned, so two datasets built
from the same inputs collide by design and two built from different inputs
cannot be confused. If a later run produces a different id, something changed;
the id is the alarm.

The gate is enforced at construction. :meth:`ResearchDataset.create` refuses a
quality report that did not pass, so an unvalidated dataset cannot exist to be
accidentally used. It also refuses to mark a synthetic origin as research
approved -- synthetic data is welcome for testing the pipeline and must never
be able to back a claim about a market.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Sequence

from ..storage.dataset import code_commit
from .providers import Bar
from .quality import DatasetQualityReport

__all__ = [
    "DataOrigin", "ResearchDataset", "DatasetGateError", "FeatureEligibility",
]


class DatasetGateError(RuntimeError):
    """A dataset was created without satisfying the research gate."""


class DataOrigin(str, Enum):
    """Where the rows came from. Not a detail -- a hard boundary.

    ``SYNTHETIC`` data exists to test the machinery, and the machinery must not
    be able to launder it into a market claim. The distinction is carried on the
    dataset itself rather than in a naming convention, because naming
    conventions are not enforced.
    """

    REAL_MARKET = "real_market"
    SYNTHETIC = "synthetic"
    DERIVED = "derived"

    @property
    def may_support_market_claims(self) -> bool:
        return self is DataOrigin.REAL_MARKET


@dataclass(frozen=True)
class FeatureEligibility:
    """Which feature families this dataset can honestly support.

    Bars cannot support microstructure features, and a dataset whose
    availability is unverified cannot support latency-sensitive ones. Recording
    this on the dataset stops the question being re-litigated per study.
    """

    bar_features: bool = True
    microstructure_features: bool = False
    latency_sensitive_features: bool = False
    cross_sectional_features: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "bar_features": self.bar_features,
            "microstructure_features": self.microstructure_features,
            "latency_sensitive_features": self.latency_sensitive_features,
            "cross_sectional_features": self.cross_sectional_features,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ResearchDataset:
    """An immutable, checksummed slice of validated history."""

    dataset_id: str
    source: str
    origin: DataOrigin
    instrument: str
    contract: str
    timeframes: tuple[str, ...]
    date_range: tuple[datetime, datetime]
    schema_version: str
    feature_eligibility: FeatureEligibility
    quality_report: DatasetQualityReport
    code_commit: str
    checksum: str
    row_count: int
    created_at: datetime
    note: str = ""

    @property
    def may_support_market_claims(self) -> bool:
        return self.origin.may_support_market_claims

    def require_real_market(self, purpose: str = "a market claim") -> None:
        """Refuse to let synthetic data back a statement about a market."""
        if not self.may_support_market_claims:
            raise DatasetGateError(
                f"dataset {self.dataset_id} has origin {self.origin.value} and cannot "
                f"support {purpose}. Results computed on it describe the generator, "
                "not any market."
            )

    def to_dict(self) -> dict:
        return {
            "dataset_id": self.dataset_id,
            "source": self.source,
            "origin": self.origin.value,
            "instrument": self.instrument,
            "contract": self.contract,
            "timeframes": list(self.timeframes),
            "date_range": [self.date_range[0].isoformat(),
                           self.date_range[1].isoformat()],
            "schema_version": self.schema_version,
            "feature_eligibility": self.feature_eligibility.to_dict(),
            "quality_report": self.quality_report.to_dict(),
            "code_commit": self.code_commit,
            "checksum": self.checksum,
            "row_count": self.row_count,
            "created_at": self.created_at.isoformat(),
            "may_support_market_claims": self.may_support_market_claims,
            "note": self.note,
        }

    def save(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True))
        return destination

    # -- construction -----------------------------------------------------
    @classmethod
    def create(cls, bars: Sequence[Bar], *, source: str, origin: DataOrigin,
               quality_report: DatasetQualityReport,
               feature_eligibility: FeatureEligibility | None = None,
               note: str = "") -> "ResearchDataset":
        """Build a dataset, refusing anything the gate did not admit."""
        if not bars:
            raise DatasetGateError("cannot create a dataset from zero rows")
        if not quality_report.is_research_eligible:
            raise DatasetGateError(
                f"quality report is {quality_report.quality_status.value} with "
                f"{len(quality_report.fatal_findings)} fatal finding(s); an unvalidated "
                "dataset must not exist to be used by accident. Failing checks: "
                + (", ".join(f.check for f in quality_report.fatal_findings) or "none")
            )

        instruments = {b.instrument for b in bars}
        contracts = {b.contract for b in bars}
        if len(instruments) > 1 or len(contracts) > 1:
            raise DatasetGateError(
                "a research dataset covers one contract; received instruments="
                f"{sorted(instruments)} contracts={sorted(contracts)}. Contracts are "
                "kept separate: joining them is a roll, and a roll needs a policy."
            )
        schemas = {b.schema_version for b in bars}
        if len(schemas) > 1:
            raise DatasetGateError(
                f"rows span multiple schema versions {sorted(schemas)}; a dataset with "
                "two layouts cannot be interpreted consistently"
            )

        ordered = sorted(bars, key=lambda b: (b.timeframe, b.event_time))
        checksum = _checksum(ordered)
        commit = code_commit()
        timeframes = tuple(sorted({b.timeframe for b in ordered}))

        eligibility = feature_eligibility or _default_eligibility(ordered)
        created_at = datetime.now(timezone.utc)

        dataset_id = _dataset_id(
            source=source, origin=origin,
            instrument=next(iter(instruments)), contract=next(iter(contracts)),
            timeframes=timeframes, checksum=checksum, commit=commit,
            schema_version=next(iter(schemas)),
        )

        return cls(
            dataset_id=dataset_id, source=source, origin=origin,
            instrument=next(iter(instruments)), contract=next(iter(contracts)),
            timeframes=timeframes,
            date_range=(ordered[0].event_time, ordered[-1].event_time),
            schema_version=next(iter(schemas)),
            feature_eligibility=eligibility, quality_report=quality_report,
            code_commit=commit, checksum=checksum, row_count=len(ordered),
            created_at=created_at, note=note,
        )


def _default_eligibility(bars: Sequence[Bar]) -> FeatureEligibility:
    """Infer what bar data can honestly support."""
    from .availability import AvailabilityQuality

    observed = all(b.availability_quality is AvailabilityQuality.OBSERVED
                   for b in bars)
    return FeatureEligibility(
        bar_features=True,
        microstructure_features=False,
        latency_sensitive_features=observed,
        cross_sectional_features=False,
        reason=(
            "bar data supports bar features only; microstructure features need "
            "trades or books. Latency-sensitive features are "
            + ("permitted: every row carries an observed arrival time."
               if observed else
               "refused: arrival times are not observed, so any latency result would "
               "measure the availability assumption rather than the market.")
        ),
    )


def _checksum(bars: Sequence[Bar]) -> str:
    """Content hash over the fields that define the rows.

    Includes ``available_at`` deliberately: re-deriving availability under a
    different policy produces different research, so it must produce a
    different dataset.
    """
    digest = hashlib.sha256()
    for bar in bars:
        digest.update(
            f"{bar.contract}|{bar.timeframe}|{bar.event_time.isoformat()}|"
            f"{bar.available_at.isoformat()}|{bar.open!r}|{bar.high!r}|"
            f"{bar.low!r}|{bar.close!r}|{bar.volume!r}".encode()
        )
    return digest.hexdigest()


def _dataset_id(*, source: str, origin: DataOrigin, instrument: str,
                contract: str, timeframes: tuple[str, ...], checksum: str,
                commit: str, schema_version: str) -> str:
    digest = hashlib.sha256(
        json.dumps({
            "source": source, "origin": origin.value, "instrument": instrument,
            "contract": contract, "timeframes": list(timeframes),
            "checksum": checksum, "code_commit": commit,
            "schema_version": schema_version,
        }, sort_keys=True).encode()
    ).hexdigest()[:16]
    return f"{instrument.lower()}-{contract.lower()}-{origin.value}-{digest}"
