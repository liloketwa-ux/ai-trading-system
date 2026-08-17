# ICT Hypothesis Family v1 — LOCKED

> # THIS IS A RESEARCH PROTOCOL, NOT A TRADING STRATEGY.
>
> Nothing here produces a signal. Every hypothesis is a *question* about
> forward outcomes, `is_signal` returns `False` for all six, and no payload
> carries a side, entry, stop, target or size. None has been tested.

| | |
|---|---|
| Family ID | `ict-objective-family-v1` |
| Fingerprint | `b3ebb0af7f01b137` |
| Protocol | `research-protocol-v1` (frozen) |
| Creation commit | `0e333125d016…` |
| Locked | **yes**, at import |
| Hypotheses | 6 |
| **Trial count** | **36** |
| Baseline comparisons | 24 |

---

## Why lock it

A pre-registration is worth something only if it was written **before** the
data was seen and cannot be edited afterwards. `HypothesisFamily.lock()` is
one-way: there is no `unlock`, `reopen`, `edit` or `remove`, asserted absent by
test. That omission is the design — an unlock method is the only thing anyone
would ever reach for once results disappoint.

Adding a hypothesis to a locked family raises, and the message says why:

> Adding a hypothesis after locking would change the trial count that earlier
> results were corrected against.

## The six hypotheses

| ID | Parent | Conditions | Decision event |
|---|---|---|---|
| `ICT-LS-001` | — | 1 | `liquidity_sweep:v1` |
| `ICT-LS-002` | `ICT-LS-001` | 2 | `displacement:v1` |
| `ICT-FVG-001` | `ICT-LS-001` | 2 | `fvg:v1` |
| `ICT-COMBO-001` | `ICT-LS-002` | 3 | `fvg:v1` |
| `ICT-EQ-001` | `ICT-LS-001` | 3 | `liquidity_sweep:v1` |
| `ICT-COMBO-002` | `ICT-COMBO-001` | 5 | `fvg:v1` |

**ICT-LS-001** — Forward outcomes following a liquidity sweep of a previously
confirmed swing level differ from baseline selection.

**ICT-LS-002** — …a sweep followed within 3 bars by a displacement differ from
the sweep alone.

**ICT-FVG-001** — …a sweep that leaves a fair value gap within 5 bars differ
from the sweep alone.

**ICT-COMBO-001** — …sweep, then displacement within 3 bars, then a fair value
gap within 2 further bars differ from sweep plus displacement.

**ICT-EQ-001** — …a sweep of a level that formed part of an equal-high or
equal-low pair within the prior 50 bars differ from the sweep alone.

**ICT-COMBO-002** — …the full chain (equality context, sweep, displacement,
FVG) differ from the same chain without the equality context.

## Feature versions

Only the five implemented objective features. No additional indicators, no new
thresholds.

```
liquidity_sweep:v1   displacement:v1   fvg:v1   equal_high:v1   equal_low:v1
```

A test asserts every key the family names is registered in
`FEATURE_REGISTRY`, and that none of the 13 deferred concepts appears.

## Event ordering

Conjunctions are **ordered chains, not sets**. "Sweep and displacement" does
not say which came first, and the two readings are different claims about the
market. A hypothesis with more than one sequenced event and no declared
ordering is refused at construction:

> A conjunction without an ordering is an unordered set, and 'sweep then
> displacement' is a different claim from 'displacement then sweep'.

The full chain:

```
equal_high:v1 / equal_low:v1        (context, within 50 bars)
        ↓
liquidity_sweep:v1                  (trigger)
        ↓  within 3 bars
displacement:v1
        ↓  within 2 bars
fvg:v1
        ↓
decision timestamp = fvg available_at
```

`EventRole.CONTEXT` exists for the equality levels specifically. An equal-high
is not another link in a chain — it exists *before* the sweep and conditions
it. Modelling it as a sequence step would impose an ordering the concept does
not have.

`validate_ordering()` rejects an observed set whose events run backwards or
exceed a window; both are tested.

## Temporal windows

| Link | Max bars |
|---|---|
| sweep → displacement | **3** |
| displacement → FVG | **2** |
| sweep → FVG (direct, `ICT-FVG-001`) | 5 |
| equality → sweep (context lookback) | **50** |

Same-bar is permitted (`min_bars = 0`) because a displacement bar can itself
complete a sweep. Backwards never is.

**These are hypothesis parameters, not universal truths.** Changing one
produces a different family fingerprint and therefore a new family version with
a fresh trial budget — asserted by test.

