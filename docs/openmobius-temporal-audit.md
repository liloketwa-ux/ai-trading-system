# OpenMobius Temporal Audit

Target: `scripts/kb_klines.py`, inspected 2026-08-17.
Machine-readable form: `ai_trading.knowledge.temporal_audit`.

---

## Verdict

**Ten of eleven audited outputs may not enter the research engine.** The single
exception is `volume_anomaly`. **No structural output survives** — not swings,
not order blocks, not FVG mitigation, not BOS/CHoCH, not premium/discount.

This is not a criticism of the tool. It annotates a chart a human is looking at
*now*, where "the next two bars exist" is trivially true. The outputs become
unsafe only when replayed as though they had been available at the bar they are
labelled with — which is exactly what importing them into historical research
would do.

## Findings

| Output | Function | Class | Lag |
|---|---|---|---|
| `swing_pivot` | `find_swings(left=2, right=2)` | **RETROSPECTIVE** | 2 |
| `fair_value_gap` | `find_fvgs` | DELAYED_CONFIRMATION | 1 |
| `fvg_mitigation_pct` | `_fvg_mitigation_pct` | **RETROSPECTIVE** | — |
| `order_block` | `find_order_blocks` | **RETROSPECTIVE** | 3 |
| `liquidity_sweep` | `find_sweeps` | **RETROSPECTIVE** | 2 |
| `displacement` | `find_displacements` | **RETROSPECTIVE** | — |
| `bos_choch` | `analyze_structure` | **RETROSPECTIVE** | 2 |
| `trailing_extremes` | remote API | **UNKNOWN** | — |
| `premium_discount_equilibrium` | remote API | **UNKNOWN** | — |
| `equal_highs_lows` | remote API | **UNKNOWN** | — |
| `volume_anomaly` | `find_volume_anomalies` | ✅ POINT_IN_TIME_SAFE | 0 |

`UNKNOWN` is barred alongside `RETROSPECTIVE`. An opaque server-side
computation is not innocent until proven guilty — the cost of being wrong is a
silently inflated backtest.

## Four mechanisms

They need different fixes, so they are worth separating.

### 1. Centred confirmation

```python
def find_swings(candles, left=2, right=2):
    for i in range(left, len(candles) - right):
        if all(c.high >= candles[i + k].high for k in range(1, right + 1)):
            out.append({"index": i, "price": c.high, "kind": "high"})
```

The pivot at `i` requires bars `i+1` and `i+2`. The record says `index: i` and
carries **no confirmation index**, so a consumer cannot recover the delay even
if they know it exists.

`find_sweeps` and `analyze_structure` both consume this list and inherit the
defect. That is why BOS/CHoCH is retrospective despite its own logic being
backward-looking: its input is not.

### 2. Whole-array normalisation

```python
def calc_atr(candles, period=14):
    ...
    return sum(trs[-period:]) / period      # last 14 TRs of the array
```

Called once on the full array, then used as a fixed threshold at every index.
**An event at bar 50 is classified using volatility from bar 3,000.**

Affects `find_fvgs` (minimum gap size), `find_order_blocks` (displacement
threshold) and `find_displacements` (body threshold). `displacement` is
otherwise a clean single-bar test — the normaliser alone makes it unsafe.

### 3. Forward scanning

`_fvg_mitigation_pct` walks every bar from formation to the end of the array.
There is no delay to model: the quantity is a summary of the future by
construction. Not importable at any lag.

### 4. Array-relative age

Every record carries `age_bars = n - 1 - i`, measured from the end of whatever
array was passed. Meaningful only for the call that produced it; a stored
record silently misstates age. Quieter than the others and just as corrupting
once records are persisted.

## The mislabelling that matters most

Order blocks are the sharpest case:

```python
for i in range(n - 3):
    next3 = candles[i + 1:i + 4]        # three future bars
    if move > threshold and cum_up > threshold:
        out.append({"formed_at_index": i, ...})   # attributed to bar i
```

Three bars of lookahead presented as a property of bar `i`. A backtest entering
at the order block's bar would be trading on information that arrives three
bars later — and the equity curve would look excellent.

The repository's own documentation acknowledges that pivots need later bars and
that order blocks may be revised by subsequent price action. **Revision is the
deeper problem**: a zone that changes as price develops has no stable historical
value at all. Our `OB:v1` therefore never revises — a later displacement
creates a new block rather than moving an old one.

## Required remediation

Every concept we operationalise records formation and confirmation separately:

```
pivot_formed_at    = i          # when it happened
pivot_confirmed_at = i + right  # when it became knowable
available_at       = pivot_confirmed_at
```

**not** `available_at = pivot_formed_at`.

| Concept | `formed_at` | `available_at` |
|---|---|---|
| Fair Value Gap | bar i+1 | close of bar **i+2** |
| Displacement | bar i | close of bar i (rolling ATR ≤ i) |
| Swing pivot | bar i | close of bar **i+right** |
| Liquidity Sweep | bar i | close of bar i (confirmed reference only) |
| Order Block | bar i | close of the **displacement bar** |
| MSS / BOS / CHoCH | breaking bar | close of the breaking bar |

These feed `FeatureSnapshot` and `derive_feature()`, so availability composes
as the maximum across inputs — a conjunction is available when its slowest
component is, not when its fastest one is.

## Enforcement

```python
assert_importable(finding)   # raises unless POINT_IN_TIME_SAFE
```

Tested: every `RETROSPECTIVE` and `UNKNOWN` finding raises;
`DELAYED_CONFIRMATION` raises too, because it is usable only after the delay is
modelled, and modelling it is our work rather than theirs.

A test asserts no structural output appears in `importable_as_is`.
