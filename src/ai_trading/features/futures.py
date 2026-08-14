"""Deterministic futures features, computed point-in-time.

Every function takes a ``decision_time`` and reads inputs only through the
store's validated APIs. None of them accept a DataFrame and index off the end:
``df.iloc[-1]`` is precisely how an unclosed higher-timeframe bar gets consumed
as if it were complete.

Objective observations only. Swing pivots, structure state and displacement are
measurements, not interpretations -- no entry logic, no "smart money" labelling.
Prior levels are named as *candidate liquidity references* because that is what
they are: prices where resting orders plausibly sit.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from ..storage.features import FeatureSnapshot, derive_feature
from ..storage.quality import AvailabilityRule, DataQuality
from ..storage.records import Observation, utc
from ..storage.store import ObservationStore
from .sessions import SessionDefinition
from .timeframes import completed_bars

__all__ = [
    "true_range", "atr", "bar_return", "gap", "realized_volatility",
    "range_expansion", "swings", "structure_state", "trend_state",
    "break_of_structure", "displacement", "session_vwap", "vwap_distance",
    "previous_period_level", "liquidity_references", "missing",
]

SOURCE = "features/futures"


def missing(name: str, decision_time: datetime, reason: DataQuality, *,
            instrument: str = "", timeframe: str | None = None,
            version: str = "1") -> FeatureSnapshot:
    """A feature that could not be computed, with the reason preserved.

    Returning this instead of ``0.0`` or ``NaN`` is the whole point: the caller
    can tell "no data" from "a value of zero".
    """
    moment = utc(decision_time)
    return FeatureSnapshot(
        name=name, value=None, event_time=moment, available_at=moment,
        source=SOURCE, feature_version=version, instrument=instrument,
        timeframe=timeframe, data_quality=reason,
    )


def _bars(store, instrument, timeframe, decision_time, limit=None, strict=True):
    return completed_bars(store, instrument, timeframe, decision_time,
                          limit=limit, strict=strict)


def _closes(bars) -> np.ndarray:
    return np.array([b.value.get("close") for b in bars], dtype="float64")


def _derive(name, bars, compute, *, instrument, timeframe, version="1",
            rule=AvailabilityRule.BAR_CLOSE, quality=DataQuality.OK):
    """Build a snapshot through the central constructor.

    ``available_at`` is never assigned by hand -- ``derive_feature`` takes the
    maximum over the inputs, which for bars is the latest bar's close.
    """
    snapshot = derive_feature(name, list(bars), compute, source=SOURCE,
                              feature_version=version)
    from dataclasses import replace
    return replace(snapshot, instrument=instrument, timeframe=timeframe,
                   availability_rule=rule, data_quality=quality, provenance_id="")


# -- price / volatility ----------------------------------------------------


def true_range(store, instrument, timeframe, decision_time, **kw) -> FeatureSnapshot:
    bars = _bars(store, instrument, timeframe, decision_time, limit=2, **kw)
    if len(bars) < 2:
        return missing("true_range", decision_time, DataQuality.MISSING,
                       instrument=instrument, timeframe=timeframe)

    def compute(values):
        prev, current = values[0], values[1]
        high, low, prev_close = current["high"], current["low"], prev["close"]
        return max(high - low, abs(high - prev_close), abs(low - prev_close))

    return _derive("true_range", bars, compute, instrument=instrument, timeframe=timeframe)


def atr(store, instrument, timeframe, decision_time, window=14, **kw) -> FeatureSnapshot:
    """Wilder-smoothed ATR over completed bars only."""
    bars = _bars(store, instrument, timeframe, decision_time, limit=window + 1, **kw)
    if len(bars) < window + 1:
        return missing("atr", decision_time, DataQuality.MISSING,
                       instrument=instrument, timeframe=timeframe)

    def compute(values):
        ranges = []
        for prev, current in zip(values[:-1], values[1:]):
            high, low, prev_close = current["high"], current["low"], prev["close"]
            ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        smoothed = ranges[0]
        alpha = 1.0 / window
        for value in ranges[1:]:
            smoothed = smoothed + alpha * (value - smoothed)
        return float(smoothed)

    return _derive("atr", bars, compute, instrument=instrument, timeframe=timeframe)


def bar_return(store, instrument, timeframe, decision_time, **kw) -> FeatureSnapshot:
    bars = _bars(store, instrument, timeframe, decision_time, limit=2, **kw)
    if len(bars) < 2:
        return missing("bar_return", decision_time, DataQuality.MISSING,
                       instrument=instrument, timeframe=timeframe)
    return _derive("bar_return", bars,
                   lambda v: v[1]["close"] / v[0]["close"] - 1.0,
                   instrument=instrument, timeframe=timeframe)


def gap(store, instrument, timeframe, decision_time, **kw) -> FeatureSnapshot:
    """This bar's open versus the previous bar's close."""
    bars = _bars(store, instrument, timeframe, decision_time, limit=2, **kw)
    if len(bars) < 2:
        return missing("gap", decision_time, DataQuality.MISSING,
                       instrument=instrument, timeframe=timeframe)
    return _derive("gap", bars, lambda v: v[1]["open"] / v[0]["close"] - 1.0,
                   instrument=instrument, timeframe=timeframe)


def realized_volatility(store, instrument, timeframe, decision_time, window=20,
                        periods_per_year=252, **kw) -> FeatureSnapshot:
    bars = _bars(store, instrument, timeframe, decision_time, limit=window + 1, **kw)
    if len(bars) < window + 1:
        return missing("realized_volatility", decision_time, DataQuality.MISSING,
                       instrument=instrument, timeframe=timeframe)

    def compute(values):
        closes = np.array([v["close"] for v in values], dtype="float64")
        returns = np.diff(np.log(closes))
        return float(returns.std(ddof=1) * np.sqrt(periods_per_year))

    return _derive("realized_volatility", bars, compute,
                   instrument=instrument, timeframe=timeframe)


def range_expansion(store, instrument, timeframe, decision_time, window=20, **kw):
    """Latest bar's range relative to its trailing average range."""
    bars = _bars(store, instrument, timeframe, decision_time, limit=window + 1, **kw)
    if len(bars) < window + 1:
        return missing("range_expansion", decision_time, DataQuality.MISSING,
                       instrument=instrument, timeframe=timeframe)

    def compute(values):
        ranges = [v["high"] - v["low"] for v in values]
        baseline = float(np.mean(ranges[:-1]))
        return float(ranges[-1] / baseline) if baseline > 0 else None

    return _derive("range_expansion", bars, compute,
                   instrument=instrument, timeframe=timeframe)


