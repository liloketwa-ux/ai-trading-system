# Phase 9 — Real Data and Provenance

**Status: the pipeline is built and proven to refuse. No real market data
entered it.**

Phase 9's first success criterion is "at least one real historical futures
dataset is research-approved." That criterion is **not met**, and this document
explains why, what was built instead, and exactly what has to happen for it to
be met later.

---

## 1. Data source used

**None.** Every market-data host is refused at this environment's network
boundary.

Probed on 2026-08-16, from inside the session container:

| Host | Result |
|---|---|
| `query1.finance.yahoo.com` | `CONNECT tunnel failed, response 403` |
| `stooq.com` | `CONNECT tunnel failed, response 403` |
| `www.cmegroup.com` | `CONNECT tunnel failed, response 403` |
| `databento.com` | `CONNECT tunnel failed, response 403` |
| `api.tiingo.com` | `CONNECT tunnel failed, response 403` |
| `raw.githubusercontent.com` | `200` (control — the proxy is up and working) |

The egress allowlist permits GitHub and package registries. Nothing else.

### Why a GitHub-hosted CSV was not used

GitHub is reachable, and there are repositories containing futures price files.
None was ingested, deliberately:

- **No provider.** A file uploaded by an anonymous account is not a data
  source. Section 5 of this phase requires recording the provider, contract
  adjustments and known limitations. "Someone's repository" answers none of
  those, and the answers cannot be checked.
- **Almost certainly a continuous series.** Nearly every published NQ file is a
  stitched front-month series with an undocumented roll date and an
  undocumented adjustment method. Section 6 forbids exactly that, and the
  choices would be baked invisibly into every bar.
- **Unverifiable.** There is no reference to check it against, so a fabricated,
  resampled or silently adjusted file would pass every check this pipeline can
  perform and poison everything downstream.

A search of GitHub for futures datasets returned only *downloader libraries* —
`yfinance` wrappers, exchange clients, ProjectX SDKs — all of which require the
network that is blocked. Installing them changes nothing.

Ingesting an unverifiable file would have satisfied the letter of criterion 1
while destroying the lineage guarantees this phase exists to build. The gate
held instead.

---

## 2. Exact historical coverage

**Real market data: zero rows, zero contracts, zero days.**

The pipeline was exercised on a synthetic generator so the machinery could be
validated. Its coverage, recorded exactly as a real dataset's would be:

| Field | Value |
|---|---|
| Instrument | `NQ` |
| Contract | `NQM26` (a label on generated data, not an observation) |
| Timeframes | `1m`, `5m`, `15m`, `1h` |
| Rows | 3,000 per timeframe, 12,000 total |
| Range (`5m`) | 2026-03-02T00:00:00Z → 2026-03-13T19:55:00Z |
| Origin | `DataOrigin.SYNTHETIC` |
| Timezone | UTC throughout |

The target — NQ, 1m/5m/15m/1h, 2021→present — remains entirely unfulfilled.
No history was fabricated to fill it.

---

## 3. Data-quality findings

The gate ran on all four timeframes. Results, against the declared CME equity
index session structure (Sun–Fri, 22:00–23:00 UTC maintenance break):

| Timeframe | Rows | Duplicates | Invalid | Missing | Completeness | Status |
|---|---|---|---|---|---|---|
| `1m` | 3,000 | 0 | 0 | 0 | 100.0000% | `research_eligible` |
| `5m` | 3,000 | 0 | 0 | 0 | 100.0000% | `research_eligible` |
| `15m` | 3,000 | 0 | 0 | 0 | 100.0000% | `research_eligible` |
| `1h` | 3,000 | 0 | 0 | 0 | 100.0000% | `research_eligible` |

A clean sheet here is unremarkable — the generator emits exactly what the
session spec expects. The gate's value is in what it *rejects*, which is
covered by test rather than by this run:

| Check | Severity | What it catches |
|---|---|---|
| `chronological_order` | fatal | windowed features silently mixing past and future |
| `duplicate_timestamps` | fatal | double-counted volume and returns |
| `impossible_ohlc` | fatal | stops resolved inside bars that cannot exist |
| `negative_volume` | fatal | not a quantity that exists |
| `non_positive_price` | fatal | a parse error wearing a price |
| `implausible_timestamp` | fatal | epoch units misread (s vs ms) |
| `availability_precedes_event` | fatal | a free look at every bar's future |
| `bar_alignment` | warning | data resampled from another timeframe |
| `missing_bars` | warning | gaps, counted against the session structure |
| `bars_outside_session` | warning | wrong session spec, or wrong timezone |

