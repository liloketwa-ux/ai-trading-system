"""Phase 10: the five objective ICT features, and the look-ahead attacks.

The governing test is :func:`test_a_prefix_produces_a_prefix_of_the_output`.
If feeding the first k bars produces exactly the output that feeding all bars
produced for those k bars, then no later bar can alter, revise, or retroactively
create an earlier feature. Every other look-ahead test is a specific instance of
that general property, kept separately because a specific failure is easier to
diagnose than a general one.
"""

from datetime import datetime, timedelta, timezone

import pytest

from ai_trading.features.ict_objective import (
    ATR_PERIOD,
    DISPLACEMENT_ATR_MULT,
    EQUAL_TOLERANCE_ATR,
    FVG_MIN_ATR,
    OBJECTIVE_FEATURE_SPECS,
    Candle,
    Direction,
    FeatureRegistryError,
    ObjectiveFeatureEngine,
    ObjectiveFeatureRegistry,
    RollingATR,
    RollingSwings,
    FEATURE_REGISTRY,
)
from ai_trading.knowledge import ONTOLOGY, ConceptObjectivity
from ai_trading.storage.features import FeatureSnapshot
from ai_trading.storage.records import TemporalIntegrityError

UTC = timezone.utc
START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


def bar(index, open_, high, low, close, volume=100.0):
    event = START + timedelta(minutes=index)
    return Candle(index, event, event + timedelta(minutes=1),
                  open_, high, low, close, volume, "NQ", "1m")


def flat_bars(count, price=20_000.0, spread=2.0, start=0):
    """Quiet bars: enough ATR history without triggering anything."""
    return [bar(start + i, price, price + spread, price - spread, price)
            for i in range(count)]


def engine(**kw):
    return ObjectiveFeatureEngine(instrument="NQ", timeframe="1m", **kw)


def names(snapshots):
    return [s.name for s in snapshots]


# =========================================================================
# Rolling ATR -- prevention of whole-array leakage
# =========================================================================


def test_the_atr_has_no_whole_series_entry_point():
    """The audited defect was calc_atr(entire_array). No such API exists."""
    for forbidden in ("from_series", "compute", "of_array", "backfill"):
        assert not hasattr(RollingATR, forbidden)


def test_the_atr_is_none_until_it_is_warm():
    atr = RollingATR(period=14)
    for candle in flat_bars(10):
        atr.update(candle)
    assert atr.value is None
    assert not atr.is_warm


def test_the_atr_at_a_bar_uses_only_bars_up_to_it():
    """Feeding future bars afterwards cannot change an earlier reading."""
    bars = flat_bars(20)
    atr = RollingATR()
    for candle in bars:
        atr.update(candle)
    early = atr.value

    shock = bar(20, 20_000.0, 25_000.0, 15_000.0, 24_000.0)
    replay = RollingATR()
    for candle in bars:
        replay.update(candle)
    assert replay.value == early          # unchanged before the shock
    replay.update(shock)
    assert replay.value != early          # and only changes once it arrives


def test_a_volatility_shock_does_not_reach_backwards():
    calm = flat_bars(30)
    shock = [bar(30, 20_000.0, 26_000.0, 14_000.0, 25_000.0)]

    without = RollingATR()
    for candle in calm:
        without.update(candle)

    with_shock = RollingATR()
    for candle in calm + shock:
        with_shock.update(candle)
    # The shock changes the current reading, never the historical one.
    assert without.value != with_shock.value
    assert without.value is not None


# =========================================================================
# Swings -- prevention of centred pivot leakage
# =========================================================================


def test_a_pivot_is_emitted_only_at_its_confirmation_bar():
    swings = RollingSwings(left=2, right=2)
    bars = [bar(0, 100, 101, 99, 100), bar(1, 100, 102, 99, 100),
            bar(2, 100, 110, 99, 100),          # the pivot
            bar(3, 100, 103, 99, 100), bar(4, 100, 104, 99, 100)]

    emitted = [swings.update(candle) for candle in bars]
    assert emitted[:4] == [[], [], [], []]      # nothing before bar 4
    assert emitted[4]                            # confirmed at bar 4


