# ICT-FAMILY-V1 — PERMANENT FREEZE DECLARATION

> # THIS IS A RESEARCH PROTOCOL, NOT A TRADING STRATEGY.
>
> Nothing below produces a signal. It has never been run on market data.

| | |
|---|---|
| Label | **`ICT-FAMILY-V1`** |
| Family ID | `ict-objective-family-v1` |
| Fingerprint | **`b3ebb0af7f01b137`** |
| Protocol | `research-protocol-v1` (frozen) |
| Frozen on | **2026-08-17** |
| Status | **`REAL_DATA_PENDING`** |
| Hypotheses | 6 |
| Labels | 6 |
| **Trial count** | **36** |
| Baseline comparisons | 24 |

Machine-readable equivalent: `ai_trading.research.ict_freeze.freeze_record()`.

---

## What "frozen" means here, beyond the lock

`HypothesisFamily.lock()` protects a **live object** from mutation. It does
nothing about someone editing the source that constructs the family and
re-importing it — after which the object is locked again, around different
content, with no trace.

So the freeze is not the lock. The freeze is
[`ict_freeze.py`](../src/ai_trading/research/ict_freeze.py): a second,
independent copy of every declared value, written as **literals** rather than
computed from the family. `verify_frozen()` compares the built family against
that record, a regression test calls it, and any edit to a window, a threshold,
a label, a decision event, a parent link or a hypothesis statement fails the
test.

The duplication is the point. A test that derives its expected value from the
thing it is checking passes no matter what that thing says.

### What is pinned

| Pinned | Count |
|---|---|
| Family fingerprint | 1 literal |
| Per-hypothesis fingerprints | 6 literals |
| Decision events | 6 literals |
| Parent links | 6 literals |
| Labels | 6 literals |
| Feature versions | 5 literals |
| Baselines | 4 literals |
| Fixed parameters | 10 literals |
| Temporal windows | 4 literals, read off the **links**, not the parameter block |

The windows are recovered from the `TemporalLink` objects and collected into a
*set*, so a window widened in one hypothesis is caught even though two other
hypotheses still declare the old value. Reading the parameter block instead
would miss a link edited without the parameter, and vice versa.

### The frozen values

```
ICT-LS-001    f430a7cdd417e06c   decision: liquidity_sweep:v1   parent: —
ICT-LS-002    62c479a20cfe6d58   decision: displacement:v1      parent: ICT-LS-001
ICT-FVG-001   aa3e12b7cbeb1b2b   decision: fvg:v1               parent: ICT-LS-001
ICT-COMBO-001 6ee2b74f4600c477   decision: fvg:v1               parent: ICT-LS-002
ICT-EQ-001    73bb7dc87ba1f6eb   decision: liquidity_sweep:v1   parent: ICT-LS-001
ICT-COMBO-002 8534861941be3739   decision: fvg:v1               parent: ICT-COMBO-001
```

```
windows        sweep→displacement 3    displacement→FVG 2
               sweep→FVG 5             equality→sweep 50
parameters     fvg_min_size_atr 0.2    displacement_threshold_atr 2.0
               equal_tolerance_atr 0.1 equal_min_separation_bars 3
               atr_period 14           swing_left 2   swing_right 2
labels         forward_return_5m / 15m / 30m / 1h
               hit_1R_before_-1R   hit_2R_before_-1R
features       liquidity_sweep:v1  displacement:v1  fvg:v1
               equal_high:v1       equal_low:v1
baselines      random  hold_matched_random  momentum  mean_reversion
```

---

## Status: `REAL_DATA_PENDING`

`family_status()` **resolves from evidence, not from a stored flag.** A status
field somebody can assign is a status that eventually gets assigned
optimistically, so this recomputes from the dataset's own grade ladder on every
call.

| Status | Reached when |
|---|---|
| **`REAL_DATA_PENDING`** | now, and until a dataset reaches `MARKET_CLAIM_ALLOWED` |
| `APPROVED_FOR_REAL_DATA` | a `REAL_MARKET` dataset clears the full five-rung ladder |
| `SUPERSEDED` | a `FamilySupersession` has been declared |

There is deliberately **no** `CALIBRATED`, `SYNTHETIC_VALIDATED` or
`PARTIALLY_RUN`. A status reachable by synthetic data would let calibration
masquerade as progress toward a market claim. A test asserts those three names
are absent from the enum.

Two independent refusals guard execution, on the same path:

1. `require_market_claim_allowed()` checks the **origin** directly — a
   `SYNTHETIC` or `DERIVED` dataset is refused with "results computed on it
   describe the generator, not a market."