Two deliberate choices:

- **A gap is not automatically a defect.** Futures do not trade continuously.
  Missing bars are counted against a declared `SessionSpec`; a dataset with no
  session spec reports `missing_rows = None`, not zero. Claiming 100%
  completeness that was never measured is worse than admitting ignorance.
- **Warnings do not block.** Three duplicate rows in two million is worth
  knowing and not worth refusing. Only fatal findings close the gate.

---

## 4. Availability semantics

This is where backtests lie by default, so it gets its own type.

`AvailabilityQuality` has four levels:

| Level | Meaning | Latency research |
|---|---|---|
| `OBSERVED` | the source recorded arrival; we kept it | ✅ permitted |
| `DERIVED` | computed from a documented publication delay | ❌ |
| `ASSUMED_BAR_CLOSE` | available at the close of its own bar | ❌ |
| `UNVERIFIED` | nothing about arrival is known | ❌ |

**The synthetic dataset is `ASSUMED_BAR_CLOSE`,** and would have been for any
CSV-based real source too — bar files record when the *market* did something,
almost never when the *file* knew about it.

Consequences, enforced in code rather than documented and forgotten:

- `AvailabilityPolicy` refuses to be constructed without a written
  justification for anything but `UNVERIFIED`. An unexplained assumption about
  arrival silently sets how much future a strategy can see.
- A `DERIVED` policy with zero delay is rejected — that is
  `ASSUMED_BAR_CLOSE` wearing a better label.
- `FeatureEligibility.latency_sensitive_features` is **false** on this dataset.
  Any latency result computed on assumed availability measures the assumption,
  not the market.
- An observed arrival timestamp always overrides the policy. A real
  measurement beats a rule about measurements.

`ASSUMED_BAR_CLOSE` is nearly harmless on hourly bars and nearly worthless on
one-second bars. Only the label tells a reader which case they are in.

---

## 5. Dataset ID

```
nq-nqm26-synthetic-2688a3a1f191c6b8
```

Content-derived, not assigned. The id hashes source, origin, instrument,
contract, timeframes, row checksum, code commit and schema version, so two
datasets built from the same inputs collide by design and two built from
different inputs cannot be confused. A changed id is the alarm.

| Field | Value |
|---|---|
| `checksum` | `8da93be3118189b106caf7a3c8c1fb1e…` |
| `code_commit` | `87412238e01f1d36820ae7e246911244d9b1d320` |
| `schema_version` | `1.0.0` |
| `row_count` | 3,000 |
| `origin` | `synthetic` |
| `may_support_market_claims` | **false** |

The checksum includes `available_at` deliberately: re-deriving availability
under a different policy is different research and must produce a different
dataset.

`ResearchDataset.require_real_market()` raises on this dataset. Synthetic data
cannot back a statement about a market, whatever it is named.

---

## 6. Baseline results

Run on **synthetic data**, costs = `realistic` (5.0 bps round trip), forward
return over 12 bars, seeded bootstrap CIs:

| Baseline | n | Gross (bps) | Net (bps) | 95% CI | Excludes 0 |
|---|---|---|---|---|---|
| `random` | 889 | +0.108 | **−4.892** | [−5.844, −3.964] | yes |
| `hold_matched_random` | 889 | −0.389 | **−5.389** | [−6.304, −4.476] | yes |
| `momentum` | 1,484 | −0.086 | **−5.086** | [−5.812, −4.359] | yes |
| `mean_reversion` | 1,484 | +0.395 | **−4.605** | [−5.301, −3.895] | yes |

### What this does and does not show

**It shows the measurement path computes correctly.** Every gross mean is
within 0.4 bps of zero and every net mean sits within 0.4 bps of −5.0 — the
cost drag. On a driftless random walk with no autocorrelation, that is the
analytically required answer, and getting it is evidence that costs, forward
returns and the bootstrap are wired up right.

**It is not evidence about edge.** A driftless walk has no autocorrelation by
construction, so momentum and mean reversion *must* return approximately minus
the cost drag on it. That result describes the generator. It says nothing
whatsoever about whether these strategies have an edge in a real market, and it
is not cited here as if it did.