def test_a_pivot_records_formation_and_confirmation_separately():
    swings = RollingSwings(left=2, right=2)
    found = []
    for candle in [bar(0, 100, 101, 99, 100), bar(1, 100, 102, 99, 100),
                   bar(2, 100, 110, 99, 100), bar(3, 100, 103, 99, 100),
                   bar(4, 100, 104, 99, 100)]:
        found.extend(swings.update(candle))

    pivot = next(s for s in found if s.kind is Direction.BULLISH)
    assert pivot.formed_at_index == 2
    assert pivot.confirmed_at_index == 4
    assert pivot.confirmation_lag_bars == 2
    assert pivot.available_at > pivot.formed_at


def test_a_pivot_is_not_available_at_its_formation_time():
    """The exact defect: a swing usable at the bar it happened on."""
    swings = RollingSwings(left=2, right=2)
    found = []
    for candle in [bar(0, 100, 101, 99, 100), bar(1, 100, 102, 99, 100),
                   bar(2, 100, 110, 99, 100), bar(3, 100, 103, 99, 100),
                   bar(4, 100, 104, 99, 100)]:
        found.extend(swings.update(candle))

    pivot = found[0]
    assert not pivot.is_available_at(pivot.formed_at)
    assert pivot.is_available_at(pivot.available_at)


def test_the_swing_type_has_no_single_time_attribute():
    """Nothing to reach for that would collapse formation and confirmation."""
    from ai_trading.features.ict_objective import Swing

    fields = set(Swing.__dataclass_fields__)
    assert "time" not in fields
    assert "index" not in fields
    assert {"formed_at_index", "confirmed_at_index"} <= fields


# =========================================================================
# THE GOVERNING TEST -- prefix determinism
# =========================================================================


def volatile_bars(count=90, seed=11):
    import random

    rng = random.Random(seed)
    out, price = [], 20_000.0
    for i in range(count):
        open_ = price
        close = open_ + rng.gauss(0.0, 9.0)
        high = max(open_, close) + abs(rng.gauss(0.0, 5.0))
        low = min(open_, close) - abs(rng.gauss(0.0, 5.0))
        out.append(bar(i, open_, high, low, close))
        price = close
    return out


def test_a_prefix_produces_a_prefix_of_the_output():
    """No later bar can alter, revise, or create an earlier feature.

    The general statement of look-ahead safety. Every specific attack below is
    an instance of it.
    """
    bars = volatile_bars()
    full = engine()
    full.run(bars)
    full_ids = [(s.name, s.event_time, str(s.value))
                for s in full.snapshots()]

    for cut in (20, 40, 60, 80):
        partial = engine()
        partial.run(bars[:cut])
        partial_ids = [(s.name, s.event_time, str(s.value))
                       for s in partial.snapshots()]
        assert partial_ids == full_ids[:len(partial_ids)], f"diverged at {cut}"


def test_the_engine_is_deterministic():
    bars = volatile_bars()
    first, second = engine(), engine()
    first.run(bars)
    second.run(bars)
    assert [s.provenance_id for s in first.snapshots()] == \
        [s.provenance_id for s in second.snapshots()]


def test_bars_must_arrive_in_order():
    """A replay that can go backwards can still be made to leak."""
    live = engine()
    live.on_bar(bar(5, 100, 101, 99, 100))
    with pytest.raises(ValueError, match="must arrive in order"):
        live.on_bar(bar(4, 100, 101, 99, 100))


# =========================================================================
# Fair Value Gap
# =========================================================================


def fvg_bars():
    """Quiet history, then a clean bullish three-candle gap."""
    bars = flat_bars(30, price=20_000.0, spread=3.0)
    bars.append(bar(30, 20_000.0, 20_005.0, 19_995.0, 20_002.0))
    bars.append(bar(31, 20_002.0, 20_060.0, 20_001.0, 20_055.0))
    bars.append(bar(32, 20_055.0, 20_070.0, 20_030.0, 20_065.0))
    return bars


