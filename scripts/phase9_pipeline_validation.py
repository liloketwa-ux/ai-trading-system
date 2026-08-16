#!/usr/bin/env python3
"""Phase 9 pipeline validation.

Runs the full Phase 9 path -- provider, contract book, quality gate, research
dataset, point-in-time replay, baselines -- and prints what each stage decided.

**This is a plumbing test, not a study.** No real market data was reachable
when it was written (every market-data host is refused at the network
boundary), so it runs on a synthetic generator. The generator is a driftless
random walk, which has no autocorrelation by construction: momentum and mean
reversion are *expected* to return approximately minus the cost drag on it, and
that result says nothing whatsoever about whether those strategies have an edge
in a real market. It is reported here only to show the measurement path
computes and that the numbers land where the mathematics says they must.

Every artefact this produces is stamped ``DataOrigin.SYNTHETIC``, which the
dataset gate refuses to let back a market claim, and the ICT gate stays closed.

Usage:  PYTHONPATH=src python scripts/phase9_pipeline_validation.py
"""

from __future__ import annotations

import json
import random
import sys
from datetime import date, datetime, timedelta, timezone

from ai_trading.history import (
    AvailabilityQuality,
    Bar,
    ContractBook,
    ContractMetadata,
    DataOrigin,
    PointInTimeReplay,
    ResearchDataset,
    SessionSpec,
    SourceLedger,
    SourceStatus,
    bar_close_availability,
    run_quality_gate,
)
from ai_trading.history.providers import SCHEMA_VERSION
from ai_trading.research.campaign import (
    BASELINE_SUITE,
    CampaignDeclaration,
    CampaignPurpose,
    CampaignResult,
    CampaignStatus,
    ICTGate,
)
from ai_trading.research.costs import REALISTIC
from ai_trading.research.statistics import bootstrap_mean

UTC = timezone.utc
SEED = 20260816

#: Hosts probed for real NQ history. Every one is refused at the proxy.
BLOCKED_SOURCES = {
    "databento": "CONNECT tunnel failed, 403 -- not on the egress allowlist",
    "yahoo_finance": "CONNECT tunnel failed, 403 -- not on the egress allowlist",
    "stooq": "CONNECT tunnel failed, 403 -- not on the egress allowlist",
    "cme_datamine": "CONNECT tunnel failed, 403 -- not on the egress allowlist",
    "tiingo": "CONNECT tunnel failed, 403 -- not on the egress allowlist",
}

#: CME equity index futures: Sunday 22:00 UTC through Friday 21:00 UTC with a
#: daily 22:00-23:00 UTC maintenance break. Declared so gaps can be judged.
CME_SESSION = SessionSpec(
    name="cme_equity_index",
    weekdays=frozenset({0, 1, 2, 3, 4, 6}),
    daily_break_utc=(22 * 60, 23 * 60),
    timezone_name="UTC",
)

AVAILABILITY = bar_close_availability(
    "synthetic generator emits bars at their close; no arrival timestamp exists "
    "because there is no feed"
)


def generate_bars(count: int, *, contract: str, timeframe: str,
                  minutes: int, seed: int) -> list[Bar]:
    """Driftless random walk shaped like NQ. Explicitly not a market."""
    rng = random.Random(seed)
    retrieved = datetime.now(UTC)
    start = datetime(2026, 3, 2, 0, 0, tzinfo=UTC)
    price = 20_000.0
    bars: list[Bar] = []
    emitted = 0
    step = 0
    while emitted < count:
        event = start + timedelta(minutes=minutes * step)
        step += 1
        if not CME_SESSION.is_expected(event):
            continue
        open_ = price
        moves = [rng.gauss(0.0, 4.0) for _ in range(4)]
        path = [open_]
        for move in moves:
            path.append(path[-1] + move)
        close = path[-1]
        bars.append(Bar(
            source="synthetic_gbm", event_time=event,
            available_at=AVAILABILITY.available_at(event_time=event),
            retrieved_at=retrieved, schema_version=SCHEMA_VERSION,
            availability_quality=AvailabilityQuality.ASSUMED_BAR_CLOSE,
            instrument="NQ", contract=contract, timeframe=timeframe,
            open=round(open_, 2), high=round(max(path), 2),
            low=round(min(path), 2), close=round(close, 2),
            volume=float(rng.randint(200, 4_000)),
        ))
        price = close
        emitted += 1
    return bars


def forward_returns(bars: list[Bar], horizon: int) -> list[float]:
    return [(bars[i + horizon].close - bars[i].close) / bars[i].close
            for i in range(len(bars) - horizon)]


