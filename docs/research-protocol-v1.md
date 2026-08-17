# Research Protocol v1 — FROZEN

**Status: FROZEN as of 2026-08-17. Do not edit this document.**

Any change to any rule below creates `research-protocol-v2.md`. Amending v1 in
place would silently invalidate every result recorded against it, and the point
of freezing a protocol is that a result can be read years later and its rules
recovered exactly.

Results cite the protocol version they ran under. A result with no protocol
version is not a result.

---

## Why freeze

A research protocol that can be edited after seeing results is not a protocol,
it is a record of what happened to work. The specific failure is small and
almost invisible: a threshold moves from 0.05 to 0.10, a baseline is dropped
because it "wasn't informative", an embargo shortens because it "cost too much
data". Each is defensible alone. Together they are how a null result becomes a
discovery.

Freezing does not make the rules right. It makes them *fixed*, so that a later
disagreement is about the rules rather than about what the rules were.

---

## 1. Dataset-version rules

- Every dataset carries a content-derived `dataset_id` hashing source, origin,
  instrument, contract, timeframes, row checksum, code commit and schema
  version.
- The checksum includes `available_at`. Re-deriving availability under a
  different policy is different research and **must** produce a different
  dataset.
- Datasets are immutable. A correction is a new dataset with a new id, never an
  edit.
- A result cites the `dataset_id` it ran on. A result citing "the NQ 5-minute
  data" is not reproducible and is not accepted.
- One dataset covers **one contract**. Joining contracts is a roll, and a roll
  requires a policy (§9).

## 2. Holdout rules

- The holdout is locked and access is recorded in an append-only ledger.
- It is **spent once per research version**. A second look under the same
  research version is refused, not discouraged.
- Spending the holdout requires the candidate to have already passed
  walk-forward validation and the robustness matrix. The holdout confirms; it
  does not screen.
- A failed holdout ends that research version. It does not authorise a tweak
  and a re-test — that is a new research version with a fresh holdout budget.
- The holdout is never used for parameter selection, threshold setting, or cost
  calibration.

## 3. Hypothesis registration

- Hypotheses are registered **before** the data that will test them is
  examined, with: id, description, features, label, horizon, direction (where
  directional), research version, dataset version.
- A registered hypothesis is immutable. Editing its definition produces a new
  hypothesis with its own id, and the original remains in the registry with its
  outcome.
- **A poor result is never grounds for amending a definition.** A changed
  definition is a different, un-pre-registered study requiring its own
  multiple-testing budget. `ICTGate.verify_definitions_unchanged()` enforces
  this.
- Hypotheses that were registered and never run are reported. Silent
  abandonment inflates the apparent hit rate of the family.

## 4. Trial counting

- The declared family size is what corrections are computed against, and it is
  fixed at declaration time via the campaign's content hash.
- `CampaignDeclaration.campaign_id` hashes name, purpose, dataset, instrument,
  contract, timeframes, features, labels, hypotheses, baselines, cost model,
  execution model, validation protocol and seed. **Changing any of them
  produces a different campaign**, which makes a quietly widened search visible
  in the record instead of invisible in the results.
- `test_count = len(hypotheses) × len(timeframes)`. Testing one hypothesis on
  four timeframes is four trials, not one.
- Trials abandoned mid-run still count. A test that was started and not
  finished was still a look at the data.

## 5. Multiple-testing controls

Three, applied together, because they answer different questions:

| Control | Answers |
|---|---|
| Benjamini-Hochberg (α = 0.05) | what fraction of the discoveries are false |
| Bonferroni (α = 0.05) | is any single discovery family-wise safe |
| Deflated Sharpe Ratio | did selection bias alone produce this Sharpe |

- BH is the primary control. Bonferroni is reported alongside as the
  conservative bound.
- DSR takes the **declared** trial count, not the number of trials that looked
  promising.
- Calibration verified: on pure null data across 50–800 trials, raw discovery
  rates cluster near α, BH leaves zero survivors, and DSR does not improve as
  trials increase. See [`research-calibration.md`](research-calibration.md).

## 6. Baseline requirements

Four baselines, run **before** any hypothesis is permitted a verdict:

```
random   ·   hold_matched_random   ·   momentum   ·   mean_reversion
```

- `CampaignResult.may_report_edge()` returns `False` until every declared
  baseline has run. A hypothesis that has not been compared against random
  selection has not been compared against anything.
- Baselines run on the same sample, same forward return, same cost model and
  same execution model as the hypothesis. The comparison is between
  *selections*, not between setups.
- `hold_matched_random` exists because a strategy holding longer earns more
  drift; matching holding time removes that confound.