The intervals excluding zero is likewise not a finding — it reflects a
deterministic 5 bps cost applied to a zero-mean series, which is exactly what a
correctly implemented CI should detect.

---

## 7. ICT research status

**Not evaluated. The gate is closed.**

```
CLOSED: dataset nq-nqm26-synthetic-2688a3a1f191c6b8 has origin synthetic.
ICT hypotheses are evaluated on real market data or not at all.
```

Phase 5 pre-registered the ICT hypotheses and stopped short of turning them
into rules. The commitment was that they would be evaluated on real data, once,
with their definitions unchanged. `ICTGate` is that commitment as code:

- It checks `DataOrigin`, not the dataset's name. A synthetic dataset cannot
  open it however it is labelled.
- It requires the dataset to have passed the quality gate.
- `verify_definitions_unchanged()` refuses an evaluation whose hypothesis set
  has drifted from the pre-registration. A different set is not forbidden — it
  is simply a new, un-pre-registered study that needs its own multiple-testing
  budget.

Evaluating the hypotheses on synthetic data would have spent a one-shot
pre-registration on a result about a random number generator. The gate held.

---

## 8. Source status

Six ordered levels; promotion is one rung at a time and requires evidence.

| Source | Status | Reason |
|---|---|---|
| `databento` | blocked | egress 403 |
| `yahoo_finance` | blocked | egress 403 |
| `stooq` | blocked | egress 403 |
| `cme_datamine` | blocked | egress 403 |
| `tiingo` | blocked | egress 403 |
| `synthetic_gbm` | `research_approved` | passed the quality gate |

`synthetic_gbm` reaching `research_approved` is correct and worth explaining,
because it looks alarming. The ledger tracks whether an **adapter** produces
data that passes the gate — this one does. Whether that data may back a
**market claim** is a separate, orthogonal control living on `DataOrigin`, and
it is `false` here. Two independent gates, and only both together admit a real
finding.

`SourceLedger.promote()` refuses `RESEARCH_APPROVED` without a passing quality
report, and refuses rung-skipping outright: going from `SOURCE_PRESENT` to
`RESEARCH_APPROVED` because the data "looks fine" is the move the ladder exists
to make visible.

---

## 9. Rule snapshots (time-aware prop-firm rules)

Phase 8 modelled a rule as a value plus provenance. That is insufficient for
replay, because a rule is also a fact about a *period*.

`RuleSnapshot` carries a half-open validity interval, and `get_ruleset()`
**requires** an `as_of` date — there is no overload that omits it, because the
only plausible default is "today", which is precisely the bug:

```python
store.get_ruleset("topstep", "trading_combine", 50_000, date(2026, 3, 15))
# -> mll_threshold = 2000  (ruleset 2026.01)

store.get_ruleset("topstep", "trading_combine", 50_000, date(2026, 9, 15))
# -> mll_threshold = 2500  (ruleset 2026.07)
```

A date before any snapshot raises `NoRulesetError` rather than falling back to
current rules. Overlapping intervals are rejected at write time, not resolved
at read time — a store that silently picks one of two conflicting snapshots
answers every query and is wrong on an unknown subset.

Five verification levels, uncollapsed:

| Level | Backs compliance | Re-verifiable without a human |
|---|---|---|
| `RUNTIME_VERIFIED` | ✅ | ✅ |
| `MACHINE_VERIFIED` | ✅ | ✅ |
| `SOURCE_VERIFIED` | ✅ | ❌ |
| `USER_SUPPLIED` | ❌ | — |
| `UNKNOWN` | ❌ | — |

The middle row is why they are not collapsed: a human-read value backs a
decision and still rots silently, because nothing re-reads the page.

---

## 10. Pumpi latency

**`UNVERIFIED`.** The Solana indexers are unreachable, so nothing was measured.

`LatencyInstrument` records four stamps per event — `event_time`,
`observed_at`, `persisted_at`, `processed_at` — and reports P50/P95/P99/max per
stage (indexing, persistence, processing, end-to-end).

Two refusals:

- `LatencyProfile.require_measured()` raises while the status is `UNVERIFIED`.
  Research must not assume zero indexing latency.
- Below 100 samples the profile reports `INSUFFICIENT_SAMPLES` and returns
  `None` percentiles. A P99 computed from eleven observations describes the
  sample, not the pipeline.