def test_a_bullish_fvg_is_detected():
    live = engine()
    live.run(fvg_bars())
    gaps = [g for e in live.emissions for g in e.fvgs]
    assert gaps
    assert gaps[0].direction is Direction.BULLISH
    assert gaps[0].bottom == pytest.approx(20_005.0)
    assert gaps[0].top == pytest.approx(20_030.0)


def test_a_bearish_fvg_is_detected():
    bars = flat_bars(30, price=20_000.0, spread=3.0)
    bars.append(bar(30, 20_000.0, 20_005.0, 19_995.0, 19_998.0))
    bars.append(bar(31, 19_998.0, 19_999.0, 19_940.0, 19_945.0))
    bars.append(bar(32, 19_945.0, 19_970.0, 19_930.0, 19_935.0))
    live = engine()
    live.run(bars)
    gaps = [g for e in live.emissions for g in e.fvgs]
    assert gaps and gaps[0].direction is Direction.BEARISH


def test_an_fvg_forms_at_the_middle_bar_and_is_available_at_the_third():
    live = engine()
    live.run(fvg_bars())
    gap = [g for e in live.emissions for g in e.fvgs][0]
    assert gap.formed_at_index == 31
    assert gap.available_at_index == 32
    assert gap.confirmation_lag_bars == 1


def test_an_fvg_is_emitted_only_when_the_third_candle_arrives():
    """A future candle cannot create an earlier FVG."""
    bars = fvg_bars()
    without_third = engine()
    without_third.run(bars[:-1])
    assert not [g for e in without_third.emissions for g in e.fvgs]

    with_third = engine()
    with_third.run(bars)
    assert [g for e in with_third.emissions for g in e.fvgs]


def test_the_fvg_appears_on_the_third_bars_emission_not_the_middle_bars():
    live = engine()
    live.run(fvg_bars())
    by_index = {e.bar_index: e for e in live.emissions}
    assert not by_index[31].fvgs
    assert by_index[32].fvgs


def test_a_gap_smaller_than_the_tolerance_is_rejected():
    bars = flat_bars(30, price=20_000.0, spread=3.0)
    bars.append(bar(30, 20_000.0, 20_001.0, 19_999.0, 20_000.0))
    bars.append(bar(31, 20_000.0, 20_002.0, 19_999.5, 20_001.0))
    bars.append(bar(32, 20_001.0, 20_003.0, 20_001.05, 20_002.0))
    live = engine()
    live.run(bars)
    assert not [g for e in live.emissions for g in e.fvgs]


def test_the_fvg_record_carries_no_mitigation_field():
    """The forward-scanning defect has nowhere to live."""
    live = engine()
    live.run(fvg_bars())
    payload = [g for e in live.emissions for g in e.fvgs][0].to_dict()
    for forbidden in ("mitigation", "mitigation_pct", "filled", "age_bars"):
        assert forbidden not in payload


def test_mitigation_requires_an_explicit_decision_index():
    live = engine()
    bars = fvg_bars()
    live.run(bars)
    gap = [g for e in live.emissions for g in e.fvgs][0]
    assert gap.mitigation_as_of(bars, 32) == 0.0


def test_mitigation_never_reads_past_the_decision_bar():
    bars = fvg_bars()
    live = engine()
    live.run(bars)
    gap = [g for e in live.emissions for g in e.fvgs][0]

    filler = bar(33, 20_065.0, 20_066.0, 20_000.0, 20_010.0)   # fills the gap
    at_32 = gap.mitigation_as_of(bars + [filler], 32)
    at_33 = gap.mitigation_as_of(bars + [filler], 33)
    assert at_32 == 0.0        # the filler is invisible at bar 32
    assert at_33 > 0.0


def test_mitigation_before_availability_is_refused():
    bars = fvg_bars()
    live = engine()
    live.run(bars)
    gap = [g for e in live.emissions for g in e.fvgs][0]
    with pytest.raises(ValueError, match="not available until"):
        gap.mitigation_as_of(bars, 31)


