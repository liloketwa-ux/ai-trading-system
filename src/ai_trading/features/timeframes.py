"""Timeframe arithmetic and multi-timeframe safety.

The leak this module exists to prevent: reading a higher-timeframe bar before it
has closed. A 1H candle stamped 10:00 describes 10:00--11:00 and is not knowable
at 10:30, but it sits in the dataframe looking exactly like a completed bar. A
strategy that joins "the latest 1H bar" onto a 5m decision at 10:30 gets an hour
of future information and a very good backtest.

``df.iloc[-1]`` is the canonical way this happens. :func:`latest_completed_bar`
is the replacement: it takes a decision time and returns only bars whose
availability has actually arrived.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from ..storage.records import Observation, utc
from ..storage.store import ObservationStore
from .latency import DEFAULT_LATENCY, LatencyModel

__all__ = ["Timeframe", "TIMEFRAMES", "bar_close", "bar_available_at", "latest_completed_bar"]

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


@dataclass(frozen=True)
class Timeframe:
    """A bar duration with parsing and close arithmetic."""

    label: str

    @property
    def duration(self) -> timedelta:
        label = self.label.strip().lower()
        if len(label) < 2 or label[-1] not in _UNITS:
            raise ValueError(f"malformed timeframe: {self.label!r}")
        try:
            amount = int(label[:-1])
        except ValueError as exc:
            raise ValueError(f"malformed timeframe: {self.label!r}") from exc
        if amount <= 0:
            raise ValueError(f"timeframe must be positive: {self.label!r}")
        return timedelta(seconds=amount * _UNITS[label[-1]])

    def close_of(self, bar_open: datetime) -> datetime:
        return utc(bar_open) + self.duration

    def __str__(self) -> str:
        return self.label


TIMEFRAMES = {label: Timeframe(label) for label in ("1m", "5m", "15m", "1h", "4h", "1d")}


def bar_close(bar_open: datetime, timeframe: str) -> datetime:
    """When a bar opening at ``bar_open`` completes."""
    return Timeframe(timeframe).close_of(bar_open)


def bar_available_at(
    bar_open: datetime, timeframe: str, source: str = "*", latency: LatencyModel | None = None
) -> datetime:
    """When a completed bar becomes usable, including source latency.

    Availability is the bar's **close** plus whatever delay the source is
    modelled to have -- never the bar's open.
    """
    model = latency or DEFAULT_LATENCY
    return model.available_at(source, bar_close(bar_open, timeframe))


def latest_completed_bar(
    store: ObservationStore,
    instrument: str,
    timeframe: str,
    decision_time: datetime,
    *,
    kind: str = "ohlcv",
    strict: bool = True,
) -> Observation | None:
    """The most recent bar of ``timeframe`` whose availability has arrived.

    The safe replacement for ``df.iloc[-1]``. Returns ``None`` when no bar of
    that timeframe has completed yet, which callers must handle rather than
    substituting an incomplete bar.
    """
    moment = utc(decision_time)
    candidates = [
        o
        for o in store.query(moment, key=instrument, kind=kind, strict=strict)
        if o.timeframe == timeframe
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda o: o.event_time)


def completed_bars(
    store: ObservationStore,
    instrument: str,
    timeframe: str,
    decision_time: datetime,
    *,
    kind: str = "ohlcv",
    limit: int | None = None,
    strict: bool = True,
) -> list[Observation]:
    """All completed, available bars of one timeframe, oldest first."""
    bars = [
        o
        for o in store.query(decision_time, key=instrument, kind=kind, strict=strict)
        if o.timeframe == timeframe
    ]
    bars.sort(key=lambda o: o.event_time)
    return bars[-limit:] if limit else bars


def to_frame(bars: list[Observation]) -> pd.DataFrame:
    """OHLCV frame from bar observations, indexed by bar open."""
    if not bars:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    return pd.DataFrame(
        [
            {
                "open": b.value.get("open"),
                "high": b.value.get("high"),
                "low": b.value.get("low"),
                "close": b.value.get("close"),
                "volume": b.value.get("volume"),
            }
            for b in bars
        ],
        index=pd.DatetimeIndex([b.event_time for b in bars], name="timestamp"),
    )