The tail is the number that matters: P50 describes the pipeline on a quiet
afternoon, P99 describes it when a mint goes viral — which is exactly when a
strategy would want to act.

---

## 11. Point-in-time replay

`PointInTimeReplay` holds bars sorted by `available_at`, not `event_time` — the
ordering a live system experiences. Sorting by event time is how a
late-arriving correction gets replayed as though it had been on time.

Verified in this run:

| Check | Result |
|---|---|
| Decision at 2026-03-08T10:00:00Z | 1,501 of 3,000 rows visible |
| Max visible `event_time` | 2026-03-08T10:00:00Z (never ahead of the decision) |
| Leakage check | clean |
| Injected future observation (+30 days, close 99,999) | **not visible** |

The class deliberately exposes **no attribute that returns all rows**. Every
accessor takes a decision time. Look-ahead rarely arrives as a bug in the
comparison; it arrives as a code path that skipped the filter, so there is no
path to skip. The cursor is also monotonic — a replay that can rewind can
re-decide with knowledge it did not have, and the resulting curve looks like
skill.

---

## 12. Contract handling

No continuous series exists and none can be built.

`ContractBook` stores bars under `(contract, timeframe)` and has no
`as_continuous()`, no `stitched()` — asserted absent by test.
`continuous_series()` exists solely to refuse, delegating to the Phase 7
`RollPolicy.assert_continuous_claim()` so that rule lives in exactly one place.
Even given a policy that *would* permit continuity, it still refuses: no
adjustment implementation exists, and returning concatenated raw bars from a
method with that name would be worse than refusing.

Per-contract metadata: `contract`, `expiry`, `first_seen`, `last_seen`,
`roll_indicator`, `roll_indicator_date`. `first_seen`/`last_seen` describe
*this dataset*, not the contract's real trading life — conflating them would
overstate coverage. `roll_indicator` records observed evidence (a volume
crossover on a date), never a decision, so a roll policy can change without
re-deriving the evidence.

---

## 13. Tests added

136 new tests, 906 → **1,042 passing**.

| File | Tests | Covers |
|---|---|---|
| `tests/test_phase9_rule_snapshots.py` | 35 | five levels, interval semantics, as-of lookup, two historical versions, no-fallback |
| `tests/test_phase9_history.py` | 101 | availability, provider contract, contract separation, quality gate, source ladder, dataset lineage, replay + injected future, latency, campaign + ICT gate |

---

## 14. Phase 9 success criteria

| # | Criterion | Status |
|---|---|---|
| 1 | A real historical futures dataset is research-approved | ❌ **not met** — no source reachable |
| 2 | Its quality report is complete | ⚠️ machinery complete; nothing real to report on |
| 3 | Point-in-time replay works | ✅ verified, incl. injected future observations |
| 4 | Baselines run successfully | ✅ run — on synthetic data, labelled as such |
| 5 | The research pipeline runs against real data | ❌ **not met** — ran against synthetic |
| 6 | No artificial edge is claimed | ✅ none claimed; gates enforce it |
| 7 | Dataset lineage is reproducible | ✅ content-hashed id, checksum, code commit |
| 8 | Rule snapshots are time-aware | ✅ as-of lookup, two-version tests |

**Four of eight met, two partially, two blocked on the same root cause.**

---

## 15. Remaining blockers

1. **Network egress for market data.** The single blocker for criteria 1, 2
   and 5. Requires adding at least one provider host to the environment's
   allowlist. `databento.com` is the best fit — it serves per-contract CME
   futures with documented adjustments, which is what section 6 requires.
   Yahoo and Stooq would be worse even if unblocked: both serve a stitched
   front-month series.
2. **Credentials for a paid provider.** Not to be pasted into this session.
   A provider key belongs in the environment's secret configuration, read at
   runtime from the environment, never committed and never logged.
3. **Solana indexer access**, for Pumpi latency. Until then the profile stays
   `UNVERIFIED` and research cannot assume zero indexing latency.
4. **An adjustment implementation**, before any continuous-contract claim.
   Currently refused by construction, which is the correct state until a roll
   method and adjustment method are both implemented and tested.

## 16. What was not built, per instruction

No live trading. No funded-account credentials. No risk parameters tuned to
pass. No autonomous decisions. No strategy selected on in-sample results. The
locked holdout was not touched — the existing research gate still guards it,
and nothing in this phase had standing to spend it.