def test_age_is_relative_to_an_explicit_bar_not_an_array_end():
    """The array-relative age_bars defect, prevented by the signature."""
    live = engine()
    live.run(fvg_bars())
    gap = [g for e in live.emissions for g in e.fvgs][0]
    assert gap.age_bars_as_of(40) == 9
    assert gap.age_bars_as_of(32) == 1
    with pytest.raises(ValueError, match="not available until"):
        gap.age_bars_as_of(31)


# =========================================================================
# Displacement
# =========================================================================


def displacement_bars():
    bars = flat_bars(30, price=20_000.0, spread=3.0)
    bars.append(bar(30, 20_000.0, 20_205.0, 19_999.0, 20_200.0))
    return bars


def test_a_displacement_is_detected_and_measured():
    live = engine()
    live.run(displacement_bars())
    moves = [d for e in live.emissions for d in e.displacements]
    assert len(moves) == 1
    move = moves[0]
    assert move.direction is Direction.BULLISH
    assert move.displacement_atr > DISPLACEMENT_ATR_MULT
    assert move.displacement_strength == move.displacement_atr
    assert 0.0 < move.body_range <= 1.0
    assert move.range_atr > 0


def test_a_displacement_is_available_at_its_own_close():
    live = engine()
    live.run(displacement_bars())
    move = [d for e in live.emissions for d in e.displacements][0]
    assert move.confirmation_lag_bars == 0


def test_future_volatility_cannot_alter_a_prior_displacement():
    """The whole-array ATR defect, attacked directly."""
    bars = displacement_bars()
    early = engine()
    early.run(bars)
    early_move = [d for e in early.emissions for d in e.displacements][0]

    later = engine()
    later.run(bars + [bar(31, 20_200.0, 26_000.0, 14_000.0, 15_000.0)])
    same_move = [d for e in later.emissions for d in e.displacements
                 if d.bar_index == 30][0]

    assert same_move.atr == early_move.atr
    assert same_move.displacement_atr == early_move.displacement_atr


def test_a_quiet_bar_is_not_a_displacement():
    live = engine()
    live.run(flat_bars(40))
    assert not [d for e in live.emissions for d in e.displacements]


def test_no_displacement_before_the_atr_is_warm():
    """A threshold with no volatility estimate would classify everything."""
    live = engine()
    live.run([bar(0, 20_000.0, 20_400.0, 19_990.0, 20_390.0)])
    assert not [d for e in live.emissions for d in e.displacements]


# =========================================================================
# Equal High / Equal Low
# =========================================================================


def equal_high_bars(second_price=110.0):
    """Two pivots at the same high, separated and confirmed."""
    bars = flat_bars(30, price=100.0, spread=1.0)
    bars += [bar(30, 100, 102, 99, 100), bar(31, 100, 103, 99, 100),
             bar(32, 100, 110, 99, 100),                  # first pivot
             bar(33, 100, 104, 99, 100), bar(34, 100, 103, 99, 100),
             bar(35, 100, 102, 99, 100), bar(36, 100, 103, 99, 100),
             bar(37, 100, second_price, 99, 100),         # second pivot
             bar(38, 100, 104, 99, 100), bar(39, 100, 103, 99, 100)]
    return bars


def test_equal_highs_are_detected_within_tolerance():
    live = engine()
    live.run(equal_high_bars(110.0))
    levels = [l for e in live.emissions for l in e.equal_highs]
    assert levels
    assert levels[0].kind is Direction.BULLISH
    assert levels[0].price_difference == pytest.approx(0.0)


def test_exact_float_equality_is_not_required():
    """Two ticks apart still counts, which is the point of a tolerance."""
    live = engine()
    live.run(equal_high_bars(110.25))
    levels = [l for e in live.emissions for l in e.equal_highs]
    assert levels
    assert 0 < levels[0].price_difference <= levels[0].tolerance


def test_a_difference_beyond_tolerance_is_rejected():
    live = engine()
    live.run(equal_high_bars(140.0))
    assert not [l for e in live.emissions for l in e.equal_highs]


