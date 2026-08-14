# Research Validation

Governance for making research results trustworthy. This phase builds no
strategy, no feature, and no score — it builds the machinery that decides
whether a later result means anything.

## Why this sits before feature engineering

The phase order was changed deliberately: validation and lineage now come
*between* historical storage and the feature engine.

A locked holdout established **after** exploratory work has begun is already
contaminated. So is a trial counter that starts counting once someone remembers
to. Both must exist before the first sweep, or the corrections that depend on
them (deflated Sharpe, reality check) are computed from numbers nobody actually
tracked.

## The three windows

```
|<--- development --->|<-- validation -->|<===== LOCKED HOLDOUT =====>|
   explore, train,       model selection      evaluated ONCE, at the end
   sweep parameters
```

Windows must be contiguous and disjoint; overlap is rejected at construction,
because overlapping windows leak validation data into training silently.

## Enforcement, not discipline

The holdout is unreachable by mechanism, not by good intentions — discipline is
exactly what fails at 2am when one more sweep would settle an argument.

```python
registry.window(Purpose.PARAMETER_SWEEP)      # -> development window
registry.window(Purpose.FINAL_HOLDOUT_EVAL)   # -> HoldoutViolation
```

Even asking for the holdout by purpose is refused. The only route is:

```python
registry.evaluate_holdout(research_version="v1", reason="frozen strategy, final eval")
```

which records the access in an append-only ledger (`HOLDOUT_TOUCHES.md`) with
timestamp, split, research version, code commit, and reason.

Arbitrary date ranges are guarded separately:

```python
split.assert_no_holdout(start, end, purpose)   # raises on intersection
```

### The holdout is spent once used

Re-evaluating a research version already in the ledger is refused:

```
HoldoutViolation: research version 'v1' has already been evaluated against
holdout s1. Modifying a strategy after seeing holdout results requires a NEW
research version and a NEW holdout period.
```

This is the rule that makes a holdout number mean what it appears to mean.
Tuning against a holdout you have already seen converts it into a validation
set, silently.

## Experiment registry

Every run records what a reproduction needs:

| Field | Purpose |
|---|---|
| `experiment_id` | identity |
| `strategy_version`, `feature_version`, `dataset_version` | what was run, on what |
| `parameters`, `seed` | determinism |
| `code_commit` | the code that produced it |
| `training_period`, `validation_period`, `holdout_period` | windows used |
| `execution_assumptions` | costs, slippage, delay |
| `metrics`, `status`, `notes` | outcome |
| `touched_holdout` | whether the locked period was read |

### Trial counting is automatic

```python
registry.trial_count(strategy_version="s1")   # completed runs
```

Tuning over fifty configurations makes the best one look excellent by
construction. A deflated Sharpe needs the trial count, and counting it
automatically is the only way it stays honest — self-reported trial counts are
always too low.

`find_duplicate()` detects a run whose reproduction key (strategy, feature,
dataset, parameters, seed, commit) matches an earlier one.

## What this phase deliberately does not build

Per instruction, none of the following exist yet, and none may be built until
the validation layer is complete:

live execution · social scraping · wallet intelligence · AI recommendations ·
strategy optimization · meme scoring · ICT optimization

## Runtime validation is prepared, not claimed

Network access is unavailable, so no adapter has been executed. The lifecycle
model (`AdapterState`) exists precisely so that "we have the code" is never
mistaken for "we have verified data". Fixtures for decode, reconnect, duplicate
handling, out-of-order events, and provenance preservation are the next
increment; they are not written yet and are listed as unresolved below.
