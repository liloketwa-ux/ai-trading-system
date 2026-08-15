# Walk-Forward Validation

Phase 7. Research validation only — no parameter optimization, no winner
selection, no sizing, no execution.

## Window geometry

```
|-- TRAIN --|-- VALIDATION --|-- TEST --|
            |<- purge + embargo applied here
     step ->|-- TRAIN --|-- VALIDATION --|-- TEST --|
```

Windows advance by `step`. A fold is emitted only when its **full** test period
fits inside the data — a truncated final fold would be graded on less data than
every other and quietly skew the aggregate.

Every window records dataset version, date ranges, feature versions, hypothesis
version, execution model version and cost model version.

## Purge policy

A training observation at time `t` carrying a label of horizon `h` is still
resolving at `t + h`. If the test period begins before then, that label was
computed from bars inside the test period — the model was trained on the answer.

**Rule:** drop any training observation where `t + label_horizon > test_start`.

The purge is reported, not silent: `PurgeReport` gives submitted, kept,
`purged_label_overlap`, `purged_embargo`, `purged_out_of_range` and the removed
fraction, so the cost of the policy is visible.

## Embargo policy

Purging removes label overlap but not serial correlation. The last training bar
and the first test bar can sit seconds apart in a market with strong
autocorrelation, so the test set is still not independent.

**Rule:** drop any training observation where `t > test_start − label_horizon −
embargo`.

Combined, eligibility is one inequality:

```
train_time + label_horizon + embargo <= test_start
```

The embargo is configurable and recorded on every window.

### Zero-length validation

A train→test design with no validation split is legitimate, and in that case
**purge and embargo carry the entire separation burden**. The config refuses a
zero validation window when both embargo and label horizon are also zero — that
combination leaves nothing whatever between the last training bar and the first
test bar.

## Candidate immutability

A candidate entering Phase 7 is frozen: hypothesis id, feature definitions,
thresholds, label, execution model, cost model. Retuning inside the loop turns
out-of-sample testing into in-sample fitting with extra steps.

Candidates are content-addressed. Re-registering the same id with a changed
threshold raises `CandidateLockError`. `retuned()` refuses to produce a new
candidate without a new id, because the original's out-of-sample results do not
transfer to a changed definition.

## Contamination tests

| Attack | Defence |
|---|---|
| Training label resolving inside the test period | purge on label horizon |
| Observation one hour before the test boundary | embargo gap |
| Threshold changed mid-loop under the same id | `CandidateLockError` |
| Truncated final window | fold omitted entirely |
| Zero separation between train and test | config rejected |

## Reproducibility

Every run identifies dataset version, hypothesis version, feature versions,
label version, backtest version, execution model version, cost model version,
random seed, code commit and protocol version. The candidate fingerprint hashes
all of it.

## Holdout

The locked holdout from Phase 3 remains unspent. Producing a report never
touches it — asserted by test. Evaluation is permitted only once a candidate has
passed every pre-declared gate, and is consumed exactly once per research
version under the existing ledger policy.