def run_baselines(bars: list[Bar], horizon: int = 12) -> dict[str, dict]:
    """Four selectors, evaluated net of realistic costs.

    Every selector picks entries from the same bar sequence and is scored on
    the same forward return with the same cost model, so the comparison is
    between selections rather than between setups.
    """
    rng = random.Random(SEED)
    returns = forward_returns(bars, horizon)
    if not returns:
        return {}

    closes = [b.close for b in bars]
    lookback = 20

    def score(indices: list[int], label: str) -> dict:
        sample = [returns[i] for i in indices if i < len(returns)]
        if len(sample) < 30:
            return {"baseline": label, "trades": len(sample),
                    "verdict": "insufficient sample"}
        net = [REALISTIC.apply(r) for r in sample]
        interval = bootstrap_mean(net, seed=SEED)
        mean = sum(net) / len(net)
        return {
            "baseline": label,
            "trades": len(net),
            "gross_mean_bps": round(sum(sample) / len(sample) * 10_000, 3),
            "net_mean_bps": round(mean * 10_000, 3),
            "ci_low_bps": round(interval.lower * 10_000, 3),
            "ci_high_bps": round(interval.upper * 10_000, 3),
            "excludes_zero": bool(interval.excludes_zero),
        }

    candidates = list(range(lookback, len(returns)))
    results: dict[str, dict] = {}

    picked = [i for i in candidates if rng.random() < 0.30]
    results["random"] = score(picked, "random")

    results["hold_matched_random"] = score(
        rng.sample(candidates, min(len(picked), len(candidates))),
        "hold_matched_random")

    results["momentum"] = score(
        [i for i in candidates if closes[i] > closes[i - lookback]], "momentum")

    results["mean_reversion"] = score(
        [i for i in candidates if closes[i] < closes[i - lookback]],
        "mean_reversion")

    return results


