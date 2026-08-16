# Research Calibration

Every phase up to now asked whether the research machinery *runs*. This one
asks whether it is *right*: five datasets whose generating process is known in
advance, and a scorer that checks the system recovers what was put in and
refuses what was not.

The distinction matters because a pipeline that runs cleanly on real data and
silently fails to detect a real relationship is indistinguishable, from the
outside, from a pipeline reporting that no edge exists. Calibration is how you
tell those apart before spending real data on the question.

**Everything here is synthetic and cannot support a market claim.** That is
enforced by `DataOrigin`, not by convention.

---

## Part A — Known-edge synthetic validation

### How the datasets are built

Each generator returns bars plus a `SealedTruth`. The bars carry no reference
back to the parameters that made them, and the truth object refuses to hand
over its contents until `reveal(purpose)` is called — which is logged.
`CalibrationRun.assert_blind()` fails the run if the log was non-empty when the
detector executed.

The seal does not *prevent* cheating; nothing can. It makes a breach visible,
and the real defence is that the detector receives a plain sequence of bars.

### One cost model, every dataset

All five run against `REALISTIC` — 5.0 bps round trip. This matters: sizing the
edges so that the intended verdicts fall out of a *single* cost model is what
stops the calibration being rigged by per-dataset cost tuning. A test asserts
the cost figure is identical across the tradeable and refused cases.

### Results

| Dataset | n | Gross (bps) | Net (bps) | p | Statistical | Economic | Verdict |
|---|---|---|---|---|---|---|---|
| `null` | 5,998 | +0.237 | −4.763 | 0.612 | `no_evidence` | `not_assessed` | ✅ |
| `momentum` | 5,998 | +7.795 | +2.795 | 0.001 | `positive_effect` | `economically_attractive` | ✅ |
| `mean_reversion` | 1,741 | +9.577 | +4.577 | 0.001 | `positive_effect` | `economically_attractive` | ✅ |
| `regime_dependent` | 7,998 | +2.698 | −2.302 | 0.001 | `positive_effect` | `economically_unattractive` | ✅ |
| `sub_cost` | 7,998 | +1.766 | −3.234 | 0.001 | `positive_effect` | `economically_unattractive` | ✅ |

**All five objectives pass.**

#### 1. Reject zero-drift data ✅

`null` is iid normal with zero drift. The detector returns `no_evidence` at
p = 0.61. Checked across five independent seeds; at most one marginal positive
is tolerated, which is what a 5% test should produce.

#### 2. Detect known positive edge ✅

`momentum` is AR(1) with φ = 0.25. The analytically expected sign-conditioned
expectancy is φ·σ·√(2/π); the detector recovers a value within 30% of it, and
a test asserts that — recovery of the *magnitude*, not merely detection of a
sign.

Out of sample, on a chronological 50/50 split:

| Dataset | OOS n | Gross (bps) | p | Verdict |
|---|---|---|---|---|
| `momentum` | 2,998 | +7.554 | 0.001 | `positive_effect` |
| `mean_reversion` | 860 | +10.379 | 0.001 | `positive_effect` |

The split is chronological, never random. A shuffled split on autocorrelated
returns puts neighbouring bars on both sides and leaks the exact structure the
test is measuring.

`mean_reversion` expresses its edge as a *reversal probability* rather than a
coefficient, deliberately: a detector tuned only to return autocorrelation
would under-read it. The detector also picks its own lookback and threshold,
which differ from the generator's — handing it the true parameters would prove
nothing.

#### 3. Detect known negative edge ✅

With φ = −0.25:

```
gross = −7.830 bps    p = 0.0010    negative_effect / negative_net
```

Correctly signed, and never routed to `economically_attractive`.

#### 4. Detect regime-dependent edge ✅

Regime A carries φ = 0.22; regime B carries φ = −0.05, in 500-bar blocks.

| Regime | n | Gross (bps) | 95% CI | Verdict |
|---|---|---|---|---|
| A | 3,999 | +7.276 | [6.045, 8.526] | `positive_effect` |
| B | 3,999 | −1.881 | [−3.104, −0.645] | `negative_effect` |

Spread 9.157 bps, **intervals disjoint** — the regimes differ by more than the
noise in either.

The pooled result is +2.698 bps: the two regimes partly cancel, so aggregating
understates the effect by roughly two-thirds. A test asserts
`regime_A > pooled`, pinning the reason the breakdown exists.

#### 5. Costs destroy the edge ✅

`sub_cost` is AR(1) with φ = 0.05 — a real relationship, detectable at
p = 0.001, worth +1.766 bps gross against a 5.0 bps round trip.

```
statistical: positive_effect
economic:    ECONOMICALLY_UNATTRACTIVE
```

This is the dataset that separates "significant" from "worth trading". A system
reporting only the p-value would size into it. The two verdicts are computed and
reported separately, never one instead of the other.

Note that `regime_dependent` lands in the same economic state when pooled —
also correct, and a second illustration of the same point.

---

## Part A — Synthetic null validation (false-discovery stress)

A pre-declared family of hypotheses run against pure noise. Every hypothesis is
false by construction, so every discovery is a false one.

| Trials | Raw discoveries | Raw rate | BH | Bonferroni | Best Sharpe | DSR |
|---|---|---|---|---|---|---|
| 50 | 1 | 2.00% | 0 | 0 | 0.0614 | 0.0000 |
| 200 | 8 | 4.00% | 0 | 0 | 0.0939 | 0.0000 |
| 400 | 11 | 2.75% | 0 | 0 | 0.0939 | 0.0000 |
| 800 | 31 | 3.88% | 0 | 0 | 0.1067 | 0.0000 |