2. The same method checks the **grade ladder** — `MARKET_CLAIM_ALLOWED` must be
   granted, which the ladder already withholds from anything not `REAL_MARKET`.

Either alone would do. Both are there so a future change to the ladder cannot
quietly open the gate.

---

## Prohibited while frozen

Held as data in `PROHIBITED_ACTIONS`, so a caller can check membership rather
than remember a document. `require_action_permitted(action)` raises
`ProhibitedActionError` naming the reason and the next permitted action.

| Action | Why it is prohibited |
|---|---|
| `run_on_synthetic_for_evidence` | results describe the generator, never NQ |
| `use_openmobius_cases_as_evidence` | 1,282 unreviewed VLM extractions from video are terminology, not observations |
| `alter_definitions` | an edited definition is a different, un-pre-registered study |
| `tune_thresholds` | fixed before data; tuning after is selection |
| `tune_event_windows` | pre-registered values, not a search space |
| `add_features` | a seventh feature changes the family and its trial budget |
| `expand_family` | changes the count corrections are computed against |
| `reorder_family` | order is part of the declared record |
| `spend_holdout` | spent once, after walk-forward and robustness pass |
| `create_trade_signals` | a hypothesis is a question; nothing emits an order |
| `optimize_for_topstep_pass_probability` | fitting to an evaluation's pass criteria is optimising the scorer |

This is a **named-prohibition check, not an allowlist**. An action it has not
heard of passes, and the docstring says so — pretending it authorises everything
unrecognised would be worse than admitting it knows only these eleven.

## The next permitted research action

```
run ICT-FAMILY-V1 against the first approved real NQ dataset
under research-protocol-v1
```

One action, written out, so "what may I do now" has an answer rather than an
inference from a list of prohibitions.

---

## Change control: `ICT-FAMILY-V2`

There is no path in this module that edits v1. `verify_frozen()`'s failure
message says so explicitly, because the tempting response to a red freeze test
is to paste the new fingerprint over the old one:

> `ICT-FAMILY-V1` is permanently frozen. A change of any kind creates
> `ICT-FAMILY-V2` via `FamilySupersession` … Editing v1 back into shape is the
> only wrong answer here.

`FamilySupersession` checks four requirements at construction:

| Requirement | Refusal if violated |
|---|---|
| A new fingerprint | "the new family's fingerprint equals v1's, which means nothing actually changed" |
| A new research protocol version | v1's rules stay readable for results that ran under them |
| A recounted trial budget | "carrying v1's number forward would correct v2's results against v1's budget" |
| Explicit provenance | `change_summary` **and** `reason`, ≥ 40 characters each |

Plus: it must name the fingerprint it replaces (`b3ebb0af7f01b137`), and it may
not reuse the version string `v1`.

The record is immutable, timestamped, and carries the declaring commit.
Declaring one does not touch v1 — a test asserts `verify_frozen()` still passes
afterwards.

**One caveat worth stating.** Requiring a *different* trial count is stricter
than strictly necessary: a v2 that only widened a temporal window would still
have 6 hypotheses × 6 labels = 36 trials, and would be refused. That is the
declared rule and it is enforced as declared; the effect is that such a change
cannot be waved through as a minor edit. If a genuine same-budget v2 is ever
needed, the right response is to amend this rule deliberately, in writing,
rather than to loosen the check quietly.

---

## Regression coverage

`tests/test_ict_family_freeze.py` — **91 tests**. Each mutation below is
introduced into a freshly built family and asserted to fail `verify_frozen()`:

| Mutation | Caught by |
|---|---|
| hypothesis added | membership diff, names the id |
| hypothesis removed | membership diff |
| threshold retuned | `parameter displacement_threshold_atr` |
| window widened (all sites) | `window sweep_to_displacement` |
| window widened (one site only) | set-valued window comparison |
| decision event moved | `decision event` |
| baseline dropped | `baselines` |
| label added | `labels` |
| statement edited | `ICT-LS-001 fingerprint` |
| family unlocked | `not locked` |

Every difference is reported at once rather than stopping at the first, and the
family fingerprint is listed **last** so the itemised causes appear above it
instead of being summarised into one opaque hash mismatch.

Suite total: **1,520 tests passing** (1,429 preserved, 91 added).

---

## What has not happened

No hypothesis has been run. No market data has been observed. No threshold has
been tuned, no window adjusted, no feature added, no holdout spent, no signal
produced, and nothing has been optimised toward a prop-firm evaluation's pass
criteria.

The family is a set of six questions, written down before the answers, waiting
on a dataset that does not yet exist in this environment.