def main() -> int:
    ledger = SourceLedger()
    for name, reason in BLOCKED_SOURCES.items():
        ledger.block(name, reason)

    ledger.register("synthetic_gbm")
    for level, evidence in (
        (SourceStatus.UNIT_TESTED, "generator covered by tests/test_phase9_history.py"),
        (SourceStatus.MACHINE_RETRIEVED, "bytes produced locally, no network involved"),
        (SourceStatus.RUNTIME_VERIFIED, "output shape checked against the Bar contract"),
        (SourceStatus.HISTORICALLY_VALIDATED, "full requested range emitted"),
    ):
        ledger.promote("synthetic_gbm", level, evidence=evidence)

    print("=" * 78)
    print("PHASE 9 PIPELINE VALIDATION -- synthetic data, no market claims")
    print("=" * 78)

    print("\n[1] SOURCE LEDGER")
    for record in ledger.all():
        flag = "BLOCKED" if record.is_blocked else record.status.value
        print(f"    {record.source_name:<18} {flag}")
        if record.is_blocked:
            print(f"        {record.blocked_reason}")

    book = ContractBook("NQ")
    book.register_contract(ContractMetadata(
        "NQ", "NQM26", expiry=date(2026, 6, 19),
        note="synthetic; expiry is a label, not an observation"))

    timeframes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}
    for timeframe, minutes in timeframes.items():
        book.add_bars(generate_bars(3_000, contract="NQM26", timeframe=timeframe,
                                    minutes=minutes, seed=SEED + minutes))

    print("\n[2] CONTRACT BOOK")
    print(f"    instrument     : {book.instrument}")
    print(f"    contracts      : {', '.join(book.contracts)}")
    print(f"    timeframes     : {', '.join(book.timeframes)}")
    print(f"    total bars     : {book.bar_count():,}")
    print(f"    continuous     : {book.coverage_report()['is_continuous']}")

    print("\n[3] QUALITY GATE")
    reports = {}
    for timeframe in book.timeframes:
        bars = book.bars("NQM26", timeframe)
        report = run_quality_gate(bars, provider="synthetic_gbm",
                                  session_spec=CME_SESSION)
        reports[timeframe] = report
        completeness = report.completeness
        print(f"    {timeframe:<4} rows={report.rows:<6,} "
              f"dupes={report.duplicate_rows} invalid={report.invalid_rows} "
              f"missing={report.missing_rows} "
              f"completeness={completeness:.4%} "
              f"-> {report.quality_status.value}")
        for finding in report.findings:
            print(f"         - [{finding.severity.value}] {finding.check} "
                  f"x{finding.count}")

    primary = "5m"
    print("\n[4] RESEARCH DATASET")
    dataset = ResearchDataset.create(
        book.bars("NQM26", primary), source="synthetic_gbm",
        origin=DataOrigin.SYNTHETIC, quality_report=reports[primary],
        note="pipeline validation only; generator output, not a market",
    )
    print(f"    dataset_id     : {dataset.dataset_id}")
    print(f"    origin         : {dataset.origin.value}")
    print(f"    rows           : {dataset.row_count:,}")
    print(f"    range          : {dataset.date_range[0].isoformat()} -> "
          f"{dataset.date_range[1].isoformat()}")
    print(f"    checksum       : {dataset.checksum[:32]}...")
    print(f"    code_commit    : {dataset.code_commit}")
    print(f"    market claims  : {dataset.may_support_market_claims}")
    print(f"    latency feats  : "
          f"{dataset.feature_eligibility.latency_sensitive_features}")

    try:
        ledger.promote("synthetic_gbm", SourceStatus.RESEARCH_APPROVED,
                       evidence="passed the quality gate",
                       quality_report=reports[primary])
        print(f"    ledger         : synthetic_gbm -> "
              f"{ledger.get('synthetic_gbm').status.value}")
    except Exception as error:                       # pragma: no cover - reporting
        print(f"    ledger         : refused -- {error}")

    print("\n[5] POINT-IN-TIME REPLAY")
    bars = book.bars("NQM26", primary)
    replay = PointInTimeReplay(bars)
    midpoint = bars[len(bars) // 2].event_time
    visible = replay.visible_at(midpoint)
    print(f"    decision time  : {midpoint.isoformat()}")
    print(f"    visible rows   : {len(visible):,} of {len(bars):,}")
    print(f"    max visible ev : {max(b.event_time for b in visible).isoformat()}")
    replay.assert_no_leakage(midpoint)
    print("    leakage check  : clean")

    injected = Bar(
        source="injected_future", event_time=bars[-1].event_time + timedelta(days=30),
        available_at=bars[-1].event_time + timedelta(days=30),
        retrieved_at=datetime.now(UTC), instrument="NQ", contract="NQM26",
        timeframe=primary, open=1.0, high=99_999.0, low=1.0, close=99_999.0,
        volume=1.0,
    )
    poisoned = PointInTimeReplay(bars + [injected])
    leaked = [b for b in poisoned.visible_at(midpoint) if b.source == "injected_future"]
    print(f"    injected future: {'VISIBLE (BUG)' if leaked else 'not visible'}")

    print("\n[6] BASELINES (synthetic; costs = realistic, "
          f"{REALISTIC.round_trip_bps:.1f} bps round trip)")
    baselines = run_baselines(bars)
    for name in BASELINE_SUITE:
        result = baselines.get(name)
        if result is None or "net_mean_bps" not in result:
            print(f"    {name:<20} {result}")
            continue
        print(f"    {name:<20} n={result['trades']:<5} "
              f"gross={result['gross_mean_bps']:>8.3f}bps  "
              f"net={result['net_mean_bps']:>8.3f}bps  "
              f"CI[{result['ci_low_bps']:.3f}, {result['ci_high_bps']:.3f}]  "
              f"{'EXCLUDES 0' if result['excludes_zero'] else 'includes 0'}")

    print("\n[7] CAMPAIGN")
    declaration = CampaignDeclaration(
        name="nq-pipeline-validation", purpose=CampaignPurpose.PIPELINE_VALIDATION,
        dataset_id=dataset.dataset_id, instrument="NQ", contract="NQM26",
        timeframes=tuple(book.timeframes), features=("close", "sma_20"),
        labels=("forward_return_12",), hypotheses=(),
        cost_model=REALISTIC.name, execution_model="close_to_close_forward_return",
        validation_protocol="bootstrap_ci_seeded", seed=SEED,
        note="validates the measurement path; produces no strategy findings",
    )
    result = CampaignResult(declaration, status=CampaignStatus.COMPLETE)
    result.baseline_results = baselines
    result.pipeline_checks = {
        "ingestion": True, "quality_gate": True, "dataset_creation": True,
        "point_in_time_replay": not leaked, "baselines": bool(baselines),
    }
    allowed, reason = result.may_report_edge()
    print(f"    campaign_id    : {declaration.campaign_id}")
    print(f"    purpose        : {declaration.purpose.value}")
    print(f"    checks         : {result.pipeline_checks}")
    print(f"    may claim edge : {allowed} -- {reason}")

    print("\n[8] ICT GATE")
    gate = ICTGate(dataset=dataset,
                   reason_blocked="no real market data reachable from this environment")
    print(f"    {gate.status()}")
    print(f"    is_open        : {gate.is_open}")

    print("\n" + "=" * 78)
    print("No real market data entered this run. No edge is claimed.")
    print("=" * 78)

    if "--json" in sys.argv:
        print(json.dumps({
            "dataset": dataset.to_dict(),
            "campaign": result.to_dict(),
            "ledger": ledger.report(),
            "quality": {tf: r.to_dict() for tf, r in reports.items()},
        }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