- **Trial counting works** — the declared family size is what gets corrected against.
- **BH correction works** — zero survivors at every family size.
- **DSR reacts to trials** — 0.0000 throughout; the deflated Sharpe declines to
  endorse the best of many nulls, and a test asserts it never *improves* as
  trials increase.
- **Calibration holds** — raw rates cluster around the 5% α. The tolerance band
  is ±4 percentage points, wide on purpose: with a few hundred trials the
  binomial spread is substantial, and a tight assertion would fail on seed
  choice rather than on a real defect.

---

## Part B — Research-grade data gates

One quality flag was conflating questions that fail independently. Five gates
now, climbed in order:

| Grade | Means |
|---|---|
| `SOURCE_VALID` | the source is identified and named |
| `DATA_QUALITY_VALID` | the rows survive the quality gate |
| `POINT_IN_TIME_VALID` | availability semantics are coherent and replayable |
| `RESEARCH_GRADE` | all of the above; research may run |
| `MARKET_CLAIM_ALLOWED` | the data is real, so conclusions may describe a market |

**The last rung is not implied by the others.** `RESEARCH_GRADE` synthetic data
is legitimate and useful — it is what this whole document rests on. It cannot
tell you anything about NQ, and the ladder says so structurally rather than
relying on a reader remembering.

Assessment stops at the first failing rung, and each rung records *why*.
"Not research grade" is not actionable; "point_in_time_valid: 3 rows available
before their event time" is.

### Provenance timestamps

Four, kept distinct:

| Field | Meaning |
|---|---|
| `event_time` | when the thing happened |
| `source_available_at` | when the **provider** says it became available |
| `system_observed_at` | when this system first saw it |
| `ingested_at` | when it was durably written |

`source_available_at` is `None` whenever the provider does not expose one —
which is every bar file seen so far. **It is never filled in from
`available_at`.** Doing so would silently convert this project's
bar-completion policy into a claim about the vendor's delivery time, and those
are different assertions.

`available_at` remains the effective timestamp used by point-in-time replay,
derived from the documented bar-completion policy. Two properties make the
distinction machine-readable: `source_availability_known` and
`availability_is_policy_derived`.

---

## Part C — Real futures provider interface

`FuturesDataProvider` is written **before** any provider exists, deliberately.
Writing the interface against the first vendor's API shape is how a vendor's
quirks become the system's assumptions — and the quirk that matters most is
that most vendors will happily serve a stitched front-month series and call it
`NQ`.

Required capabilities: historical bars (per contract), trades where available,
contract metadata with expiries, session metadata, instrument metadata (tick
size, tick value, multiplier, currency, exchange).

Required response provenance, all seven mandatory: `provider`, `dataset`,
`contract`, `timestamp`, `timezone`, `schema_version`, `coverage`.

Two refusals are built in:

- `fetch_bars` takes a **`contract`**, not a symbol, and there is no front-month
  parameter. A provider that can only serve a stitched series must declare
  `serves_continuous_only`, and `require_contract_level()` then refuses it for
  canonical ingestion.
- `check_credentials` verifies only *presence*, by environment-variable name,
  and reports only names. A missing-key error that echoes the key's value is a
  credential leak in a log file.

No implementation ships.

---

## Part D — Contract-first policy

Unchanged from Phase 9 and now enforced at two more points. The canonical
dataset holds individual contracts. `ContractBook` has no `as_continuous()` and
no `stitched()` — asserted absent by test. A continuous series may only be
derived later from raw contracts **plus** an explicit roll policy **plus** an
explicit adjustment policy, and even given both, construction still refuses
because no adjustment implementation exists.

---

## Part F — Dataset checklist

Fourteen items, fixed, all of which must pass before research approval:

`source_identity` · `contract_identity` · `coverage` · `session_calendar` ·
`timezone` · `duplicate_rows` · `missing_intervals` · `invalid_ohlc` ·
`timestamp_anomalies` · `contract_expiry` · `roll_metadata` ·
`adjustment_policy` · `availability_semantics` · `provenance`

Each is tri-state, and `UNKNOWN` is not `FAIL`. An unverified roll policy is a
gap to close; a wrong one is a defect to fix. Both block approval; only one
means something is broken. The item list cannot be shortened — recording an
unlisted item raises.

---

## Part E / G — Real data status

**No real dataset exists.** Nothing has changed since Phase 9: every
market-data host is refused at the network boundary. No GitHub CSV was
imported, and none will be.

The ICT campaign remains gated. `ICTGate` checks `DataOrigin`, so no amount of
synthetic calibration opens it, and `GradeResult.require(MARKET_CLAIM_ALLOWED)`
now provides a second, independent refusal on the same path.

See [`real-data-validation.md`](real-data-validation.md) §15 for the exact
requirements for the first NQ dataset.

---

## What this calibration does and does not establish

**Establishes:** the detection machinery recovers known effects of three
different shapes, in and out of sample, with roughly correct magnitude; it
separates regimes whose confidence intervals are disjoint; it declines to find
structure in noise; its multiple-testing corrections behave; and it refuses a
real edge on economic grounds when costs eliminate it.

**Does not establish:** anything about any real market. The generators are
simple AR(1) and threshold-reversal processes with stationary parameters, which
real markets are not. A system that passes calibration can still fail on real
data for reasons this exercise cannot reach — non-stationarity, structural
breaks, execution effects, regime changes that are not labelled.

Calibration rules out one failure mode: *the machinery is blind*. It rules out
nothing else.
