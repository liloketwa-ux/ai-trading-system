# Objective ICT Features (Phase 10)

Five concepts implemented. Thirteen deferred. **All five are `UNTESTED`.**

These are **our** operational definitions. OpenMobius was a terminology
reference; its structural implementation was audited, found to contain four
distinct future-information mechanisms, and is not reproduced here.

---

## The architectural claim

`ObjectiveFeatureEngine` is a **streaming** computer. Bars arrive one at a time
through `on_bar()`, and the engine physically cannot see a bar that has not
arrived. There is no array to accidentally scan to the end of, no whole-series
ATR to backfill, and no way for a later bar to change an earlier emission.

That last property is directly testable and is the governing test of this
phase:

```python
def test_a_prefix_produces_a_prefix_of_the_output():
    # feeding bars[:k] produces exactly the output that feeding all bars
    # produced for those k bars, at k in {20, 40, 60, 80}
```

If that holds, no later bar can alter, revise, or retroactively create an
earlier feature. Every specific look-ahead attack below is an instance of it,
kept separate because a specific failure is easier to diagnose.

Bars must also arrive in order — `on_bar` refuses a bar that does not follow the
last one, because a replay that can go backwards is the one remaining way to
make a streaming engine leak.

## The four OpenMobius defects, and how each is prevented

| Defect | Prevention |
|---|---|
| Centred fractal pivots emitted at bar `i` | `RollingSwings` emits only at the confirmation bar, carrying `formed_at` **and** `confirmed_at`; `available_at = confirmed_at` |
| Whole-array ATR backfilled onto every bar | `RollingATR` is incremental and has **no method accepting a series** — the defect has no API to occur through |
| Forward mitigation scanning | `FairValueGap` has **no mitigation field**; mitigation is `mitigation_as_of(bars, decision_index)` and never reads past the index |
| Array-relative `age_bars` | No age is stored; `age_bars_as_of(decision_index)` requires the bar explicitly |

Each is asserted by a dedicated test, including `test_the_atr_has_no_whole_series_entry_point`
and `test_the_fvg_record_carries_no_mitigation_field`.

---

## `fvg:v1` — Fair Value Gap

**Definition.** Bullish when `high[i] < low[i+2]`; bearish when
`low[i] > high[i+2]`. Gap band is `(high[i], low[i+2])` or `(high[i+2], low[i])`.
Rejected when the band is smaller than `0.2 × ATR(14)` measured **as of bar
i+2**.

**Temporal semantics.**

| | |
|---|---|
| `formed_at` | bar `i+1` (the middle bar) |
| `available_at` | close of bar `i+2` |
| Confirmation lag | 1 bar |

The third candle is required to observe the gap at all, so the gap is emitted
on bar `i+2`'s emission and never on bar `i+1`'s. This is the one-bar
mislabelling found in the reference implementation, corrected.

**Inputs.** `bar[i]`, `bar[i+1]`, `bar[i+2]`, `atr:v1@i+2`.

**Mitigation is not part of the feature.** It is a future outcome relative to
formation, and storing it on the record is exactly how the reference
implementation ended up scanning to the end of the array. Instead:

```python
gap.mitigation_as_of(bars, decision_index)   # never reads past decision_index
```

Asserted: a bar that fills the gap at index 33 is invisible to a query at
index 32.

**Limitations.** The `0.2 ATR` floor is ours; sources state no minimum. A gap
whose third candle is missing from the data is never detected — there is no
interpolation.

---

## `displacement:v1` — Displacement

**Definition.** `|close − open| ≥ 2.0 × ATR(14)`, where ATR is computed
incrementally on bars `≤ i`. Direction from `sign(close − open)`.

**Outputs.** `range`, `atr`, `range_atr`, `body_range`, `displacement_atr`,
`displacement_direction`, `displacement_strength`.

**Temporal semantics.** `formed_at = available_at = close of bar i`.
Confirmation lag **0**. No future bar participates, and the ATR window ends at
bar `i`.

This is the concept the whole-array ATR defect corrupted: the event itself is a
clean single-bar test, and only the normaliser made it retrospective. Asserted
directly — a 26,000/14,000 shock bar appended afterwards leaves the earlier
displacement's `atr` and `displacement_atr` byte-identical.