def test_the_tolerance_is_atr_relative_and_versioned():
    live = engine()
    live.run(equal_high_bars(110.0))
    level = [l for e in live.emissions for l in e.equal_highs][0]
    assert level.tolerance_atr == EQUAL_TOLERANCE_ATR
    spec = FEATURE_REGISTRY.require("equal_high:v1")
    assert spec.parameters["tolerance_atr"] == EQUAL_TOLERANCE_ATR


def test_equal_lows_are_detected():
    bars = flat_bars(30, price=100.0, spread=1.0)
    bars += [bar(30, 100, 101, 98, 100), bar(31, 100, 101, 97, 100),
             bar(32, 100, 101, 90, 100),
             bar(33, 100, 101, 96, 100), bar(34, 100, 101, 97, 100),
             bar(35, 100, 101, 98, 100), bar(36, 100, 101, 97, 100),
             bar(37, 100, 101, 90, 100),
             bar(38, 100, 101, 96, 100), bar(39, 100, 101, 97, 100)]
    live = engine()
    live.run(bars)
    levels = [l for e in live.emissions for l in e.equal_lows]
    assert levels and levels[0].kind is Direction.BEARISH


def test_an_equality_is_available_only_at_the_later_confirmation():
    live = engine()
    live.run(equal_high_bars(110.0))
    level = [l for e in live.emissions for l in e.equal_highs][0]
    assert level.available_at_index == level.second_swing.confirmed_at_index
    assert level.available_at_index > level.second_swing.formed_at_index


def test_a_future_bar_cannot_join_a_historical_equality():
    """The equality decided at bar k must not change when bar k+1 arrives."""
    bars = equal_high_bars(110.0)
    early = engine()
    early.run(bars)
    early_levels = [l.to_dict() for e in early.emissions for l in e.equal_highs]

    later = engine()
    later.run(bars + [bar(40, 100, 110, 99, 100), bar(41, 100, 103, 99, 100),
                      bar(42, 100, 102, 99, 100)])
    later_levels = [l.to_dict() for e in later.emissions for l in e.equal_highs]

    assert later_levels[:len(early_levels)] == early_levels


def test_pivots_too_close_together_are_not_paired():
    bars = flat_bars(30, price=100.0, spread=1.0)
    bars += [bar(30, 100, 102, 99, 100), bar(31, 100, 103, 99, 100),
             bar(32, 100, 110, 99, 100), bar(33, 100, 104, 99, 100),
             bar(34, 100, 110, 99, 100), bar(35, 100, 104, 99, 100),
             bar(36, 100, 103, 99, 100)]
    live = engine()
    live.run(bars)
    for level in [l for e in live.emissions for l in e.equal_highs]:
        assert level.separation_bars >= 3


# =========================================================================
# Liquidity Sweep
# =========================================================================


def sweep_bars():
    """A confirmed swing high, then a bar that pierces and closes back."""
    bars = flat_bars(30, price=100.0, spread=1.0)
    bars += [bar(30, 100, 102, 99, 100), bar(31, 100, 103, 99, 100),
             bar(32, 100, 110, 99, 100),                 # pivot forms
             bar(33, 100, 104, 99, 100),
             bar(34, 100, 103, 99, 100),                 # pivot confirms here
             bar(35, 100, 115, 99, 101)]                 # sweeps it
    return bars


def test_a_sweep_is_detected_against_a_confirmed_level():
    live = engine()
    live.run(sweep_bars())
    sweeps = [s for e in live.emissions for s in e.sweeps]
    assert sweeps
    sweep = sweeps[0]
    assert sweep.direction is Direction.BULLISH
    assert sweep.reference_price == pytest.approx(110.0)
    assert sweep.sweep_bar_index == 35


def test_a_sweep_records_all_four_timestamps_separately():
    live = engine()
    live.run(sweep_bars())
    sweep = [s for e in live.emissions for s in e.sweeps][0]
    assert sweep.reference_formed_index == 32
    assert sweep.reference_confirmed_index == 34
    assert sweep.sweep_bar_index == 35
    assert sweep.reference_level_time < sweep.reference_confirmed_at
    assert sweep.reference_confirmed_at <= sweep.sweep_event_time
    assert sweep.available_at >= sweep.sweep_event_time


