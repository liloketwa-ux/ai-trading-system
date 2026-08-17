# First Real Dataset — Onboarding Checklist

**Target: NQ. One individual CME futures contract. 1-minute bars preferred.**

Not a continuous front-month series. Not a GitHub CSV. Not fabricated. Those
three refusals are enforced in code, not left to judgement — see
[`research-protocol-v1.md`](research-protocol-v1.md) §1 and the CLI refusals
below.

**Current status: 0 of 23 items complete.** No provider is reachable, so item 1
blocks every item after it.

---

## The 23 items

Every one must pass. `UNKNOWN` blocks approval exactly as `FAIL` does — a gap
to close versus a defect to fix, both disqualifying.

### Provider and access (1–4)

| # | Item | How it is satisfied | Status |
|---|---|---|---|
| 1 | Provider identity | A named vendor with documentation, registered in `PROVIDER_REGISTRY` | ❌ blocked |
| 2 | Provider provenance | Manifest with `provider`, `dataset`, kinds, availability policy, known limitations | ❌ |
| 3 | API credential via environment secret | Env var name declared in `credential_env_vars`; presence checked, value never read into reporting | ❌ |
| 4 | Exchange identified | `InstrumentMetadata.exchange` — CME | ❌ |

Item 3 is a hard rule, not a preference. Credentials go in the environment's
secret configuration. Never in source, never in a commit, never on a command
line (shell history), never pasted into a chat. The CLI has no flag that
accepts a key — asserted by test.

### Contract identity (5–7)

| # | Item | How it is satisfied | Status |
|---|---|---|---|
| 5 | Contract identified | One deliverable, e.g. `NQM26` — never a product code alone | ❌ |
| 6 | Expiry identified | `ContractRecord.expiry`; **ingestion refuses without it** | ❌ |
| 7 | Instrument specification captured | `InstrumentMetadata` complete | ❌ |

Expiry is mandatory because without it no roll can ever be justified from the
data, and the contract cannot be placed in time relative to its neighbours.

### Instrument economics (8–9)

| # | Item | Value for NQ | Status |
|---|---|---|---|
| 8 | Tick size captured | from provider, not assumed | ❌ |
| 9 | Contract multiplier captured | from provider, not assumed | ❌ |

Taken from the provider rather than hard-coded. Without tick value and
multiplier a price series cannot be turned into money, and a backtest that
guesses them produces P&L in invented units.

### Calendar and coverage (10–12)

| # | Item | How it is satisfied | Status |
|---|---|---|---|
| 10 | Session calendar captured | `SessionMetadata` from the provider — trading weekdays, open/close, maintenance break, holidays | ❌ |
| 11 | Timezone captured | recorded explicitly; UTC internally | ❌ |
| 12 | Historical coverage captured | `CoverageWindow` declared by the provider, not inferred from what a query happened to return | ❌ |

The session calendar comes from the provider because the missing-bar count is
computed against it. An assumed session turns a normal close into reported data
loss, or hides real loss inside an assumed break.

### Time semantics (13–14)

| # | Item | How it is satisfied | Status |
|---|---|---|---|
| 13 | Timestamp semantics documented | which timestamp the bar is stamped with (open or close), and the bar-completion policy | ❌ |
| 14 | Source availability semantics documented | `source_available_at` if published; otherwise **left `None`** and `availability_quality = ASSUMED_BAR_CLOSE` | ❌ |

Item 14 is where backtests lie by default. `source_available_at` is **never**
filled in from `available_at`: doing so converts this project's bar-completion
policy into a claim about the vendor's delivery time, and those are different
assertions. The flags `source_availability_known` and
`availability_is_policy_derived` make the distinction machine-readable.

Consequence: with assumed availability,
`FeatureEligibility.latency_sensitive_features` is `False`. Any latency result
would measure the assumption, not the market.

### Data quality (15–19)

