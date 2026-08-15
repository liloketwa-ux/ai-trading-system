# My Funded Futures — Rapid — ruleset `2026.08`

Sources: *My Funded Futures — Rapid Plans* —
`https://myfundedfutures.com/plans/rapid`;
*Consistency Rule at My Funded Futures* —
`https://help.myfundedfutures.com/en/articles/11994562-consistency-rule-at-my-funded-futures`

Verified 2026-08-15 by operator review, outside this coding environment.
Status `OFFICIAL_SOURCE_VERIFIED`, method `official_source_review`.

**Comparison profile.** Not an automated-execution target; the automation policy
was not verified.

## Evaluation stage

Address: `mffu / rapid / evaluation / <size> @ v2026.08`

| Size | `profit_target` | EOD max loss |
|---|---|---|
| 25K | 1,500 | 1,000 |
| 50K | 3,000 | 2,000 |
| 100K | 6,000 | 3,000 |
| 150K | 9,000 | 4,500 |

At every Rapid evaluation size:

- `daily_loss_limit` — **`NOT_APPLICABLE`**, not `UNKNOWN`. Rapid evaluations
  have none; that is a rule, not a gap, and it does not block readiness.
- `drawdown_type = "eod"`, `timing = END_OF_DAY`
- `min_trading_days = 2`
- consistency `best_day / total ≤ 0.50`

### Consistency does not fail the account

Exceeding the 50% threshold does **not** fail a Rapid evaluation — further
trading can restore compliance. Modelled as `CONSISTENCY_NOT_MET`, never
`RULE_VIOLATION`, exactly as for Topstep.

### ⚠️ The enforcement clock is not verified

`drawdown_type = eod` is verified. Whether the level is **also enforced
intraday** was not covered by the review, so `mll_mode` is `UNKNOWN` and
`build_limit_monitor()` refuses.

That gap is not pedantry. It is the difference between an open loser that ends
the evaluation the moment it touches the level and one that only matters at the
close — the same distinction that defines Topstep's MLL. Recording EOD alone and
running the tracker would be a guess dressed as a rule.

Also `UNKNOWN`: `mll_basis` (balance vs equity), `mll_locks_at`, contract limits
(`max_minis`, `max_micros`), automation policy, session boundary.

## Funded stage

Address: `mffu / rapid / funded_sim / <size> @ v2026.08`

Registered at all four sizes with **every risk rule `UNKNOWN`.** The funded
stage was not covered by the review, and its rules are not derived from the
evaluation's.

Registering the stage empty rather than omitting it is deliberate: a lookup for
the funded stage now returns a profile that refuses, instead of returning
nothing and inviting the caller to fall back on the evaluation's EOD numbers.
`mll_drawdown_type` carries the note *"must not be assumed to match the
evaluation's EOD rule"*, asserted by test.

## Readiness

Both stages `PARTIALLY_VERIFIED`. No capability is `ADJUDICATION_READY`.