## No parameter search

`build_family_v1()` **takes no parameters**, asserted by a signature test. A
build function with a threshold argument is a parameter sweep waiting to be
written.

Not run in this campaign: FVG threshold sweeps, displacement sweeps, equality
tolerance sweeps, temporal-window sweeps. Any of those is a separate
exploratory campaign with its own trial accounting.

## Decision event

Each hypothesis names exactly one, and `decision_time` is that feature's
`available_at`. For the three FVG-based setups the decision is the FVG's
availability — which is the close of the third candle, one bar after the gap
formed.

**Nothing available after the decision may contribute.**
`validate_contribution()` refuses it:

> `liquidity_sweep:v1` available 14:40:00, after the decision time 14:30:00. A
> feature available after the decision may only be an outcome label, never an
> input.

This is where a conjunction leaks most easily: the FVG that "confirms" a setup
becomes knowable a bar after the displacement, and using it at the
displacement's timestamp buys a free look at the future.

`ICT-EQ-001` decides at the **sweep**, not the equality — context precedes the
trigger, so the trigger fixes the instant.

## Incremental contribution

The family is nested, so each concept's marginal information is measurable
rather than only the final conjunction being reported.

```
baseline
  → ICT-LS-001    sweep
    → ICT-LS-002    + displacement
      → ICT-COMBO-001   + FVG
        → ICT-COMBO-002   + equality context
    → ICT-FVG-001   + FVG (skipping displacement)
    → ICT-EQ-001    + equality context
```

Five parent→child comparisons, all of which the report must show:

```
ICT-LS-001 → ICT-LS-002 → ICT-COMBO-001 → ICT-COMBO-002
ICT-LS-001 → ICT-FVG-001
ICT-LS-001 → ICT-EQ-001
```

A four-condition setup that performs identically to its one-condition parent
has told you the three extra conditions are decoration. Reporting only the
final conjunction would hide that.

## Labels

Six, all existing definitions used unmodified:

```
forward_return_5m   forward_return_15m   forward_return_30m   forward_return_1h
hit_1R_before_-1R   hit_2R_before_-1R
```

R-multiple labels resolve ties **to the stop** — bar data cannot say which side
was touched first, and assuming the favourable order inflates every result.

`MAE` and `MFE` are returned in R units alongside each R-multiple label. They
are outcome **diagnostics**, not separate labels, so they do not inflate the
trial count.

## Trial count: 36

```
6 hypotheses × 6 labels = 36
```

Labels count because testing one hypothesis against six outcomes is six looks
at the data, not one. This number is fixed at lock time and is what every
multiple-testing correction in `research-protocol-v1` §5 is computed against.

## Baselines

Every hypothesis is compared against all four, in fixed order:

```
random   ·   hold_matched_random   ·   momentum   ·   mean_reversion
```

24 baseline comparisons. Per protocol §6, no hypothesis may claim an edge until
all four have run on the same sample, same costs, same execution model.

## Fixed parameters (v1)

| Parameter | Value |
|---|---|
| FVG minimum size | 0.2 ATR |
| Displacement threshold | 2.0 ATR |
| Equal-high/low tolerance | 0.1 ATR |
| Equal-high/low min separation | 3 bars |
| ATR period | 14 |
| Swing left / right | 2 / 2 |

A test asserts these match `FEATURE_REGISTRY` exactly, so the family record
cannot drift from the implementation.

## Cost model

`realistic` — 5.0 bps round trip, fixed at declaration. One model for all six.

## Sampling and overlap

`SamplingPolicy(deduplicate_overlaps=True, label_horizon=4h)`.

The longest label window is the 4-hour R-multiple horizon, so events closer
together than that share outcome bars and **are not independent observations**.
`effective_spacing` is therefore at least the label horizon, asserted by test.

## Data gate

The family exists without real data — that is the point of pre-registration. It
may not **execute** against market data until the dataset reaches
`MARKET_CLAIM_ALLOWED`.

```python
family.require_market_claim_allowed(dataset)   # PermissionError otherwise
```

Tested three ways: refused with no grade assessment, refused on a
`RESEARCH_GRADE` **synthetic** dataset (research grade is not enough — the
origin must be a real market), permitted once a real-market dataset clears the
full ladder.

## What this phase did not do

No market research. No parameter search. No feature definition changed. No
deferred concept implemented. No signal. No live execution.

All five features remain `UNTESTED`, and locking a hypothesis about them does
not change that.
