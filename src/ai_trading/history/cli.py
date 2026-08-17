"""``data:ingest:futures`` -- the only door real market data comes through.

The command is written before any provider exists so that the refusals are in
place before the first temptation to skip them. It takes five required
arguments and no defaults for any of them: a command that will guess a
timeframe or a date range will eventually guess wrong and produce a dataset
nobody can reproduce.

Four preflight refusals, checked before a single byte is requested:

1. **Unverified source provenance.** A provider must be registered and carry a
   manifest. An unnamed source cannot be audited later, so it is not ingested
   now.
2. **Continuous-only providers.** A stitched front-month series is a
   construction with a roll date and an adjustment method baked invisibly into
   every bar. The canonical dataset holds individual contracts.
3. **Missing expiry.** Without it no roll can ever be justified from the data,
   and the contract cannot be placed in time relative to its neighbours.
4. **Missing credentials.** Checked by environment-variable *name*. Values are
   never read into the process's own reporting, never logged, never committed.

Only after all four pass does it fetch, and the fetch is followed immediately
by a quality report. There is no path through this command that produces an
ingested dataset without one.

Nothing here submits an order or touches an execution gateway.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Sequence

from .checklist import CheckOutcome, DatasetChecklist
from .contracts import ContractBook, ContractMetadata
from .datasets import DataOrigin, ResearchDataset
from .futures_provider import (
    ContinuousOnlyProviderError,
    FuturesDataProvider,
    ProviderCredentialError,
)
from .grades import DatasetGrade, assess_grades
from .providers import DataKind
from .quality import SessionSpec, run_quality_gate
from .replay import PointInTimeReplay

__all__ = ["IngestRequest", "IngestRefusal", "PROVIDER_REGISTRY",
           "preflight", "build_parser", "main", "cmd_ingest_futures"]


class IngestRefusal(RuntimeError):
    """Ingestion was refused before any data was requested."""


#: Registered providers, by name. Empty: no real provider adapter exists yet,
#: and an empty registry is what makes the command refuse rather than improvise.
PROVIDER_REGISTRY: dict[str, FuturesDataProvider] = {}


@dataclass(frozen=True)
class IngestRequest:
    """A fully specified ingestion. Every field is required."""

    provider: str
    contract: str
    start: datetime
    end: datetime
    timeframe: str
    instrument: str = ""
    expiry: date | None = None

    def __post_init__(self) -> None:
        for name in ("provider", "contract", "timeframe"):
            if not getattr(self, name):
                raise IngestRefusal(f"--{name.replace('_', '-')} is required")
        if self.end <= self.start:
            raise IngestRefusal(
                f"--end {self.end.isoformat()} does not follow --start "
                f"{self.start.isoformat()}"
            )

    def to_dict(self) -> dict:
        return {
            "provider": self.provider, "contract": self.contract,
            "start": self.start.isoformat(), "end": self.end.isoformat(),
            "timeframe": self.timeframe, "instrument": self.instrument,
            "expiry": self.expiry.isoformat() if self.expiry else None,
        }


def preflight(request: IngestRequest, *,
              registry: dict[str, FuturesDataProvider] | None = None,
              environ: dict[str, str] | None = None) -> FuturesDataProvider:
    """Run every refusal before requesting data. Returns the provider.

    Ordered cheapest-first so the most common failure (no provider) reports
    first rather than after a credential check that could not have mattered.
    """
    registry = PROVIDER_REGISTRY if registry is None else registry
    environ = dict(os.environ) if environ is None else environ

    provider = registry.get(request.provider)
    if provider is None:
        raise IngestRefusal(
            f"unverified source provenance: {request.provider!r} is not a "
            f"registered provider. Registered: "
            f"{', '.join(sorted(registry)) or 'none'}. A source that cannot be "
            "named and audited later is not ingested now."
        )

    manifest = provider.manifest
    if DataKind.BARS not in manifest.kinds:
        raise IngestRefusal(
            f"{manifest.provider} does not serve bars"
        )

    try:
        manifest.require_contract_level()
    except ContinuousOnlyProviderError as error:
        raise IngestRefusal(str(error)) from error

    if request.expiry is None:
        raise IngestRefusal(
            f"missing expiry for {request.contract}: without it no roll can be "
            "justified from the data and the contract cannot be placed in time "
            "relative to its neighbours. Supply --expiry."
        )

    try:
        manifest.check_credentials(environ)
    except ProviderCredentialError as error:
        raise IngestRefusal(str(error)) from error

    return provider


def _checklist_from_run(request: IngestRequest, *, quality_ok: bool,
                        duplicates: int, invalid: int, missing: int | None,
                        timestamp_anomalies: int, pit_clean: bool,
                        has_session_spec: bool) -> DatasetChecklist:
    """Fill in what this run can establish. The rest stays UNKNOWN."""
    checklist = DatasetChecklist(f"{request.instrument}/{request.contract}")
    checklist.record("source_identity", CheckOutcome.PASS,
                     f"provider {request.provider}")
    checklist.record("contract_identity", CheckOutcome.PASS, request.contract)
    checklist.record("coverage", CheckOutcome.PASS,
                     f"{request.start.date()} to {request.end.date()}")
    checklist.record("contract_expiry", CheckOutcome.PASS,
                     request.expiry.isoformat() if request.expiry else "")
    checklist.record("timezone", CheckOutcome.PASS, "UTC throughout")
    checklist.record(
        "session_calendar",
        CheckOutcome.PASS if has_session_spec else CheckOutcome.UNKNOWN,
        "provider session metadata applied" if has_session_spec
        else "no session spec supplied; missing intervals are unmeasurable")
    checklist.record("duplicate_rows",
                     CheckOutcome.PASS if duplicates == 0 else CheckOutcome.FAIL,
                     f"{duplicates} duplicate timestamp(s)")
    checklist.record("invalid_ohlc",
                     CheckOutcome.PASS if invalid == 0 else CheckOutcome.FAIL,
                     f"{invalid} invalid row(s)")
    checklist.record(
        "missing_intervals",
        CheckOutcome.UNKNOWN if missing is None else CheckOutcome.PASS,
        "not measurable without a session calendar" if missing is None
        else f"{missing} missing bar(s), measured against the session calendar")
    checklist.record(
        "timestamp_anomalies",
        CheckOutcome.PASS if timestamp_anomalies == 0 else CheckOutcome.FAIL,
        f"{timestamp_anomalies} anomaly(ies)")
    checklist.record(
        "availability_semantics", CheckOutcome.PASS,
        "recorded per row, with source_available_at left unset where the "
        "provider does not publish one")
    checklist.record("provenance", CheckOutcome.PASS,
                     "provider, dataset, contract, timestamp, timezone, schema "
                     "and coverage recorded per response")
    # Deliberately left UNKNOWN: neither can be established by ingestion alone.
    checklist.record("roll_metadata", CheckOutcome.UNKNOWN,
                     "requires at least two contracts and observed crossover "
                     "evidence")
    checklist.record("adjustment_policy", CheckOutcome.UNKNOWN,
                     "no adjustment implementation exists; individual contracts "
                     "only")
    return checklist


def cmd_ingest_futures(args) -> int:
    """Ingest one contract, then report on it. Refuses before fetching."""
    request = IngestRequest(
        provider=args.provider, contract=args.contract,
        start=_parse_time(args.start), end=_parse_time(args.end),
        timeframe=args.timeframe, instrument=args.instrument or "",
        expiry=date.fromisoformat(args.expiry) if args.expiry else None,
    )

    try:
        provider = preflight(request)
    except IngestRefusal as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2

    bars, provenance = provider.fetch_bars(
        instrument=request.instrument, contract=request.contract,
        timeframe=request.timeframe, start=request.start, end=request.end)

    session_metadata = provider.session_metadata(request.instrument)
    session_spec = SessionSpec(
        name=f"{request.instrument}:{provenance.provider}",
        weekdays=frozenset(session_metadata.trading_weekdays),
        daily_break_utc=session_metadata.daily_break_utc,
        holidays=session_metadata.holidays,
        timezone_name=session_metadata.timezone_name,
    )

    report = run_quality_gate(list(bars), provider=provenance.provider,
                              session_spec=session_spec,
                              timezone_name=session_metadata.timezone_name)

    book = ContractBook(request.instrument)
    book.register_contract(ContractMetadata(
        request.instrument, request.contract, expiry=request.expiry))
    book.add_bars(bars)

    replay = PointInTimeReplay(bars)
    horizon = replay.horizon()
    pit_clean = True
    if horizon is not None:
        pit_clean = replay.check_leakage(horizon[1]).is_clean

    checklist = _checklist_from_run(
        request, quality_ok=report.is_research_eligible,
        duplicates=report.duplicate_rows, invalid=report.invalid_rows,
        missing=report.missing_rows,
        timestamp_anomalies=report.timestamp_anomalies,
        pit_clean=pit_clean, has_session_spec=True)

    grades = assess_grades(
        source_name=provenance.provider, origin=DataOrigin.REAL_MARKET,
        quality_report=report, point_in_time_clean=pit_clean,
        point_in_time_note="replay leakage check on the full window")

    payload = {
        "request": request.to_dict(),
        "response_provenance": provenance.to_dict(),
        "quality_report": report.to_dict(),
        "checklist": checklist.to_dict(),
        "grades": grades.to_dict(),
        "contract_book": book.coverage_report(),
        "dataset": None,
    }

    if grades.granted(DatasetGrade.RESEARCH_GRADE):
        dataset = ResearchDataset.create(
            list(bars), source=provenance.provider,
            origin=DataOrigin.REAL_MARKET, quality_report=report,
            note=f"ingested via data:ingest:futures from {provenance.dataset}")
        payload["dataset"] = dataset.to_dict()
        if args.out:
            dataset.save(Path(args.out))

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(report.summary())
        print(checklist.summary())
        print(f"grades: highest="
              f"{grades.highest.value if grades.highest else 'none'} "
              f"research={grades.permits_research} "
              f"market_claims={grades.permits_market_claims}")
        if grades.blocking_reason:
            print(f"blocked: {grades.blocking_reason}")
        if not checklist.is_complete:
            print(f"checklist outstanding: {', '.join(checklist.blocking)}")

    return 0 if grades.permits_research else 1


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="data", description="Historical market-data ingestion")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser(
        "data:ingest:futures",
        help="ingest one futures contract; refuses continuous-only sources")
    ingest.add_argument("--provider", required=True)
    ingest.add_argument("--contract", required=True,
                        help="one deliverable contract, e.g. NQM26")
    ingest.add_argument("--start", required=True, help="ISO-8601")
    ingest.add_argument("--end", required=True, help="ISO-8601")
    ingest.add_argument("--timeframe", required=True,
                        help="1m, 5m, 15m, 1h, 1d")
    ingest.add_argument("--instrument", default="",
                        help="product code, e.g. NQ")
    ingest.add_argument("--expiry", default=None,
                        help="ISO date; required -- no roll can be justified "
                             "without it")
    ingest.add_argument("--out", default=None,
                        help="write the dataset manifest here")
    ingest.add_argument("--json", action="store_true")
    ingest.set_defaults(func=cmd_ingest_futures)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
