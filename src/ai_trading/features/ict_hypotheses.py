"""ICT hypothesis feature vector -- objective components only.

Every field below is a measurement with a precise definition. Nothing here is
an entry rule, a threshold, or a claim that these patterns predict anything.
They exist so Phase 5 can ask whether combinations of them carry information,
and the answer is allowed to be no.

Subjective ICT vocabulary is deliberately excluded. "Institutional order flow"
and "smart money intent" have no calculable definition, so they cannot be
tested and are not represented.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from ..storage.features import FeatureSnapshot, derive_feature
from ..storage.quality import AvailabilityRule, DataQuality
from ..storage.records import utc
from ..storage.store import ObservationStore
from . import futures
from .sessions import SESSIONS, SessionDefinition
from .timeframes import completed_bars, latest_completed_bar

__all__ = ["ICTVector", "build_ict_vector", "HYPOTHESIS_FEATURE_VERSION"]

HYPOTHESIS_FEATURE_VERSION = "1"
SOURCE = "features/ict_hypotheses"


@dataclass(frozen=True)
class ICTVector:
    """Machine-readable hypothesis features at one decision time.

    Each component is a :class:`FeatureSnapshot`, so availability and quality
    travel with the value. :meth:`as_dict` flattens to plain values only for
    statistical grouping, never for decision-making.
    """

    instrument: str
    timeframe: str
    decision_time: datetime
    liquidity_sweep: FeatureSnapshot
    displacement_atr: FeatureSnapshot
    fvg: FeatureSnapshot
    mss: FeatureSnapshot
    htf_bias: FeatureSnapshot
    session: FeatureSnapshot

    @property
    def components(self) -> dict[str, FeatureSnapshot]:
        return {
            "liquidity_sweep": self.liquidity_sweep,
            "displacement_atr": self.displacement_atr,
            "fvg": self.fvg,
            "mss": self.mss,
            "htf_bias": self.htf_bias,
            "session": self.session,
        }

    @property
    def available_at(self) -> datetime:
        """The vector is knowable only once every component is."""
        return max(s.available_at for s in self.components.values())

    @property
    def complete(self) -> bool:
        """Whether every component resolved to a usable value."""
        return all(s.usable for s in self.components.values())

    def is_eligible_at(self, decision_time: datetime) -> bool:
        return self.available_at <= utc(decision_time) and self.complete

    def as_dict(self) -> dict[str, Any]:
        return {name: s.value for name, s in self.components.items()}


def _snapshot(name, value, bars, instrument, timeframe, quality=DataQuality.OK):
    """Build through the central constructor so availability propagates."""
    if not bars:
        moment = utc(datetime.now())
        return FeatureSnapshot(name, None, moment, moment, SOURCE,
                               feature_version=HYPOTHESIS_FEATURE_VERSION,
                               instrument=instrument, timeframe=timeframe,
                               data_quality=DataQuality.MISSING)
    snapshot = derive_feature(name, list(bars), lambda _: value, source=SOURCE,
                              feature_version=HYPOTHESIS_FEATURE_VERSION)
    return replace(snapshot, instrument=instrument, timeframe=timeframe,
                   data_quality=quality, availability_rule=AvailabilityRule.BAR_CLOSE,
                   provenance_id="")


def _missing(name, instrument, timeframe, decision_time):
    moment = utc(decision_time)
    return FeatureSnapshot(name, None, moment, moment, SOURCE,
                           feature_version=HYPOTHESIS_FEATURE_VERSION,
                           instrument=instrument, timeframe=timeframe,
                           data_quality=DataQuality.MISSING)


def detect_liquidity_sweep(store, instrument, timeframe, decision_time,
                           lookback=50, **kw) -> FeatureSnapshot:
    """A wick beyond a prior confirmed pivot with a close back inside.

    Objective definition: the latest bar's high exceeds a confirmed prior swing
    high while its close returns below that level (or the mirror for lows). No
    claim about who was stopped out or why.
    """
    name = "liquidity_sweep"
    bars = completed_bars(store, instrument, timeframe, decision_time, limit=lookback,
                          strict=kw.get("strict", True))
    if len(bars) < 10:
        return _missing(name, instrument, timeframe, decision_time)

    highs, lows = futures.swings(store, instrument, timeframe, decision_time, **kw)
    latest = bars[-1].value
    swept_high = any(
        latest["high"] > price and latest["close"] < price for _, price in highs[:-1]
    ) if len(highs) > 1 else False
    swept_low = any(
        latest["low"] < price and latest["close"] > price for _, price in lows[:-1]
    ) if len(lows) > 1 else False

    return _snapshot(name, bool(swept_high or swept_low), bars[-1:], instrument, timeframe)


def detect_fvg(store, instrument, timeframe, decision_time, **kw) -> FeatureSnapshot:
    """Three-bar imbalance: bar1 high below bar3 low, or the mirror."""
    name = "fvg"
    bars = completed_bars(store, instrument, timeframe, decision_time, limit=3,
                          strict=kw.get("strict", True))
    if len(bars) < 3:
        return _missing(name, instrument, timeframe, decision_time)

    first, _, third = bars[-3].value, bars[-2].value, bars[-1].value
    bullish = third["low"] > first["high"]
    bearish = third["high"] < first["low"]
    return _snapshot(name, bool(bullish or bearish), bars[-3:], instrument, timeframe)


def detect_mss(store, instrument, timeframe, decision_time, **kw) -> FeatureSnapshot:
    """Market structure shift: a close beyond the last confirmed opposing pivot."""
    name = "mss"
    bos = futures.break_of_structure(store, instrument, timeframe, decision_time, **kw)
    if not bos.usable:
        return _missing(name, instrument, timeframe, decision_time)
    value = bool(bos.value.get("up") or bos.value.get("down"))
    return replace(bos, name=name, value=value,
                   feature_version=HYPOTHESIS_FEATURE_VERSION, provenance_id="")


def detect_htf_bias(store, instrument, decision_time, htf="4h", **kw) -> FeatureSnapshot:
    """Higher-timeframe trend state.

    Uses only HTF bars that have **closed** by the decision time -- the
    canonical multi-timeframe leak is reading a forming 4H bar from a 5m
    decision.
    """
    name = "htf_bias"
    trend = futures.trend_state(store, instrument, htf, decision_time, **kw)
    if not trend.usable:
        return _missing(name, instrument, htf, decision_time)
    mapping = {"up": "bullish", "down": "bearish", "range": "neutral"}
    return replace(trend, name=name, value=mapping.get(trend.value, "neutral"),
                   feature_version=HYPOTHESIS_FEATURE_VERSION, provenance_id="")


def detect_session(store, instrument, timeframe, decision_time,
                   sessions: dict[str, SessionDefinition] | None = None,
                   **kw) -> FeatureSnapshot:
    """Which named session contains the decision time."""
    name = "session"
    catalogue = sessions or SESSIONS
    moment = utc(decision_time)
    label = "none"
    for session_name, definition in catalogue.items():
        if definition.is_open(moment):
            label = session_name
            break

    bars = completed_bars(store, instrument, timeframe, decision_time, limit=1,
                          strict=kw.get("strict", True))
    if not bars:
        return _missing(name, instrument, timeframe, decision_time)
    return _snapshot(name, label, bars, instrument, timeframe)


def build_ict_vector(
    store: ObservationStore, instrument: str, timeframe: str,
    decision_time: datetime, *, htf: str = "4h", strict: bool = True,
) -> ICTVector:
    """Assemble the full hypothesis vector at one decision time."""
    kw = {"strict": strict}
    return ICTVector(
        instrument=instrument,
        timeframe=timeframe,
        decision_time=utc(decision_time),
        liquidity_sweep=detect_liquidity_sweep(store, instrument, timeframe, decision_time, **kw),
        displacement_atr=futures.displacement(store, instrument, timeframe, decision_time, **kw),
        fvg=detect_fvg(store, instrument, timeframe, decision_time, **kw),
        mss=detect_mss(store, instrument, timeframe, decision_time, **kw),
        htf_bias=detect_htf_bias(store, instrument, decision_time, htf, **kw),
        session=detect_session(store, instrument, timeframe, decision_time, **kw),
    )