# -- market structure ------------------------------------------------------


def swings(store, instrument, timeframe, decision_time, left=2, right=2,
           lookback=200, **kw) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Confirmed pivots as ``(index, price)``, highs and lows.

    A pivot needs ``right`` bars after it to be confirmed, so the final
    ``right`` bars can never be pivots -- which is what makes this safe to call
    on a growing history.
    """
    bars = _bars(store, instrument, timeframe, decision_time, limit=lookback, **kw)
    highs = [b.value.get("high") for b in bars]
    lows = [b.value.get("low") for b in bars]

    swing_highs, swing_lows = [], []
    for i in range(left, len(bars) - right):
        window_h = highs[i - left:i + right + 1]
        if highs[i] == max(window_h) and window_h.count(highs[i]) == 1:
            swing_highs.append((i, highs[i]))
        window_l = lows[i - left:i + right + 1]
        if lows[i] == min(window_l) and window_l.count(lows[i]) == 1:
            swing_lows.append((i, lows[i]))
    return swing_highs, swing_lows


def structure_state(store, instrument, timeframe, decision_time, **kw) -> FeatureSnapshot:
    """HH/HL/LH/LL from the last two confirmed pivots of each kind."""
    bars = _bars(store, instrument, timeframe, decision_time, limit=200,
                 strict=kw.get("strict", True))
    highs, lows = swings(store, instrument, timeframe, decision_time, **kw)
    if len(highs) < 2 or len(lows) < 2 or not bars:
        return missing("structure_state", decision_time, DataQuality.MISSING,
                       instrument=instrument, timeframe=timeframe)

    high_label = "higher_high" if highs[-1][1] > highs[-2][1] else "lower_high"
    low_label = "higher_low" if lows[-1][1] > lows[-2][1] else "lower_low"
    return _derive("structure_state", bars[-1:],
                   lambda v: {"high": high_label, "low": low_label},
                   instrument=instrument, timeframe=timeframe)


def trend_state(store, instrument, timeframe, decision_time, **kw) -> FeatureSnapshot:
    """up / down / range from the confirmed pivot sequence."""
    bars = _bars(store, instrument, timeframe, decision_time, limit=200,
                 strict=kw.get("strict", True))
    highs, lows = swings(store, instrument, timeframe, decision_time, **kw)
    if len(highs) < 2 or len(lows) < 2 or not bars:
        return missing("trend_state", decision_time, DataQuality.MISSING,
                       instrument=instrument, timeframe=timeframe)

    rising = highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1]
    falling = highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1]
    state = "up" if rising else "down" if falling else "range"
    return _derive("trend_state", bars[-1:], lambda v: state,
                   instrument=instrument, timeframe=timeframe)


def break_of_structure(store, instrument, timeframe, decision_time, **kw) -> FeatureSnapshot:
    """Whether the latest close is beyond the last confirmed opposing pivot."""
    bars = _bars(store, instrument, timeframe, decision_time, limit=200,
                 strict=kw.get("strict", True))
    highs, lows = swings(store, instrument, timeframe, decision_time, **kw)
    if not bars or (not highs and not lows):
        return missing("break_of_structure", decision_time, DataQuality.MISSING,
                       instrument=instrument, timeframe=timeframe)

    close = bars[-1].value.get("close")
    up = bool(highs) and close > highs[-1][1]
    down = bool(lows) and close < lows[-1][1]
    return _derive("break_of_structure", bars[-1:],
                   lambda v: {"up": up, "down": down},
                   instrument=instrument, timeframe=timeframe)


def displacement(store, instrument, timeframe, decision_time, atr_window=14, **kw):
    """Latest bar's range measured in ATR units. Magnitude only, no meaning."""
    atr_snapshot = atr(store, instrument, timeframe, decision_time, atr_window, **kw)
    bars = _bars(store, instrument, timeframe, decision_time, limit=1, **kw)
    if not bars or not atr_snapshot.usable or not atr_snapshot.value:
        return missing("displacement", decision_time, DataQuality.MISSING,
                       instrument=instrument, timeframe=timeframe)

    value = atr_snapshot.value
    return _derive("displacement", bars,
                   lambda v: (v[0]["high"] - v[0]["low"]) / value,
                   instrument=instrument, timeframe=timeframe)


