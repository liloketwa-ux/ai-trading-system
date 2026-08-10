"""Aggregation of per-document sentiment into per-bar features.

Raw sentiment arrives as individual scored documents (tweets, headlines) with
timestamps. Strategies need it aligned to the price bar grid. These helpers do
that alignment while preserving the causality guarantee: a bar timestamped ``t``
may only see documents published at or before ``t``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def aggregate_to_bars(
    docs: pd.DataFrame,
    bar_index: pd.DatetimeIndex,
    *,
    score_col: str = "sentiment_score",
    time_col: str = "timestamp_utc",
) -> pd.DataFrame:
    """Aggregate scored documents onto a bar grid.

    Each document is assigned to the first bar at or after its timestamp, so a
    document published mid-bar is only visible from that bar's close onward.

    Returns a frame indexed by ``bar_index`` with columns:
    ``doc_count``, ``sentiment_mean``, ``sentiment_sum``, ``positive_count``,
    ``negative_count``, ``net_sentiment`` (positive minus negative count).

    Bars with no documents get zero counts and ``NaN`` mean.
    """
    if not isinstance(bar_index, pd.DatetimeIndex):
        raise TypeError("bar_index must be a DatetimeIndex")
    if not bar_index.is_monotonic_increasing:
        raise ValueError("bar_index must be sorted ascending")

    empty = pd.DataFrame(
        {
            "doc_count": np.zeros(len(bar_index), dtype=float),
            "sentiment_mean": np.full(len(bar_index), np.nan),
            "sentiment_sum": np.zeros(len(bar_index), dtype=float),
            "positive_count": np.zeros(len(bar_index), dtype=float),
            "negative_count": np.zeros(len(bar_index), dtype=float),
            "net_sentiment": np.zeros(len(bar_index), dtype=float),
        },
        index=bar_index,
    )
    if docs.empty:
        return empty

    missing = {score_col, time_col} - set(docs.columns)
    if missing:
        raise KeyError(f"docs is missing required column(s): {sorted(missing)}")

    times = pd.to_datetime(docs[time_col])
    scores = pd.to_numeric(docs[score_col], errors="coerce")
    frame = pd.DataFrame({"t": times, "s": scores}).dropna(subset=["t"]).sort_values("t")
    if frame.empty:
        return empty

    # searchsorted with side="left" maps a doc at exactly a bar time to that bar,
    # and any doc after it to the NEXT bar -- never an earlier one.
    positions = bar_index.searchsorted(frame["t"].to_numpy(), side="left")
    inside = positions < len(bar_index)
    frame = frame.loc[inside]
    if frame.empty:
        return empty
    frame["bar"] = bar_index[positions[inside]]

    grouped = frame.groupby("bar")["s"]
    out = empty.copy()
    out["doc_count"] = grouped.count().reindex(bar_index).fillna(0.0)
    out["sentiment_mean"] = grouped.mean().reindex(bar_index)
    out["sentiment_sum"] = grouped.sum().reindex(bar_index).fillna(0.0)
    out["positive_count"] = (
        frame[frame["s"] > 0].groupby("bar")["s"].count().reindex(bar_index).fillna(0.0)
    )
    out["negative_count"] = (
        frame[frame["s"] < 0].groupby("bar")["s"].count().reindex(bar_index).fillna(0.0)
    )
    out["net_sentiment"] = out["positive_count"] - out["negative_count"]
    return out


def time_decayed(series: pd.Series, half_life: float) -> pd.Series:
    """Exponentially time-decayed running sum, emphasizing recent values.

    ``half_life`` is expressed in bars: a value contributes half as much after
    that many bars have passed. Uses a causal EWM, so no future leakage.
    """
    if half_life <= 0:
        raise ValueError(f"half_life must be > 0, got {half_life}")
    return series.fillna(0.0).ewm(halflife=half_life, adjust=False).mean()


def hype_score(doc_count: pd.Series, window: int = 24) -> pd.Series:
    """Mention-volume surge: current document count vs. its trailing average.

    Returns a ratio where 1.0 is "normal" chatter and values well above 1.0
    indicate a spike. ``NaN`` until the window fills; ``NaN`` where the trailing
    average is zero (no baseline to compare against).
    """
    if window < 1:
        raise ValueError(f"window must be >= 1, got {window}")
    # shift(1) so the baseline excludes the current bar -- comparing a bar to a
    # window that contains it would dampen exactly the spikes we want to catch.
    baseline = doc_count.rolling(window).mean().shift(1)
    return doc_count.divide(baseline).where(baseline > 0.0)