def test_the_reference_must_be_confirmed_before_the_sweep_bar():
    live = engine()
    live.run(sweep_bars())
    for sweep in [s for e in live.emissions for s in e.sweeps]:
        assert sweep.reference_confirmed_index < sweep.sweep_bar_index


def test_a_future_swing_cannot_be_backdated_into_a_sweep():
    """A sweep at bar k must not appear once a later pivot confirms."""
    bars = sweep_bars()
    early = engine()
    early.run(bars[:35])          # up to and including bar 34
    early_sweeps = [s.to_dict() for e in early.emissions for s in e.sweeps]

    later = engine()
    later.run(bars)
    later_sweeps = [s.to_dict() for e in later.emissions for s in e.sweeps]
    assert later_sweeps[:len(early_sweeps)] == early_sweeps


def test_a_bar_that_closes_beyond_the_level_is_not_a_sweep():
    bars = flat_bars(30, price=100.0, spread=1.0)
    bars += [bar(30, 100, 102, 99, 100), bar(31, 100, 103, 99, 100),
             bar(32, 100, 110, 99, 100), bar(33, 100, 104, 99, 100),
             bar(34, 100, 103, 99, 100),
             bar(35, 100, 120, 99, 118)]        # breaks out, does not close back
    live = engine()
    live.run(bars)
    assert not [s for e in live.emissions for s in e.sweeps]


def test_a_sweep_cannot_reference_a_pivot_confirmed_on_the_same_bar():
    """The pivot and the sweep would be the same information instant."""
    live = engine()
    live.run(sweep_bars())
    for emission in live.emissions:
        confirmed_here = {s.formed_at_index for s in emission.swings}
        for sweep in emission.sweeps:
            assert sweep.reference_formed_index not in confirmed_here


# =========================================================================
# Temporal contract via FeatureSnapshot / derive_feature
# =========================================================================


def test_every_emission_is_a_feature_snapshot():
    live = engine()
    live.run(volatile_bars())
    assert live.snapshots()
    for snapshot in live.snapshots():
        assert isinstance(snapshot, FeatureSnapshot)


def test_availability_is_never_earlier_than_the_event():
    live = engine()
    live.run(volatile_bars())
    for snapshot in live.snapshots():
        assert snapshot.available_at >= snapshot.event_time


def test_every_snapshot_carries_instrument_and_timeframe():
    live = engine()
    live.run(volatile_bars())
    for snapshot in live.snapshots():
        assert snapshot.instrument == "NQ"
        assert snapshot.timeframe == "1m"


def test_snapshots_are_versioned():
    live = engine()
    live.run(volatile_bars())
    for snapshot in live.snapshots():
        assert snapshot.key.endswith(":v1")


def test_derive_feature_rejects_an_availability_before_its_inputs():
    """The existing temporal architecture is not bypassed."""
    from ai_trading.storage.features import derive_feature

    candle = bar(0, 100, 101, 99, 100)
    with pytest.raises(TemporalIntegrityError):
        derive_feature("x", [candle.as_observation()], lambda v: 1,
                       available_at=candle.event_time - timedelta(hours=1))


def test_a_candle_cannot_be_available_before_it_happened():
    event = START
    with pytest.raises(ValueError, match="precedes event_time"):
        Candle(0, event, event - timedelta(minutes=1), 100, 101, 99, 100)


def test_an_impossible_candle_is_refused():
    with pytest.raises(ValueError, match="high < low"):
        bar(0, 100, 90, 110, 100)


# =========================================================================
# Registry and versioning
# =========================================================================


def test_the_five_features_are_registered():
    assert FEATURE_REGISTRY.keys() == [
        "displacement:v1", "equal_high:v1", "equal_low:v1", "fvg:v1",
        "liquidity_sweep:v1",
    ]


def test_every_spec_states_the_full_contract():
    for spec in OBJECTIVE_FEATURE_SPECS:
        assert spec.concept_name and spec.operational_definition
        assert spec.temporal_rule and spec.required_inputs
        assert spec.output_schema
        assert spec.definition_version == "v1"
        assert spec.classification == "OBJECTIVE"
        assert spec.research_status == "UNTESTED"