# -- session ---------------------------------------------------------------


def session_vwap(store, instrument, timeframe, decision_time,
                 session: SessionDefinition, **kw) -> FeatureSnapshot:
    """Volume-weighted average price within the current session.

    Declared ``INTRABAR``: it evolves as the session proceeds and is knowable
    from each completed bar within it, not only at session close.

    Missing volume is not zero volume -- a bar whose volume is absent is
    excluded and the result is marked ``STALE`` rather than silently weighting
    it at zero.
    """
    moment = utc(decision_time)
    window = session.window_containing(moment)
    if window is None:
        return missing("session_vwap", decision_time, DataQuality.NOT_APPLICABLE,
                       instrument=instrument, timeframe=timeframe)

    bars = [
        b for b in _bars(store, instrument, timeframe, decision_time, **kw)
        if window.start <= b.event_time < window.end
    ]
    if not bars:
        return missing("session_vwap", decision_time, DataQuality.MISSING,
                       instrument=instrument, timeframe=timeframe)

    usable = [b for b in bars if b.value.get("volume") is not None]
    if not usable:
        return missing("session_vwap", decision_time, DataQuality.MISSING,
                       instrument=instrument, timeframe=timeframe)
    quality = DataQuality.OK if len(usable) == len(bars) else DataQuality.STALE

    def compute(values):
        total_volume = sum(v["volume"] for v in values)
        if total_volume <= 0:
            return None
        typical = [(v["high"] + v["low"] + v["close"]) / 3.0 for v in values]
        return float(sum(t * v["volume"] for t, v in zip(typical, values)) / total_volume)

    return _derive("session_vwap", usable, compute, instrument=instrument,
                   timeframe=timeframe, rule=AvailabilityRule.INTRABAR, quality=quality)


