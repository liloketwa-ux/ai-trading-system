"""The five objective ICT concepts, computed point-in-time.

These are **our** operational definitions. OpenMobius was a conceptual
reference for terminology and nothing else; its structural implementation was
audited and found to contain four distinct future-information mechanisms, and
none of them are reproduced here.

The defence is architectural rather than disciplinary. :class:`ObjectiveFeatureEngine`
is a **streaming** computer: bars arrive one at a time through :meth:`on_bar`,
and the engine physically cannot see a bar that has not arrived. There is no
array to accidentally scan to the end of, no whole-series ATR to backfill, and
no way for a later bar to change an earlier emission. That last property is
testable directly -- feeding a prefix must produce a prefix of the full output
-- and it is the strongest statement available about look-ahead.

Four defects, four structural preventions:

============================ ==================================================
OpenMobius defect            Prevention here
============================ ==================================================
Centred fractal pivots       :class:`RollingSwings` emits a pivot only at its
emitted at bar ``i``         confirmation bar, carrying both ``formed_at`` and
                             ``confirmed_at``. ``available_at = confirmed_at``.
Whole-array ATR backfilled   :class:`RollingATR` is incremental. It has no
onto every bar               method that accepts a series, so a full-array ATR
                             cannot be computed by mistake.
Forward mitigation scanning  ``FVG`` records no mitigation field. Mitigation is
                             a function of a decision time, exposed as
                             :meth:`FairValueGap.mitigation_as_of`.
Array-relative ``age_bars``  No age is stored. :meth:`age_bars_as_of` takes the
                             decision bar index explicitly.
============================ ==================================================

Every emission is a :class:`~ai_trading.storage.features.FeatureSnapshot` built
through :func:`~ai_trading.storage.features.derive_feature`, so availability
composes as the maximum over inputs and the existing temporal architecture is
never bypassed.

**Implementation is not evidence.** All five features are ``UNTESTED``. Nothing
here computes a win rate, an expectancy, or a signal.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Iterable, Sequence

from ..storage.features import FeatureSnapshot, derive_feature
from ..storage.quality import AvailabilityRule, DataQuality
from ..storage.records import Observation, utc

__all__ = [
    "Candle", "RollingATR", "Swing", "RollingSwings", "FairValueGap",
    "Displacement", "EqualLevel", "LiquiditySweep", "ObjectiveFeatureEngine",
    "FeatureSpec", "OBJECTIVE_FEATURE_SPECS", "FeatureRegistryError",
    "ObjectiveFeatureRegistry", "FEATURE_REGISTRY", "Direction",
    "ATR_PERIOD", "FVG_MIN_ATR", "DISPLACEMENT_ATR_MULT",
    "EQUAL_TOLERANCE_ATR", "EQUAL_MIN_SEPARATION_BARS", "SWING_LEFT",
    "SWING_RIGHT",
]

# -- versioned parameters -------------------------------------------------
#: All parameters are part of the definition. Changing one is a v2.
ATR_PERIOD = 14
FVG_MIN_ATR = 0.2
DISPLACEMENT_ATR_MULT = 2.0
EQUAL_TOLERANCE_ATR = 0.1
EQUAL_MIN_SEPARATION_BARS = 3
SWING_LEFT = 2
SWING_RIGHT = 2


class Direction(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class FeatureRegistryError(RuntimeError):
    """A feature was redefined without a version bump."""


@dataclass(frozen=True)
class Candle:
    """One completed bar, with the two timestamps that matter.

    ``available_at`` is the bar's own availability as established by the
    ingestion layer's policy -- this module never invents it.
    """

    index: int
    event_time: datetime
    available_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    instrument: str = ""
    timeframe: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_time", utc(self.event_time))
        object.__setattr__(self, "available_at", utc(self.available_at))
        if self.available_at < self.event_time:
            raise ValueError(
                f"bar {self.index}: available_at precedes event_time"
            )
        if self.high < self.low:
            raise ValueError(f"bar {self.index}: high < low")

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def direction(self) -> Direction:
        return Direction.BULLISH if self.close >= self.open else Direction.BEARISH

    def as_observation(self, kind: str = "bar") -> Observation:
        return Observation(
            kind=kind, key=f"{self.instrument}:{self.timeframe}:{self.index}",
            event_time=self.event_time, available_at=self.available_at,
            ingested_at=self.available_at, value=self.close, source="bars",
        )


class RollingATR:
    """Incremental ATR. Cannot be computed over a whole series.

    The absence of a ``from_series`` method is the point: the audited reference
    implementation took the last 14 true ranges of an entire array and used that
    single value to threshold every earlier bar, so an event at bar 50 was
    classified with volatility from bar 3,000. There is no API here through
    which that can happen.
    """

    __slots__ = ("period", "_trs", "_previous_close", "_bars_seen")

    def __init__(self, period: int = ATR_PERIOD) -> None:
        if period < 1:
            raise ValueError("ATR period must be >= 1")
        self.period = period
        self._trs: deque[float] = deque(maxlen=period)
        self._previous_close: float | None = None
        self._bars_seen = 0

    def update(self, candle: Candle) -> float | None:
        """Fold in one bar and return the ATR **as of that bar**."""
        if self._previous_close is None:
            self._previous_close = candle.close
            self._bars_seen += 1
            return None
        true_range = max(
            candle.high - candle.low,
            abs(candle.high - self._previous_close),
            abs(candle.low - self._previous_close),
        )
        self._trs.append(true_range)
        self._previous_close = candle.close
        self._bars_seen += 1
        return self.value

    @property
    def value(self) -> float | None:
        """ATR over the last ``period`` completed true ranges, or ``None``."""
        if len(self._trs) < self.period:
            return None
        return sum(self._trs) / self.period

    @property
    def is_warm(self) -> bool:
        return self.value is not None

    @property
    def bars_seen(self) -> int:
        return self._bars_seen


@dataclass(frozen=True)
class Swing:
    """A fractal pivot, with formation and confirmation kept apart.

    ``formed_at_index`` is when the extreme occurred; ``confirmed_at_index`` is
    when it became knowable. Collapsing them is the defect this type exists to
    make impossible -- there is no single "time" attribute to reach for.
    """

    formed_at_index: int
    confirmed_at_index: int
    formed_at: datetime
    confirmed_at: datetime
    available_at: datetime
    price: float
    kind: Direction

    @property
    def confirmation_lag_bars(self) -> int:
        return self.confirmed_at_index - self.formed_at_index

    def is_available_at(self, decision_time: datetime) -> bool:
        return self.available_at <= utc(decision_time)

    def to_dict(self) -> dict:
        return {
            "formed_at_index": self.formed_at_index,
            "confirmed_at_index": self.confirmed_at_index,
            "formed_at": self.formed_at.isoformat(),
            "confirmed_at": self.confirmed_at.isoformat(),
            "available_at": self.available_at.isoformat(),
            "price": self.price, "kind": self.kind.value,
            "confirmation_lag_bars": self.confirmation_lag_bars,
        }


class RollingSwings:
    """Fractal pivots emitted only at their confirmation bar.

    A pivot at bar ``i`` needs ``right`` later bars to be a pivot at all, so it
    is emitted when bar ``i + right`` arrives and never before. The engine
    therefore cannot report a swing that a decision at bar ``i`` could have
    known about, because at bar ``i`` it does not exist yet.
    """

    def __init__(self, left: int = SWING_LEFT, right: int = SWING_RIGHT) -> None:
        if left < 1 or right < 1:
            raise ValueError("swing left/right must both be >= 1")
        self.left = left
        self.right = right
        self._window: deque[Candle] = deque(maxlen=left + right + 1)

    def update(self, candle: Candle) -> list[Swing]:
        """Fold in one bar; return pivots confirmed **by** this bar."""
        self._window.append(candle)
        if len(self._window) < self.left + self.right + 1:
            return []

        bars = list(self._window)
        centre = bars[self.left]
        before = bars[:self.left]
        after = bars[self.left + 1:]
        confirming = bars[-1]
        found: list[Swing] = []

        # Strict on both sides. Non-strict comparison makes every bar of a
        # flat region a pivot, and those spurious pivots then cascade into
        # spurious equal-highs (they are identical by construction) and
        # spurious sweeps. A genuine double top at the same price is separated
        # by more than this window and is what Equal High exists to express.
        if (all(centre.high > b.high for b in before)
                and all(centre.high > b.high for b in after)):
            found.append(Swing(
                centre.index, confirming.index, centre.event_time,
                confirming.event_time, confirming.available_at,
                centre.high, Direction.BULLISH))
        if (all(centre.low < b.low for b in before)
                and all(centre.low < b.low for b in after)):
            found.append(Swing(
                centre.index, confirming.index, centre.event_time,
                confirming.event_time, confirming.available_at,
                centre.low, Direction.BEARISH))
        return found


@dataclass(frozen=True)
class FairValueGap:
    """A three-candle imbalance.

    Carries **no mitigation field**. Mitigation is a property of a decision
    time, not of the gap, and storing it on the record is how the reference
    implementation ended up scanning to the end of the array.
    """

    direction: Direction
    top: float
    bottom: float
    formed_at_index: int
    available_at_index: int
    formed_at: datetime
    available_at: datetime
    size: float
    size_atr: float
    instrument: str = ""
    timeframe: str = ""

    @property
    def confirmation_lag_bars(self) -> int:
        return self.available_at_index - self.formed_at_index

    def age_bars_as_of(self, decision_index: int) -> int:
        """Age relative to an explicit decision bar, never to an array end."""
        if decision_index < self.available_at_index:
            raise ValueError(
                f"FVG is not available until bar {self.available_at_index}; "
                f"asked for age as of bar {decision_index}"
            )
        return decision_index - self.formed_at_index

    def mitigation_as_of(self, bars: Sequence[Candle],
                         decision_index: int) -> float:
        """Fraction of the gap filled by bars up to ``decision_index``.

        Takes the decision index explicitly and never reads past it. Separate
        from the gap record on purpose: this is a future outcome relative to
        formation, and mixing it into the historical feature would leak.
        """
        if decision_index < self.available_at_index:
            raise ValueError(
                f"FVG is not available until bar {self.available_at_index}"
            )
        span = self.top - self.bottom
        if span <= 0:
            return 1.0
        filled = 0.0
        for candle in bars:
            if candle.index <= self.available_at_index:
                continue
            if candle.index > decision_index:
                break        # never read past the decision bar
            if self.direction is Direction.BULLISH:
                filled = max(filled, min(span, max(0.0, self.top - candle.low)))
            else:
                filled = max(filled, min(span, max(0.0, candle.high - self.bottom)))
        return round(filled / span, 6)

    def to_dict(self) -> dict:
        return {
            "direction": self.direction.value, "top": self.top,
            "bottom": self.bottom, "formed_at_index": self.formed_at_index,
            "available_at_index": self.available_at_index,
            "formed_at": self.formed_at.isoformat(),
            "available_at": self.available_at.isoformat(),
            "size": self.size, "size_atr": self.size_atr,
            "confirmation_lag_bars": self.confirmation_lag_bars,
        }


@dataclass(frozen=True)
class Displacement:
    """A single-bar expansion, measured against a point-in-time ATR."""

    direction: Direction
    bar_index: int
    event_time: datetime
    available_at: datetime
    bar_range: float
    atr: float
    range_atr: float
    body_range: float
    displacement_atr: float
    instrument: str = ""
    timeframe: str = ""

    @property
    def displacement_strength(self) -> float:
        """Body in ATR multiples. The quantity the threshold is applied to."""
        return self.displacement_atr

    @property
    def confirmation_lag_bars(self) -> int:
        return 0

    def to_dict(self) -> dict:
        return {
            "direction": self.direction.value, "bar_index": self.bar_index,
            "event_time": self.event_time.isoformat(),
            "available_at": self.available_at.isoformat(),
            "range": self.bar_range, "atr": self.atr,
            "range_atr": self.range_atr, "body_range": self.body_range,
            "displacement_atr": self.displacement_atr,
            "displacement_direction": self.direction.value,
            "displacement_strength": self.displacement_strength,
        }


@dataclass(frozen=True)
class EqualLevel:
    """Two confirmed pivots at the same price within tolerance."""

    kind: Direction                      # BULLISH = equal highs
    price: float
    first_swing: Swing
    second_swing: Swing
    tolerance: float
    tolerance_atr: float
    separation_bars: int
    available_at: datetime
    available_at_index: int
    instrument: str = ""
    timeframe: str = ""

    @property
    def price_difference(self) -> float:
        return abs(self.first_swing.price - self.second_swing.price)

    def to_dict(self) -> dict:
        return {
            "kind": "equal_high" if self.kind is Direction.BULLISH else "equal_low",
            "price": self.price,
            "first_swing": self.first_swing.to_dict(),
            "second_swing": self.second_swing.to_dict(),
            "price_difference": self.price_difference,
            "tolerance": self.tolerance, "tolerance_atr": self.tolerance_atr,
            "separation_bars": self.separation_bars,
            "available_at": self.available_at.isoformat(),
            "available_at_index": self.available_at_index,
        }


@dataclass(frozen=True)
class LiquiditySweep:
    """Price trades beyond a *previously confirmed* level and closes back."""

    direction: Direction
    reference_price: float
    reference_level_time: datetime          # when the swing formed
    reference_confirmed_at: datetime        # when it became knowable
    reference_formed_index: int
    reference_confirmed_index: int
    sweep_event_time: datetime
    sweep_bar_index: int
    available_at: datetime
    penetration: float
    close_back_inside: float
    instrument: str = ""
    timeframe: str = ""

    @property
    def confirmation_time(self) -> datetime:
        """A sweep confirms at its own close; the reference confirmed earlier."""
        return self.sweep_event_time

    def to_dict(self) -> dict:
        return {
            "direction": self.direction.value,
            "reference_price": self.reference_price,
            "reference_level_time": self.reference_level_time.isoformat(),
            "reference_confirmed_at": self.reference_confirmed_at.isoformat(),
            "reference_formed_index": self.reference_formed_index,
            "reference_confirmed_index": self.reference_confirmed_index,
            "sweep_event_time": self.sweep_event_time.isoformat(),
            "sweep_bar_index": self.sweep_bar_index,
            "confirmation_time": self.confirmation_time.isoformat(),
            "available_at": self.available_at.isoformat(),
            "penetration": self.penetration,
            "close_back_inside": self.close_back_inside,
        }


# =========================================================================
# Feature specification and registry
# =========================================================================


@dataclass(frozen=True)
class FeatureSpec:
    """The full contract for one objective concept."""

    concept_name: str
    feature_name: str
    definition_version: str
    operational_definition: str
    temporal_rule: str
    required_inputs: tuple[str, ...]
    output_schema: tuple[str, ...]
    research_status: str = "UNTESTED"
    classification: str = "OBJECTIVE"
    parameters: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.feature_name}:{self.definition_version}"

    @property
    def has_evidence(self) -> bool:
        """Always ``False``. Implementation is not evidence."""
        return False

    def to_dict(self) -> dict:
        return {
            "concept_name": self.concept_name,
            "feature_name": self.feature_name,
            "definition_version": self.definition_version,
            "key": self.key,
            "operational_definition": self.operational_definition,
            "temporal_rule": self.temporal_rule,
            "required_inputs": list(self.required_inputs),
            "output_schema": list(self.output_schema),
            "research_status": self.research_status,
            "classification": self.classification,
            "parameters": dict(self.parameters),
            "has_evidence": False,
        }


OBJECTIVE_FEATURE_SPECS: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        concept_name="Fair Value Gap", feature_name="fvg",
        definition_version="v1",
        operational_definition=(
            "Bullish when high[i] < low[i+2]; bearish when low[i] > high[i+2]. "
            "Gap band is (high[i], low[i+2]) or (high[i+2], low[i]). Rejected "
            "when the band is smaller than 0.2 x ATR(14) measured as of bar "
            "i+2. Mitigation is not part of the record."),
        temporal_rule=(
            "formed_at = bar i+1 (the middle bar); available_at = close of bar "
            "i+2, because the third candle is required to observe the gap at "
            "all. Emitted only when bar i+2 arrives."),
        required_inputs=("bar[i]", "bar[i+1]", "bar[i+2]", "atr:v1@i+2"),
        output_schema=("direction", "top", "bottom", "size", "size_atr",
                       "formed_at_index", "available_at_index", "formed_at",
                       "available_at", "confirmation_lag_bars"),
        parameters={"atr_period": ATR_PERIOD, "min_size_atr": FVG_MIN_ATR},
    ),
    FeatureSpec(
        concept_name="Displacement", feature_name="displacement",
        definition_version="v1",
        operational_definition=(
            "|close - open| >= 2.0 x ATR(14), where ATR is computed "
            "incrementally on bars <= i. Direction from sign(close - open)."),
        temporal_rule=(
            "formed_at = available_at = close of bar i. No future bar "
            "participates, and the ATR window ends at bar i."),
        required_inputs=("bar[i]", "atr:v1@i"),
        output_schema=("direction", "bar_index", "range", "atr", "range_atr",
                       "body_range", "displacement_atr",
                       "displacement_direction", "displacement_strength",
                       "event_time", "available_at"),
        parameters={"atr_period": ATR_PERIOD,
                    "atr_multiple": DISPLACEMENT_ATR_MULT},
    ),
    FeatureSpec(
        concept_name="Equal High", feature_name="equal_high",
        definition_version="v1",
        operational_definition=(
            "Two confirmed swing highs whose prices differ by no more than "
            "0.1 x ATR(14) measured as of the later pivot's confirmation bar, "
            "separated by at least 3 bars. Never dissolved by later bars."),
        temporal_rule=(
            "available_at = confirmation bar of the later pivot, which is "
            "itself formed_at + 2. The pair is emitted at that bar."),
        required_inputs=("swing:v1", "swing:v1", "atr:v1@confirmation"),
        output_schema=("kind", "price", "first_swing", "second_swing",
                       "price_difference", "tolerance", "tolerance_atr",
                       "separation_bars", "available_at",
                       "available_at_index"),
        parameters={"tolerance_atr": EQUAL_TOLERANCE_ATR,
                    "min_separation_bars": EQUAL_MIN_SEPARATION_BARS,
                    "swing_left": SWING_LEFT, "swing_right": SWING_RIGHT},
    ),
    FeatureSpec(
        concept_name="Equal Low", feature_name="equal_low",
        definition_version="v1",
        operational_definition=(
            "Mirror of equal_high:v1 on confirmed swing lows, with the same "
            "tolerance and separation."),
        temporal_rule="As equal_high:v1.",
        required_inputs=("swing:v1", "swing:v1", "atr:v1@confirmation"),
        output_schema=("kind", "price", "first_swing", "second_swing",
                       "price_difference", "tolerance", "tolerance_atr",
                       "separation_bars", "available_at",
                       "available_at_index"),
        parameters={"tolerance_atr": EQUAL_TOLERANCE_ATR,
                    "min_separation_bars": EQUAL_MIN_SEPARATION_BARS,
                    "swing_left": SWING_LEFT, "swing_right": SWING_RIGHT},
    ),
    FeatureSpec(
        concept_name="Liquidity Sweep", feature_name="liquidity_sweep",
        definition_version="v1",
        operational_definition=(
            "For a swing high at price P confirmed at or before bar i-1: bar i "
            "sweeps when high[i] > P and close[i] < P. Mirror for swing lows. "
            "The reference is never created retroactively."),
        temporal_rule=(
            "reference_level_time = the swing's formation bar; "
            "reference_confirmed_at = its confirmation bar, which must be <= "
            "i-1; sweep_event_time = confirmation_time = available_at = close "
            "of bar i."),
        required_inputs=("bar[i]", "swing:v1 confirmed <= i-1"),
        output_schema=("direction", "reference_price", "reference_level_time",
                       "reference_confirmed_at", "reference_formed_index",
                       "reference_confirmed_index", "sweep_event_time",
                       "sweep_bar_index", "confirmation_time", "available_at",
                       "penetration", "close_back_inside"),
        parameters={"swing_left": SWING_LEFT, "swing_right": SWING_RIGHT},
    ),
)


class ObjectiveFeatureRegistry:
    """Versioned feature definitions. Silent redefinition is refused."""

    def __init__(self) -> None:
        self._specs: dict[str, FeatureSpec] = {}

    def register(self, spec: FeatureSpec) -> FeatureSpec:
        existing = self._specs.get(spec.key)
        if existing is not None:
            if existing.to_dict() != spec.to_dict():
                raise FeatureRegistryError(
                    f"{spec.key} is already registered with a different "
                    "definition. Changing a definition creates a new version "
                    "and therefore a new research lineage -- register it as "
                    f"{spec.feature_name}:v2 instead of redefining "
                    f"{spec.key} in place."
                )
            return existing
        self._specs[spec.key] = spec
        return spec

    def get(self, key: str) -> FeatureSpec | None:
        return self._specs.get(key)

    def require(self, key: str) -> FeatureSpec:
        spec = self._specs.get(key)
        if spec is None:
            raise FeatureRegistryError(
                f"{key} is not a registered feature. Registered: "
                f"{', '.join(sorted(self._specs)) or 'none'}"
            )
        return spec

    def all(self) -> list[FeatureSpec]:
        return sorted(self._specs.values(), key=lambda s: s.key)

    def keys(self) -> list[str]:
        return sorted(self._specs)

    def __len__(self) -> int:
        return len(self._specs)

    def __contains__(self, key: object) -> bool:
        return key in self._specs

    def summary(self) -> dict:
        return {
            "features": self.keys(),
            "count": len(self._specs),
            "all_objective": all(s.classification == "OBJECTIVE"
                                 for s in self.all()),
            "all_untested": all(s.research_status == "UNTESTED"
                                for s in self.all()),
            "with_evidence": 0,
        }


FEATURE_REGISTRY = ObjectiveFeatureRegistry()
for _spec in OBJECTIVE_FEATURE_SPECS:
    FEATURE_REGISTRY.register(_spec)
del _spec


# =========================================================================
# The streaming engine
# =========================================================================


@dataclass
class BarEmission:
    """Everything one bar produced, and the snapshots for it."""

    bar_index: int
    event_time: datetime
    available_at: datetime
    atr: float | None
    swings: list[Swing] = field(default_factory=list)
    fvgs: list[FairValueGap] = field(default_factory=list)
    displacements: list[Displacement] = field(default_factory=list)
    equal_highs: list[EqualLevel] = field(default_factory=list)
    equal_lows: list[EqualLevel] = field(default_factory=list)
    sweeps: list[LiquiditySweep] = field(default_factory=list)
    snapshots: list[FeatureSnapshot] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.snapshots


class ObjectiveFeatureEngine:
    """Computes the five objective features one bar at a time.

    The engine holds only what it needs: a rolling ATR, a swing window of
    ``left + right + 1`` bars, the confirmed swings so far, and a bounded tail
    of recent bars for the three-candle FVG test. It never holds the future,
    because the future has not been passed in.

    Determinism and append-only emission are the two properties worth stating.
    Feeding the same bars in the same order always produces the same output,
    and feeding a prefix produces a prefix of the full output. Together those
    mean no later bar can alter, revise, or retroactively create an earlier
    feature -- which is precisely what the audited reference implementation
    could not promise.
    """

    def __init__(self, *, instrument: str = "", timeframe: str = "",
                 atr_period: int = ATR_PERIOD,
                 fvg_min_atr: float = FVG_MIN_ATR,
                 displacement_atr_mult: float = DISPLACEMENT_ATR_MULT,
                 equal_tolerance_atr: float = EQUAL_TOLERANCE_ATR,
                 equal_min_separation: int = EQUAL_MIN_SEPARATION_BARS,
                 swing_left: int = SWING_LEFT,
                 swing_right: int = SWING_RIGHT,
                 dataset_version: str | None = None) -> None:
        self.instrument = instrument
        self.timeframe = timeframe
        self.fvg_min_atr = fvg_min_atr
        self.displacement_atr_mult = displacement_atr_mult
        self.equal_tolerance_atr = equal_tolerance_atr
        self.equal_min_separation = equal_min_separation
        self.dataset_version = dataset_version

        self._atr = RollingATR(atr_period)
        self._swings = RollingSwings(swing_left, swing_right)
        self._recent: deque[Candle] = deque(maxlen=3)
        self._confirmed_swings: list[Swing] = []
        self._emissions: list[BarEmission] = []
        self._last_index: int | None = None

    # -- accessors --------------------------------------------------------
    @property
    def confirmed_swings(self) -> list[Swing]:
        """Swings confirmed so far. Never includes an unconfirmed pivot."""
        return list(self._confirmed_swings)

    @property
    def emissions(self) -> list[BarEmission]:
        return list(self._emissions)

    def snapshots(self) -> list[FeatureSnapshot]:
        return [s for e in self._emissions for s in e.snapshots]

    # -- the only entry point ---------------------------------------------
    def on_bar(self, candle: Candle) -> BarEmission:
        """Fold in one bar and emit whatever became knowable at it.

        Bars must arrive in order. Accepting an out-of-order bar would let a
        caller replay history backwards, which is the one way a streaming
        engine can still be made to leak.
        """
        if self._last_index is not None and candle.index <= self._last_index:
            raise ValueError(
                f"bar {candle.index} does not follow bar {self._last_index}; "
                "bars must arrive in order or the engine can be made to leak"
            )
        self._last_index = candle.index

        atr = self._atr.update(candle)
        emission = BarEmission(candle.index, candle.event_time,
                               candle.available_at, atr)

        self._detect_displacement(candle, atr, emission)
        self._recent.append(candle)
        self._detect_fvg(candle, atr, emission)

        newly_confirmed = self._swings.update(candle)
        for swing in newly_confirmed:
            self._confirmed_swings.append(swing)
            emission.swings.append(swing)
            self._detect_equal_levels(swing, candle, atr, emission)

        # Sweeps use only swings confirmed strictly before this bar, so the
        # detection runs against the list as it stood on entry.
        self._detect_sweep(candle, emission,
                           exclude={s.formed_at_index for s in newly_confirmed})

        self._emissions.append(emission)
        return emission

    def run(self, candles: Iterable[Candle]) -> list[BarEmission]:
        return [self.on_bar(candle) for candle in candles]

    # -- detectors --------------------------------------------------------
    def _snapshot(self, name: str, value, candle: Candle,
                  inputs: Sequence[Observation | FeatureSnapshot],
                  available_at: datetime | None = None) -> FeatureSnapshot:
        """Build through derive_feature so availability composes correctly."""
        snapshot = derive_feature(
            name, list(inputs), lambda _values: value,
            source="ict_objective", feature_version="1",
            available_at=available_at,
        )
        from dataclasses import replace
        return replace(
            snapshot, instrument=self.instrument, timeframe=self.timeframe,
            dataset_version=self.dataset_version,
            data_quality=DataQuality.OK,
            availability_rule=AvailabilityRule.INPUT_MAX,
            provenance_id="",
        )

    def _detect_displacement(self, candle: Candle, atr: float | None,
                             emission: BarEmission) -> None:
        if atr is None or atr <= 0:
            return
        body = candle.body
        if body < self.displacement_atr_mult * atr:
            return
        bar_range = candle.range
        displacement = Displacement(
            direction=candle.direction, bar_index=candle.index,
            event_time=candle.event_time, available_at=candle.available_at,
            bar_range=round(bar_range, 6), atr=round(atr, 6),
            range_atr=round(bar_range / atr, 6) if atr else 0.0,
            body_range=round(body / bar_range, 6) if bar_range else 0.0,
            displacement_atr=round(body / atr, 6),
            instrument=self.instrument, timeframe=self.timeframe,
        )
        emission.displacements.append(displacement)
        emission.snapshots.append(self._snapshot(
            "displacement", displacement.to_dict(), candle,
            [candle.as_observation()]))

    def _detect_fvg(self, candle: Candle, atr: float | None,
                    emission: BarEmission) -> None:
        if len(self._recent) < 3 or atr is None or atr <= 0:
            return
        first, middle, third = self._recent[0], self._recent[1], self._recent[2]
        if third.index is not candle.index and third.index != candle.index:
            return

        if first.high < third.low:
            direction, top, bottom = Direction.BULLISH, third.low, first.high
        elif first.low > third.high:
            direction, top, bottom = Direction.BEARISH, first.low, third.high
        else:
            return

        size = top - bottom
        if size < self.fvg_min_atr * atr:
            return

        gap = FairValueGap(
            direction=direction, top=round(top, 6), bottom=round(bottom, 6),
            formed_at_index=middle.index, available_at_index=third.index,
            formed_at=middle.event_time, available_at=third.available_at,
            size=round(size, 6), size_atr=round(size / atr, 6),
            instrument=self.instrument, timeframe=self.timeframe,
        )
        emission.fvgs.append(gap)
        emission.snapshots.append(self._snapshot(
            "fvg", gap.to_dict(), candle,
            [first.as_observation(), middle.as_observation(),
             third.as_observation()]))

    def _detect_equal_levels(self, swing: Swing, candle: Candle,
                             atr: float | None,
                             emission: BarEmission) -> None:
        if atr is None or atr <= 0:
            return
        tolerance = self.equal_tolerance_atr * atr
        for earlier in reversed(self._confirmed_swings[:-1]):
            if earlier.kind is not swing.kind:
                continue
            separation = swing.formed_at_index - earlier.formed_at_index
            if separation < self.equal_min_separation:
                continue
            if abs(earlier.price - swing.price) > tolerance:
                continue
            level = EqualLevel(
                kind=swing.kind,
                price=round((earlier.price + swing.price) / 2.0, 6),
                first_swing=earlier, second_swing=swing,
                tolerance=round(tolerance, 6),
                tolerance_atr=self.equal_tolerance_atr,
                separation_bars=separation,
                available_at=swing.available_at,
                available_at_index=swing.confirmed_at_index,
                instrument=self.instrument, timeframe=self.timeframe,
            )
            name = ("equal_high" if swing.kind is Direction.BULLISH
                    else "equal_low")
            target = (emission.equal_highs if swing.kind is Direction.BULLISH
                      else emission.equal_lows)
            target.append(level)
            emission.snapshots.append(self._snapshot(
                name, level.to_dict(), candle, [candle.as_observation()]))
            break        # nearest qualifying pivot only

    def _detect_sweep(self, candle: Candle, emission: BarEmission,
                      exclude: set[int]) -> None:
        for swing in reversed(self._confirmed_swings):
            # A reference must have been confirmed strictly before this bar.
            if swing.confirmed_at_index >= candle.index:
                continue
            if swing.formed_at_index in exclude:
                continue
            if swing.kind is Direction.BULLISH:
                if not (candle.high > swing.price and candle.close < swing.price):
                    continue
                penetration = candle.high - swing.price
                close_back = swing.price - candle.close
            else:
                if not (candle.low < swing.price and candle.close > swing.price):
                    continue
                penetration = swing.price - candle.low
                close_back = candle.close - swing.price

            sweep = LiquiditySweep(
                direction=swing.kind, reference_price=swing.price,
                reference_level_time=swing.formed_at,
                reference_confirmed_at=swing.confirmed_at,
                reference_formed_index=swing.formed_at_index,
                reference_confirmed_index=swing.confirmed_at_index,
                sweep_event_time=candle.event_time,
                sweep_bar_index=candle.index,
                available_at=candle.available_at,
                penetration=round(penetration, 6),
                close_back_inside=round(close_back, 6),
                instrument=self.instrument, timeframe=self.timeframe,
            )
            emission.sweeps.append(sweep)
            emission.snapshots.append(self._snapshot(
                "liquidity_sweep", sweep.to_dict(), candle,
                [candle.as_observation()]))
            break        # most recent qualifying reference only
