# ICT Hypothesis Research Protocol

Phase 5. Research only — nothing here produces a trading rule.

## The question

Do combinations of objective features associated with ICT concepts carry
information about future outcomes **beyond what simpler signals already
provide**? The incremental clause is the whole test. A setup correlating with
forward returns proves nothing if it fires mostly in trending conditions a
moving-average crossover would also catch.

The protocol is built to be able to answer *no*, and on the demonstration run
it did.

## Hypothesis feature vector

Six objective components, each a `FeatureSnapshot` carrying availability and
quality:

| Component | Definition |
|---|---|
| `liquidity_sweep` | wick beyond a confirmed prior pivot with the close back inside |
| `displacement_atr` | bar range ÷ ATR — magnitude only |
| `fvg` | three-bar imbalance: bar1 high < bar3 low, or the mirror |
| `mss` | close beyond the last confirmed opposing pivot |
| `htf_bias` | higher-timeframe trend state, from **closed** HTF bars only |
| `session` | which named session contains the decision |

Subjective ICT vocabulary is excluded. "Institutional intent" has no calculable
definition, so it cannot be tested and is not represented.

## Labels

Labels are the one place future prices are legitimate. The danger is that a
label is structurally identical to a feature — a float on a timestamp — so
nothing stops it being joined back as an input except discipline.

Discipline is replaced by type. `Label` is **not** a `FeatureSnapshot`: it has
`resolved_at`, not `available_at`, so it will not fit where a feature is
expected.

- `forward_return_{5m,15m,30m,1h,4h}`
- `hit_{1,2,3}R_before_-1R`, plus MAE, MFE, time-to-resolve

Definitions are immutable and checksummed. **Tie policy defaults to `stop`**:
when a bar spans both target and stop, bar data cannot say which came first, and
assuming the favourable order inflates every R-multiple result.

## Sampling

Overlapping samples are how significance gets manufactured. Twenty consecutive
firings each carrying a 4-hour label share nearly all their outcome window; the
effective sample is close to one, but every test treats it as twenty and the
interval shrinks fourfold for free.

`SamplingPolicy` enforces minimum spacing, defaulting to at least the label
horizon when deduplicating. The policy is recorded on every result.

## Baselines — all seven required

`random`, `hold_matched_random`, `momentum`, `mean_reversion`, `session_only`,
`volatility_only`, `structure_only`.

`hold_matched_random` is count-matched to the treatment: a random baseline that
fires a different number of times is confounded by turnover rather than signal
quality.

## Costs

Gross returns are not a result. Every outcome is reported gross **and** net
under three presets, pessimistic by default (10bp round trip).

## Multiple testing

The family is **declared before running**, so the trial denominator is fixed
before any result is seen. Benjamini–Hochberg controls false discovery across
baseline comparisons; Bonferroni is available but rejects real effects on large
families. Deflated Sharpe is available for Sharpe-based claims — a Sharpe of
1.5 survives one trial (DSR ≈ 1.0) and is destroyed by two hundred (DSR ≈ 0.0).

## Verdicts

`NO EVIDENCE` · `WEAK` · `PROMISING` · `UNSTABLE` ·
`ECONOMICALLY UNATTRACTIVE` · `OUT-OF-SAMPLE FAILURE` · `ROBUST CANDIDATE` ·
`INSUFFICIENT SAMPLE`

"Profitable strategy" is not in the vocabulary, and a test asserts it never
appears in a rendered report. Verdicts are derived deterministically, ordered so
the most disqualifying condition wins — notably, surviving gross but dying after
costs is `ECONOMICALLY UNATTRACTIVE`, not `WEAK`, because the first is a dead
end and the second might merely want more data.

`INSUFFICIENT SAMPLE` fires below 30 independent events. Effect sizes on a
handful of overlapping events are noise with a decimal point.

## Demonstration run

3,000 hourly synthetic ES bars, zero drift with volatility clustering, 486
sampled events, pessimistic costs, seed 42, family of 6.

| ID | n | net mean | 95% CI | beats | Conclusion |
|---|---|---|---|---|---|
| ICT-001 | 371 | −0.001380 | [−0.001923, −0.000841] | 0/7 | **NO EVIDENCE** |
| ICT-002 | 74 | −0.001614 | [−0.003037, −0.000276] | 0/7 | **NO EVIDENCE** |
| ICT-003 | 74 | −0.001614 | [−0.003037, −0.000276] | 0/7 | **NO EVIDENCE** |
| ICT-004 | 29 | −0.002374 | [−0.004556, −0.000234] | 0/7 | INSUFFICIENT SAMPLE |
| ICT-005 | 29 | −0.002374 | [−0.004556, −0.000234] | 0/7 | INSUFFICIENT SAMPLE |
| ICT-006 | 29 | −0.002374 | [−0.004556, −0.000234] | 0/7 | INSUFFICIENT SAMPLE |

This is the expected and correct result on data containing nothing to find. It
demonstrates the machinery works; it says nothing whatever about ICT on real
markets.

### Two design problems the run exposed

**1. The family has fewer degrees of freedom than it appears.** ICT-002 and
ICT-003 selected identical samples (74 events), as did ICT-004/005/006 (29). The
cause: `displacement_atr`, `htf_bias` and `session` are always *present*, so
adding them to a conjunction filters nothing. Only the booleans
(`liquidity_sweep`, `fvg`, `mss`) actually narrow the sample. Testing them as
conjunctions of "is not null" is not a hypothesis. Continuous components need
explicit thresholds or must enter as covariates rather than filters — and a
threshold is a parameter, so it must be declared before the run and counted as
a trial.

**2. Conjunctions starve the sample.** Four conditions cut 486 events to 29, at
which point no verdict is defensible. Any real campaign needs either far more
data or fewer simultaneous conditions.

## Not built

Per the phase boundary: no entry signals, thresholds, position sizing, strategy
optimization, AI recommendations, meme scoring, social or wallet intelligence,
and no live execution. A `PROMISING` verdict is not permission to build a rule.