## 7. Cost assumptions

- Three preset models: `optimistic`, `realistic`, `pessimistic`. **`realistic`
  (5.0 bps round trip) is the protocol default** and the one results are
  reported against.
- Costs are fixed at campaign declaration. Choosing a cost model after seeing
  results is choosing it to suit them.
- Spread is crossed once per side; slippage, commission and exchange fees apply
  to both sides.
- Two verdicts are always reported separately and never one instead of the
  other:
  - **statistical** — is the effect distinguishable from noise?
  - **economic** — does it survive costs?
- A real effect smaller than costs is `ECONOMICALLY_UNATTRACTIVE`, not a pass.
  Gross edge alone never qualifies.

## 8. Walk-forward rules

- Purged, embargoed, anchored walk-forward. Rolling windows are permitted only
  when declared in advance.
- Every fold reports separately. A candidate is judged on the **distribution**
  of fold results, not their average — an average hides one fold carrying
  everything.
- Parameters are fit on the training portion of each fold only. Any parameter
  touching test data invalidates the fold.
- Fold count and boundaries are fixed at declaration and cannot be adjusted
  after seeing fold results.

## 9. Purge policy

- Training observations whose label horizon overlaps the test window are
  **purged** from training. Overlapping labels leak the test period's outcome
  into the training set.
- Purge length equals the maximum label horizon in the campaign, not the
  average.
- A validation window with a zero-length purge is permitted only when the
  embargo is non-zero and the label horizon is zero.

## 10. Embargo policy

- A fixed embargo follows every test window, during which no training
  observation may be drawn.
- The embargo exists for serial correlation that outlives the label horizon;
  purging alone does not remove it.
- Embargo length is declared in bars, not wall-clock time. The dataset's
  resolution bounds what can honestly be claimed.
- Shortening an embargo to recover sample size is a protocol change, not a
  tuning decision.

## 11. Robustness matrix

Every surviving candidate is re-run under deteriorated execution:

| Axis | Multipliers |
|---|---|
| cost | 1.0, 1.25, 1.5, 2.0, 3.0 |
| slippage | 1.0, 1.5, 2.0, 3.0 |
| delay | 0, 1, 2, 3 **bars** |

- Delay is expressed in **bars, not milliseconds**. On hourly bars a 250 ms
  delay is unobservable, and reporting sensitivity to it implies a precision
  the data does not contain.
- The breakeven multiplier — where expectancy crosses zero — is reported as a
  number, never assumed to be far away.
- Trade-removal analysis is mandatory: expectancy recomputed after removing the
  best 1, 5 and 10 trades and the top 5% of wins. **A candidate whose edge
  disappears when its single best trade is removed did not have an edge.**

## 12. Minimum evidence requirements

A candidate must clear **all** of the following. These are necessary, not
sufficient.

| Requirement | Threshold |
|---|---|
| Sample size | ≥ 30 events per fold; ≥ 100 for a detection verdict |
| Statistical | BH-significant at α = 0.05 across the declared family |
| Effect | bootstrap CI (seeded) excludes zero |
| Economic | net expectancy positive under `realistic` costs |
| Out of sample | recovered on a **chronological** split, never shuffled |
| Walk-forward | positive in a majority of folds, no fold catastrophic |
| Robustness | survives cost ×1.5 and delay of 1 bar |
| Outlier dependence | survives removal of the best trade |
| Baselines | beats all four, on the same sample and costs |
| Holdout | passes once, at the end |

Failing any one is a failing candidate. There is no aggregate score that lets a
strong result on one axis compensate for a failure on another.

## 13. Market-claim requirements

A statement about a real market requires **all** of:

1. Dataset origin is `REAL_MARKET` — synthetic data can be `RESEARCH_GRADE` and
   still may never describe a market.
2. Dataset grade is `MARKET_CLAIM_ALLOWED` (the full five-rung ladder).
3. The 14-item dataset checklist is complete, with no `UNKNOWN` items.
4. Point-in-time replay passes, including against injected future observations.
5. Every minimum evidence requirement in §12 is met.
6. The result cites its `dataset_id`, `campaign_id`, protocol version and code
   commit.

Results computed on synthetic data describe the generator. They are legitimate
and useful — the calibration in `research-calibration.md` rests entirely on
them — and they are never evidence about NQ.

---

## Change control

| Version | Status | Frozen |
|---|---|---|
| v1 | **FROZEN** | 2026-08-17 |
| v2 | does not exist | — |

To change any rule: create `research-protocol-v2.md`, state what changed and
why, and record which results ran under which version. Results are not migrated
between versions.