**Limitations.** No displacement is emitted before the ATR is warm (14 true
ranges). A threshold with no volatility estimate would classify everything, so
the feature stays silent rather than guessing.

---

## `equal_high:v1` / `equal_low:v1`

**Definition.** Two confirmed swing highs (lows) whose prices differ by no more
than `0.1 × ATR(14)` measured **as of the later pivot's confirmation bar**,
separated by at least **3 bars**.

**No exact float equality.** The tolerance is ATR-relative and versioned as
`tolerance_atr = 0.1`, recorded in the spec's `parameters` so a change forces a
`v2`.

**Temporal semantics.** `available_at` = confirmation bar of the **later**
pivot, which is itself `formed_at + 2`. Emitted at that bar and never dissolved
by later bars: a level that was equal remains a historical fact.

**Limitations.** Only the nearest qualifying earlier pivot is paired, so a
triple-top yields two pairs rather than one three-way level. The 3-bar
separation and the tolerance are both ours; sources say "roughly equal" and
specify neither.

---

## `liquidity_sweep:v1`

**Definition.** For a swing high at price `P` confirmed **at or before bar
i−1**: bar `i` sweeps when `high[i] > P` **and** `close[i] < P`. Mirror for
swing lows.

**Four timestamps, kept separate:**

| Field | Meaning |
|---|---|
| `reference_level_time` | when the swing formed |
| `reference_confirmed_at` | when the swing became knowable |
| `sweep_event_time` | the sweeping bar |
| `available_at` | close of the sweeping bar |

The reference must be confirmed **strictly before** the sweep bar — a pivot
confirmed on the same bar as the sweep would be the same information instant,
and is excluded by test.

**The reference is never created retroactively.** Asserted: sweeps emitted up to
bar 34 are unchanged when bars 35+ arrive and new pivots confirm.

**Limitations.** Only the most recent qualifying reference is used, so one bar
sweeping several stacked levels reports one event. A bar that pierces and
closes *beyond* the level is a breakout, not a sweep, and is not emitted.

---

## Swing references (`swing:v1`, internal)

Not a registered research feature — an input to the two above. Fractal with
`left = 2`, `right = 2`.

**Strict comparison on both sides.** This was a defect caught during
implementation: with non-strict (`>=`) comparison every bar of a flat region is
a pivot, and a perfectly flat 10-bar series produced **12 spurious pivots**.
Those cascaded into false equal-highs (identical by construction) and false
sweeps. Strict comparison yields **0**. A genuine double top at the same price
is separated by more than this window and is what Equal High exists to express.

The `Swing` type deliberately has **no single time attribute** — no `time`, no
`index` — so formation and confirmation cannot be collapsed by reaching for the
convenient one.

---

## Temporal contract

Every emission is a `FeatureSnapshot` built through `derive_feature()`, so
availability composes as the **maximum over inputs**. Asserted for every
snapshot: `available_at >= event_time`, instrument and timeframe present, key
ends `:v1`.

The existing architecture is not bypassed — `derive_feature` still rejects an
explicit `available_at` earlier than its inputs', and that is tested here too.

## Versioning

```
fvg:v1   displacement:v1   equal_high:v1   equal_low:v1   liquidity_sweep:v1
```

`ObjectiveFeatureRegistry.register()` refuses a changed definition under an
existing key:

> `fvg:v1` is already registered with a different definition. Changing a
> definition creates a new version and therefore a new research lineage —
> register it as `fvg:v2` instead of redefining `fvg:v1` in place.

Parameters are part of the definition. `atr_period`, `min_size_atr`,
`atr_multiple`, `tolerance_atr`, `min_separation_bars`, `swing_left`,
`swing_right` are all recorded in the spec, so changing any of them is a `v2`
and a new hypothesis lineage.

## Research status

**All five: `UNTESTED`.** `has_evidence` returns `False` for every spec, and
the registry summary reports `with_evidence: 0`.

Implementation is not evidence. Nothing here computes a win rate, an
expectancy, or a signal, and no concept has been promoted to a strategy.

## Deferred (13)

Partially objective: Order Block · Market Structure Shift · BOS · CHoCH ·
Protected High · Protected Low · Premium · Discount · Equilibrium ·
Breaker Block · Killzone

Subjective: SMT Divergence · Inducement

A parametrised test asserts none of them has a feature and that
`may_enter_feature_engine` is `False` for each.
