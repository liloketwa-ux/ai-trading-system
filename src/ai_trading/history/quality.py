"""The data-quality gate.

Nothing reaches research without passing through here. The checks are dull on
purpose -- ordering, duplicates, impossible OHLC, negative volume, timestamp
validity, session structure, bar spacing, timezone -- because the failures they
catch are dull, common, and destroy studies quietly.

Two design choices are worth stating.

**Anomalies are counted, not just flagged.** "This dataset has gaps" is not
actionable; "this dataset is missing 412 of 98,000 expected bars, 380 of them in
one week in March" tells you whether to fix it, exclude it, or ignore it.

**A gap is not automatically a defect.** Futures do not trade continuously, and
a checker that expects a bar every minute will report a maintenance break and a
holiday as data loss. The expected session structure is therefore an input, and
a dataset whose sessions were never specified reports its missing-bar count as
*unknown* instead of pretending zero.

``QualityStatus.RESEARCH_ELIGIBLE`` is the only status that opens the gate, and
it requires zero fatal findings. Warnings are recorded and do not block: a
dataset with 3 duplicate rows out of 2 million is worth knowing about and not
worth refusing.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Sequence

from .providers import Bar

__all__ = [
    "QualityStatus", "Severity", "QualityFinding", "SessionSpec",
    "DatasetQualityReport", "run_quality_gate", "TIMEFRAME_SECONDS",
]

#: Bar spacing by timeframe label.
TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "4h": 14400, "1d": 86400,
}


class Severity(str, Enum):
    FATAL = "fatal"
    WARNING = "warning"
    INFO = "info"


class QualityStatus(str, Enum):
    """Whether a dataset may be used for research."""

    RESEARCH_ELIGIBLE = "research_eligible"
    ELIGIBLE_WITH_WARNINGS = "eligible_with_warnings"
    REJECTED = "rejected"
    NOT_ASSESSED = "not_assessed"

    @property
    def opens_the_gate(self) -> bool:
        return self in (QualityStatus.RESEARCH_ELIGIBLE,
                        QualityStatus.ELIGIBLE_WITH_WARNINGS)


@dataclass(frozen=True)
class QualityFinding:
    """One thing wrong, with enough detail to act on."""

    check: str
    severity: Severity
    count: int
    detail: str
    examples: tuple[str, ...] = ()

    @property
    def is_fatal(self) -> bool:
        return self.severity is Severity.FATAL

    def to_dict(self) -> dict:
        return {
            "check": self.check, "severity": self.severity.value,
            "count": self.count, "detail": self.detail,
            "examples": list(self.examples),
        }


@dataclass(frozen=True)
class SessionSpec:
    """Expected trading structure, so gaps can be judged rather than counted.

    ``weekdays`` uses ``date.weekday()`` numbering (Monday is 0). A CME equity
    index future trades Sunday evening through Friday afternoon with a daily
    maintenance break, which is why both the weekday set and the daily break
    are explicit rather than assumed.
    """

    name: str
    weekdays: frozenset[int] = frozenset({0, 1, 2, 3, 4, 6})
    #: Minutes of the UTC day that are expected to have no bars.
    daily_break_utc: tuple[int, int] | None = None
    holidays: frozenset[date] = frozenset()
    timezone_name: str = "UTC"

    def is_expected(self, moment: datetime) -> bool:
        if moment.date() in self.holidays:
            return False
        if moment.weekday() not in self.weekdays:
            return False
        if self.daily_break_utc is not None:
            start, end = self.daily_break_utc
            minute = moment.hour * 60 + moment.minute
            if start <= minute < end:
                return False
        return True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "weekdays": sorted(self.weekdays),
            "daily_break_utc": list(self.daily_break_utc) if self.daily_break_utc else None,
            "holiday_count": len(self.holidays),
            "timezone": self.timezone_name,
        }


@dataclass
class DatasetQualityReport:
    """The verdict on one contract/timeframe slice."""

    instrument: str
    contract: str
    timeframe: str
    provider: str
    rows: int
    date_range: tuple[datetime, datetime] | None
    missing_rows: int | None
    duplicate_rows: int
    invalid_rows: int
    timestamp_anomalies: int
    session_anomalies: int
    quality_status: QualityStatus
    findings: list[QualityFinding] = field(default_factory=list)
    session_spec: SessionSpec | None = None
    timezone_name: str = "UTC"
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))

    @property
    def fatal_findings(self) -> list[QualityFinding]:
        return [f for f in self.findings if f.is_fatal]

    @property
    def is_research_eligible(self) -> bool:
        return self.quality_status.opens_the_gate

    @property
    def completeness(self) -> float | None:
        """Fraction of expected bars present, or ``None`` if unknowable.

        ``None`` when no session spec was supplied. Reporting 1.0 in that case
        would claim completeness that was never measured.
        """
        if self.missing_rows is None:
            return None
        expected = self.rows + self.missing_rows
        return self.rows / expected if expected else 1.0

    def summary(self) -> str:
        completeness = self.completeness
        completeness_text = (f"{completeness:.4%}" if completeness is not None
                             else "unknown (no session spec)")
        return (
            f"{self.instrument}/{self.contract} {self.timeframe} via {self.provider}: "
            f"{self.rows:,} rows, completeness {completeness_text}, "
            f"status {self.quality_status.value}"
        )

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument,
            "contract": self.contract,
            "timeframe": self.timeframe,
            "provider": self.provider,
            "rows": self.rows,
            "date_range": ([self.date_range[0].isoformat(),
                            self.date_range[1].isoformat()]
                           if self.date_range else None),
            "missing_rows": self.missing_rows,
            "duplicate_rows": self.duplicate_rows,
            "invalid_rows": self.invalid_rows,
            "timestamp_anomalies": self.timestamp_anomalies,
            "session_anomalies": self.session_anomalies,
            "quality_status": self.quality_status.value,
            "completeness": self.completeness,
            "findings": [f.to_dict() for f in self.findings],
            "session_spec": self.session_spec.to_dict() if self.session_spec else None,
            "timezone": self.timezone_name,
            "generated_at": self.generated_at.isoformat(),
        }


def _examples(values: Sequence[object], limit: int = 5) -> tuple[str, ...]:
    return tuple(str(v) for v in list(values)[:limit])


def run_quality_gate(bars: Sequence[Bar], *, provider: str,
                     session_spec: SessionSpec | None = None,
                     timezone_name: str = "UTC") -> DatasetQualityReport:
    """Assess one contract/timeframe slice.

    Takes bars for a single ``(instrument, contract, timeframe)``. Mixing
    slices is rejected rather than averaged, because a report that blends a
    clean contract with a broken one describes neither.
    """
    if not bars:
        return DatasetQualityReport(
            instrument="", contract="", timeframe="", provider=provider,
            rows=0, date_range=None, missing_rows=None, duplicate_rows=0,
            invalid_rows=0, timestamp_anomalies=0, session_anomalies=0,
            quality_status=QualityStatus.REJECTED,
            findings=[QualityFinding(
                "non_empty", Severity.FATAL, 0,
                "the slice contains no bars; an empty dataset cannot be assessed and "
                "must not be treated as a clean one",
            )],
            timezone_name=timezone_name,
        )

    slices = {(b.instrument, b.contract, b.timeframe) for b in bars}
    if len(slices) > 1:
        raise ValueError(
            "run_quality_gate assesses one instrument/contract/timeframe slice; "
            f"received {len(slices)}: {sorted(slices)}. Blending slices produces a "
            "report that describes none of them."
        )
    instrument, contract, timeframe = next(iter(slices))
    findings: list[QualityFinding] = []

    # -- ordering ---------------------------------------------------------
    out_of_order = [
        b.event_time for previous, b in zip(bars, bars[1:])
        if b.event_time < previous.event_time
    ]
    if out_of_order:
        findings.append(QualityFinding(
            "chronological_order", Severity.FATAL, len(out_of_order),
            "bars are not in chronological order; every windowed feature computed "
            "over this slice would silently mix past and future",
            _examples(out_of_order),
        ))

    ordered = sorted(bars, key=lambda b: b.event_time)

    # -- duplicates -------------------------------------------------------
    counts = Counter(b.event_time for b in ordered)
    duplicates = {t: n for t, n in counts.items() if n > 1}
    duplicate_rows = sum(n - 1 for n in duplicates.values())
    if duplicate_rows:
        findings.append(QualityFinding(
            "duplicate_timestamps", Severity.FATAL, duplicate_rows,
            "the same bar timestamp appears more than once; volume and returns "
            "computed over this slice are double-counted at those points",
            _examples(sorted(duplicates)),
        ))

    # -- OHLC sanity ------------------------------------------------------
    impossible = [b.event_time for b in ordered if b.has_impossible_ohlc]
    if impossible:
        findings.append(QualityFinding(
            "impossible_ohlc", Severity.FATAL, len(impossible),
            "high < low, or open/close outside the high-low range; any stop or "
            "target resolved inside such a bar is fiction",
            _examples(impossible),
        ))

    negative_volume = [b.event_time for b in ordered if b.volume < 0]
    if negative_volume:
        findings.append(QualityFinding(
            "negative_volume", Severity.FATAL, len(negative_volume),
            "negative volume is not a quantity that exists",
            _examples(negative_volume),
        ))

    non_positive_price = [
        b.event_time for b in ordered
        if min(b.open, b.high, b.low, b.close) <= 0
    ]
    if non_positive_price:
        findings.append(QualityFinding(
            "non_positive_price", Severity.FATAL, len(non_positive_price),
            "a zero or negative price in an equity index future is a parse error, "
            "not a market event",
            _examples(non_positive_price),
        ))

    # -- timestamps -------------------------------------------------------
    epoch = datetime(1990, 1, 1, tzinfo=timezone.utc)
    horizon = datetime.now(timezone.utc) + timedelta(days=1)
    implausible = [b.event_time for b in ordered
                   if b.event_time < epoch or b.event_time > horizon]
    timestamp_anomalies = len(implausible)
    if implausible:
        findings.append(QualityFinding(
            "implausible_timestamp", Severity.FATAL, len(implausible),
            "timestamps outside a plausible range, which usually means an epoch "
            "unit was misread (seconds parsed as milliseconds or vice versa)",
            _examples(implausible),
        ))

    unaligned: list[datetime] = []
    spacing = TIMEFRAME_SECONDS.get(timeframe)
    if spacing:
        unaligned = [
            b.event_time for b in ordered
            if int(b.event_time.timestamp()) % spacing != 0
        ]
        if unaligned:
            findings.append(QualityFinding(
                "bar_alignment", Severity.WARNING, len(unaligned),
                f"bar timestamps not aligned to the {timeframe} grid; the slice may "
                "have been resampled from another timeframe",
                _examples(unaligned),
            ))
            timestamp_anomalies += len(unaligned)
    else:
        findings.append(QualityFinding(
            "known_timeframe", Severity.WARNING, 1,
            f"timeframe {timeframe!r} has no declared spacing, so bar spacing and "
            "missing bars cannot be checked",
        ))

    # -- availability ordering -------------------------------------------
    early_availability = [
        b.event_time for b in ordered if b.available_at < b.event_time
    ]
    if early_availability:
        findings.append(QualityFinding(
            "availability_precedes_event", Severity.FATAL,
            len(early_availability),
            "available_at precedes event_time, which grants the strategy a look at "
            "the future of every affected bar",
            _examples(early_availability),
        ))

    # -- session structure and missing bars -------------------------------
    missing_rows: int | None = None
    session_anomalies = 0
    if spacing and session_spec is not None:
        step = timedelta(seconds=spacing)
        expected: list[datetime] = []
        cursor = ordered[0].event_time
        last = ordered[-1].event_time
        while cursor <= last:
            if session_spec.is_expected(cursor):
                expected.append(cursor)
            cursor += step
        present = set(counts)
        absent = [t for t in expected if t not in present]
        missing_rows = len(absent)
        if absent:
            findings.append(QualityFinding(
                "missing_bars", Severity.WARNING, len(absent),
                f"{len(absent):,} bars expected by the {session_spec.name} session "
                "structure are absent",
                _examples(absent),
            ))

        unexpected = [t for t in present if not session_spec.is_expected(t)]
        session_anomalies = len(unexpected)
        if unexpected:
            findings.append(QualityFinding(
                "bars_outside_session", Severity.WARNING, len(unexpected),
                "bars present at times the declared session structure says the market "
                "is closed; either the session spec or the timezone is wrong",
                _examples(sorted(unexpected)),
            ))
    elif spacing:
        findings.append(QualityFinding(
            "session_structure", Severity.WARNING, 1,
            "no session spec supplied, so missing bars cannot be distinguished from "
            "normal market closures; missing_rows is reported as unknown rather "
            "than zero",
        ))

    invalid_rows = len(set(impossible) | set(negative_volume)
                       | set(non_positive_price))

    fatal = [f for f in findings if f.is_fatal]
    if fatal:
        status = QualityStatus.REJECTED
    elif any(f.severity is Severity.WARNING for f in findings):
        status = QualityStatus.ELIGIBLE_WITH_WARNINGS
    else:
        status = QualityStatus.RESEARCH_ELIGIBLE

    return DatasetQualityReport(
        instrument=instrument, contract=contract, timeframe=timeframe,
        provider=provider, rows=len(ordered),
        date_range=(ordered[0].event_time, ordered[-1].event_time),
        missing_rows=missing_rows, duplicate_rows=duplicate_rows,
        invalid_rows=invalid_rows, timestamp_anomalies=timestamp_anomalies,
        session_anomalies=session_anomalies, quality_status=status,
        findings=findings, session_spec=session_spec,
        timezone_name=timezone_name,
    )