def test_implementation_is_not_evidence():
    for spec in OBJECTIVE_FEATURE_SPECS:
        assert not spec.has_evidence
        assert spec.research_status == "UNTESTED"
    assert FEATURE_REGISTRY.summary()["with_evidence"] == 0


def test_silent_redefinition_is_refused():
    registry = ObjectiveFeatureRegistry()
    spec = OBJECTIVE_FEATURE_SPECS[0]
    registry.register(spec)
    registry.register(spec)              # identical is fine

    from dataclasses import replace
    changed = replace(spec, operational_definition="something else")
    with pytest.raises(FeatureRegistryError, match="register it as"):
        registry.register(changed)


def test_a_changed_definition_becomes_a_new_version():
    from dataclasses import replace

    registry = ObjectiveFeatureRegistry()
    spec = OBJECTIVE_FEATURE_SPECS[0]
    registry.register(spec)
    registry.register(replace(spec, definition_version="v2",
                              operational_definition="a revised rule"))
    assert registry.keys() == ["fvg:v1", "fvg:v2"]


def test_an_unregistered_feature_is_refused():
    with pytest.raises(FeatureRegistryError, match="not a registered feature"):
        FEATURE_REGISTRY.require("order_block:v1")


def test_every_spec_records_its_parameters():
    """Parameters are part of the definition; changing one is a v2."""
    assert FEATURE_REGISTRY.require("fvg:v1").parameters["min_size_atr"] == FVG_MIN_ATR
    assert FEATURE_REGISTRY.require("displacement:v1").parameters[
        "atr_multiple"] == DISPLACEMENT_ATR_MULT
    assert FEATURE_REGISTRY.require("fvg:v1").parameters["atr_period"] == ATR_PERIOD


# =========================================================================
# Scope -- the deferred concepts stay deferred
# =========================================================================


def test_only_the_five_objective_concepts_are_implemented():
    implemented = {s.concept_name for s in OBJECTIVE_FEATURE_SPECS}
    assert implemented == {"Fair Value Gap", "Displacement", "Equal High",
                           "Equal Low", "Liquidity Sweep"}


def test_the_implemented_set_matches_the_ontology_objective_set():
    implemented = {s.concept_name for s in OBJECTIVE_FEATURE_SPECS}
    objective = {c.canonical_name for c in
                 ONTOLOGY.by_objectivity(ConceptObjectivity.OBJECTIVE)}
    assert implemented == objective


@pytest.mark.parametrize("deferred", [
    "Order Block", "Market Structure Shift", "BOS", "CHoCH", "Protected High",
    "Protected Low", "Premium", "Discount", "Equilibrium", "Breaker Block",
    "Killzone", "SMT Divergence", "Inducement",
])
def test_no_deferred_concept_has_a_feature(deferred):
    concept = ONTOLOGY.require(deferred)
    assert not concept.may_enter_feature_engine
    for spec in OBJECTIVE_FEATURE_SPECS:
        assert spec.concept_name != deferred


def test_thirteen_concepts_remain_deferred():
    deferred = [c for c in ONTOLOGY.all() if not c.may_enter_feature_engine]
    assert len(deferred) == 13


# =========================================================================
# Degenerate inputs
# =========================================================================


def test_an_empty_stream_emits_nothing():
    live = engine()
    assert live.run([]) == []
    assert live.snapshots() == []


def test_a_short_stream_emits_nothing():
    live = engine()
    live.run(flat_bars(3))
    assert live.snapshots() == []


def test_a_constant_series_produces_no_features():
    """Zero true range means no ATR basis, so nothing is classified."""
    live = engine()
    live.run([bar(i, 100.0, 100.0, 100.0, 100.0) for i in range(60)])
    assert not [d for e in live.emissions for d in e.displacements]
    assert not [g for e in live.emissions for g in e.fvgs]


def test_the_engine_reports_atr_warmth_per_bar():
    live = engine()
    live.run(flat_bars(20))
    assert live.emissions[0].atr is None
    assert live.emissions[-1].atr is not None