def vwap_distance(store, instrument, timeframe, decision_time,
                  session: SessionDefinition, **kw) -> FeatureSnapshot:
    """Latest close relative to session VWAP, as a fraction."""
    vwap = session_vwap(store, instrument, timeframe, decision_time, session, **kw)
    bars = _bars(store, instrument, timeframe, decision_time, limit=1, **kw)
    if not bars or not vwap.usable or not vwap.value:
        return missing("vwap_distance", decision_time, vwap.data_quality,
                       instrument=instrument, timeframe=timeframe)
    value = vwap.value
    return _derive("vwap_distance", bars, lambda v: v[0]["close"] / value - 1.0,
                   instrument=instrument, timeframe=timeframe,
                   rule=AvailabilityRule.INTRABAR)


# -- previous-period levels ------------------------------------------------


def previous_period_level(
    store, instrument, timeframe, decision_time, session: SessionDefinition,
    level: str, **kw
) -> FeatureSnapshot:
    """Previous completed session's high/low/open/close.

    **Availability is the previous session's close, not its start.** Yesterday's
    high is not knowable until yesterday ended; using the current session's
    running high as "the day's high" during that same session is look-ahead of
    the most basic kind.
    """
    if level not in ("high", "low", "open", "close"):
        raise ValueError(f"unknown level {level!r}")

    window = session.previous_completed(decision_time)
    name = f"prev_{session.name}_{level}"
    if window is None:
        return missing(name, decision_time, DataQuality.MISSING,
                       instrument=instrument, timeframe=timeframe)

    bars = [
        b for b in _bars(store, instrument, timeframe, decision_time, **kw)
        if window.start <= b.event_time < window.end
    ]
    if not bars:
        return missing(name, decision_time, DataQuality.MISSING,
                       instrument=instrument, timeframe=timeframe)

    def compute(values):
        if level == "high":
            return max(v["high"] for v in values)
        if level == "low":
            return min(v["low"] for v in values)
        return values[0]["open"] if level == "open" else values[-1]["close"]

    snapshot = _derive(name, bars, compute, instrument=instrument,
                       timeframe=timeframe, rule=AvailabilityRule.SESSION_CLOSE)
    # The level is knowable no earlier than the session that produced it closed.
    from dataclasses import replace
    return replace(snapshot, available_at=max(snapshot.available_at, window.end),
                   provenance_id="")


def liquidity_references(store, instrument, timeframe, decision_time, **kw) -> dict:
    """Objective candidate liquidity references.

    Prior pivots and previous-period extremes -- prices where resting orders
    plausibly sit. These are observations, not signals, and are deliberately not
    labelled "smart money" anything.
    """
    highs, lows = swings(store, instrument, timeframe, decision_time, **kw)
    return {
        "prior_swing_highs": [price for _, price in highs],
        "prior_swing_lows": [price for _, price in lows],
    }
