"""Data quality gate — validate before any backtest runs.

Backtesting corrupt data is worse than not backtesting: it produces a number,
and numbers get believed. This gate runs before research and reports every
defect it can detect, with a severity that decides whether the run proceeds.

Checks: missing bars against the expected grid, duplicate timestamps,
out-of-order rows, zero-volume bars, price spikes beyond N sigma, weekend and
session gaps, OHLC self-consistency, and staleness of the newest bar.

Weekend gaps are reported as INFO for instruments that legitimately close, so
an equities or futures series is not flagged as broken every Saturday.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import IntEnum

import numpy as np
import pandas as pd

__all__ = ["Severity", "QualityIssue", "QualityReport", "check_quality", "QualityGateError"]


class QualityGateError(RuntimeError):
    """Raised when data fails the gate at the configured threshold."""


class Severity(IntEnum):
    INFO = 10
    WARNING = 20
    CRITICAL = 30

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class QualityIssue:
    kind: str
    severity: Severity
    message: str
    count: int = 0
    examples: list = field(default_factory=list)

    def __str__(self) -> str:
        return f"[{self.severity}] {self.kind}: {self.message}"


@dataclass
class QualityReport:
    """Outcome of a quality check."""

    symbol: str
    timeframe: str
    rows: int
    start: datetime | None
    end: datetime | None
    issues: list[QualityIssue] = field(default_factory=list)

    @property
    def worst(self) -> Severity | None:
        return max((i.severity for i in self.issues), default=None)

    @property
    def ok(self) -> bool:
        """True when nothing CRITICAL was found."""
        worst = self.worst
        return worst is None or worst < Severity.CRITICAL

    def of(self, kind: str) -> list[QualityIssue]:
        return [i for i in self.issues if i.kind == kind]

    def raise_if_failed(self, threshold: Severity = Severity.CRITICAL) -> None:
        """Fail loudly rather than let a research run proceed on bad data."""
        bad = [i for i in self.issues if i.severity >= threshold]
        if bad:
            detail = "; ".join(str(i) for i in bad)
            raise QualityGateError(f"{self.symbol} {self.timeframe}: {detail}")

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for issue in self.issues:
            counts[str(issue.severity)] = counts.get(str(issue.severity), 0) + 1
        tally = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "clean"
        return f"{self.symbol} {self.timeframe}: {self.rows} rows, {tally}"


def check_quality(
    bars: pd.DataFrame,
    *,
    symbol: str = "?",
    timeframe: str = "?",
    expected_interval: timedelta | None = None,
    spike_sigma: float = 10.0,
    max_staleness: timedelta | None = None,
    now: datetime | None = None,
    session_gaps_expected: bool = True,
) -> QualityReport:
    """Validate an OHLCV frame.

    Args:
        bars: Frame indexed by a ``DatetimeIndex`` with OHLC(V) columns.
        expected_interval: Bar spacing. Gaps are only reported when supplied.
        spike_sigma: Return z-score beyond which a bar is flagged as a spike.
        max_staleness: Age of the newest bar beyond which data is stale.
        now: Injectable clock for deterministic tests.
        session_gaps_expected: When True, weekend gaps are INFO rather than
            WARNING — a closed market is not corrupt data.
    """
    issues: list[QualityIssue] = []
    now = now or datetime.now(timezone.utc)

    if not isinstance(bars.index, pd.DatetimeIndex):
        raise TypeError("bars must be indexed by a DatetimeIndex")

    if bars.empty:
        return QualityReport(symbol, timeframe, 0, None, None,
                             [QualityIssue("empty", Severity.CRITICAL, "no rows")])

    missing_cols = [c for c in ("open", "high", "low", "close") if c not in bars.columns]
    if missing_cols:
        return QualityReport(
            symbol, timeframe, len(bars), None, None,
            [QualityIssue("schema", Severity.CRITICAL, f"missing columns: {missing_cols}")],
        )

    index = bars.index

    # -- ordering and duplicates -------------------------------------------
    if not index.is_monotonic_increasing:
        issues.append(QualityIssue("out_of_order", Severity.CRITICAL,
                                   "timestamps are not sorted ascending"))

    duplicates = index[index.duplicated()]
    if len(duplicates):
        issues.append(QualityIssue(
            "duplicate_timestamps", Severity.CRITICAL,
            f"{len(duplicates)} duplicate timestamps",
            len(duplicates), list(duplicates[:5]),
        ))

    # -- OHLC self-consistency ---------------------------------------------
    bad_range = bars[bars["high"] < bars["low"]]
    if len(bad_range):
        issues.append(QualityIssue("high_below_low", Severity.CRITICAL,
                                   f"{len(bad_range)} bars with high < low",
                                   len(bad_range), list(bad_range.index[:5])))

    outside = bars[
        (bars["open"] > bars["high"]) | (bars["open"] < bars["low"])
        | (bars["close"] > bars["high"]) | (bars["close"] < bars["low"])
    ]
    if len(outside):
        issues.append(QualityIssue("ohlc_inconsistent", Severity.CRITICAL,
                                   f"{len(outside)} bars with open/close outside the high-low range",
                                   len(outside), list(outside.index[:5])))

    nonpositive = bars[(bars[["open", "high", "low", "close"]] <= 0).any(axis=1)]
    if len(nonpositive):
        issues.append(QualityIssue("nonpositive_price", Severity.CRITICAL,
                                   f"{len(nonpositive)} bars with a non-positive price",
                                   len(nonpositive), list(nonpositive.index[:5])))

    nulls = int(bars[["open", "high", "low", "close"]].isna().sum().sum())
    if nulls:
        issues.append(QualityIssue("null_prices", Severity.CRITICAL,
                                   f"{nulls} null price values", nulls))

    # -- gaps ---------------------------------------------------------------
    if expected_interval is not None and len(index) > 1:
        deltas = pd.Series(index[1:]) - pd.Series(index[:-1])
        gaps = deltas[deltas > expected_interval]
        weekend, weekday = [], []
        for position, gap in gaps.items():
            starts_at = index[position]
            # Friday/Saturday starts are the normal weekend close.
            (weekend if starts_at.weekday() >= 4 else weekday).append(starts_at)

        if weekday:
            missing = int(sum(
                (index[index.get_indexer([t])[0] + 1] - t) / expected_interval - 1
                for t in weekday
            ))
            issues.append(QualityIssue(
                "missing_bars", Severity.WARNING,
                f"{len(weekday)} intra-week gaps, roughly {missing} bars missing",
                len(weekday), weekday[:5],
            ))
        if weekend:
            issues.append(QualityIssue(
                "weekend_gap",
                Severity.INFO if session_gaps_expected else Severity.WARNING,
                f"{len(weekend)} weekend/session gaps",
                len(weekend), weekend[:5],
            ))

    # -- volume -------------------------------------------------------------
    if "volume" in bars.columns:
        zero_volume = bars[bars["volume"] <= 0]
        if len(zero_volume):
            fraction = len(zero_volume) / len(bars)
            issues.append(QualityIssue(
                "zero_volume",
                Severity.WARNING if fraction > 0.01 else Severity.INFO,
                f"{len(zero_volume)} zero-volume bars ({fraction:.1%})",
                len(zero_volume), list(zero_volume.index[:5]),
            ))

    # -- spikes -------------------------------------------------------------
    if len(bars) > 30:
        returns = bars["close"].pct_change().dropna()
        sigma = returns.std(ddof=1)
        if sigma > 0:
            z = (returns - returns.mean()).abs() / sigma
            spikes = z[z > spike_sigma]
            if len(spikes):
                issues.append(QualityIssue(
                    "price_spike", Severity.WARNING,
                    f"{len(spikes)} returns beyond {spike_sigma} sigma (max {z.max():.1f})",
                    len(spikes), list(spikes.index[:5]),
                ))

    # -- staleness ----------------------------------------------------------
    newest = index[-1]
    if newest.tzinfo is None:
        newest = newest.tz_localize("UTC")
    if max_staleness is not None:
        age = now - newest.to_pydatetime()
        if age > max_staleness:
            issues.append(QualityIssue("stale_data", Severity.CRITICAL,
                                       f"newest bar is {age} old, limit {max_staleness}"))

    # -- availability (AD-2) ------------------------------------------------
    if "available_at" in bars.columns:
        available = pd.to_datetime(bars["available_at"], utc=True)
        opens = index.tz_localize("UTC") if index.tz is None else index
        early = bars[available < pd.Series(opens, index=bars.index)]
        if len(early):
            issues.append(QualityIssue(
                "available_before_event", Severity.CRITICAL,
                f"{len(early)} bars claim availability before their own open — look-ahead",
                len(early), list(early.index[:5]),
            ))

    return QualityReport(symbol, timeframe, len(bars), index[0].to_pydatetime(),
                         index[-1].to_pydatetime(), issues)