| # | Item | Check | Status |
|---|---|---|---|
| 15 | Missing intervals measured | counted against the session calendar; reports `None`, not `0`, when no calendar exists | ❌ |
| 16 | Duplicate rows checked | fatal if any | ❌ |
| 17 | Invalid OHLC checked | fatal — high < low, or open/close outside the range | ❌ |
| 18 | Volume anomalies checked | negative volume is fatal; zero volume is recorded, not treated as missing | ❌ |
| 19 | Data-quality report generated | `DatasetQualityReport` with rows, range, missing, duplicates, invalid, timestamp and session anomalies | ❌ |

Reporting `missing_rows = 0` without a session calendar would claim
completeness that was never measured. It reports `None` instead.

### Lineage and gates (20–23)

| # | Item | How it is satisfied | Status |
|---|---|---|---|
| 20 | Dataset checksum generated | SHA-256 over contract, timeframe, event time, **availability**, OHLCV | ❌ |
| 21 | PIT replay passes | including against injected future observations | ❌ |
| 22 | Research-grade gate passes | `SOURCE_VALID → DATA_QUALITY_VALID → POINT_IN_TIME_VALID → RESEARCH_GRADE` | ❌ |
| 23 | Market-claim gate passes | `MARKET_CLAIM_ALLOWED` — requires `DataOrigin.REAL_MARKET` | ❌ |

Item 23 is the one that is not implied by the others. A dataset can pass 22 and
fail 23 — that is exactly what synthetic data does, and the ladder says so
structurally rather than relying on a reader remembering.

---

## The ingestion command

```bash
python -m ai_trading.history.cli data:ingest:futures \
    --provider databento \
    --contract NQM26 \
    --instrument NQ \
    --expiry 2026-06-19 \
    --start 2026-03-01 \
    --end   2026-04-01 \
    --timeframe 1m
```

Five required arguments with no defaults. A command that will guess a timeframe
or a date range eventually guesses wrong and produces a dataset nobody can
reproduce.

### Four refusals, before a single byte is requested

| Refusal | Trigger |
|---|---|
| Unverified source provenance | provider not in `PROVIDER_REGISTRY` |
| Continuous-only data | `manifest.serves_continuous_only` |
| Missing expiry | `--expiry` absent |
| Missing credentials | declared env var absent or empty |

Ordered cheapest-first, so the most common failure (no provider) reports before
a credential check that could not have mattered.

The command then fetches, runs the quality gate against the provider's own
session calendar, runs point-in-time replay, fills the checklist, and assesses
the grade ladder. **There is no path through it that produces an ingested
dataset without a quality report.**

Exit codes: `0` research-grade, `1` ingested but not research-grade, `2`
refused at preflight.

---

## First-dataset scope

Deliberately small. The smallest useful period that validates the pipeline,
expanded only after it clears all 23 items.

| | |
|---|---|
| Instrument | `NQ` |
| Contract | one, e.g. `NQM26` |
| Period | one quarter |
| Timeframe | `1m` if available, else `5m` |
| Derived timeframes | `15m`, `1h` — **after** approval, not alongside |
| Further contracts | after the first passes, not in parallel |

Provider preference: **`databento`** (`GLBX.MDP3`) — contract-level CME data
with documented adjustments. Yahoo and Stooq would be worse even if reachable:
both serve a stitched front-month series, which item 5 and the CLI's second
refusal both reject.

---

## What stays blocked until item 23

The pre-registered ICT campaign. `ICTGate` checks `DataOrigin`, and
`GradeResult.require(MARKET_CLAIM_ALLOWED)` provides a second independent
refusal on the same path. No amount of synthetic calibration opens either.

---

## The external blocker

**Network egress.** Every market-data host is refused at this environment's
proxy (`CONNECT tunnel failed, 403`). Nothing on this checklist can begin until
a provider host is added to the environment's allowlist and a credential is
placed in the environment's secret configuration.

That is one action, and it is not one this system can take for itself.
