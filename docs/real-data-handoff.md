# Real Data Handoff — First NQ Dataset

**For whoever provides the first real dataset.** This is the complete
specification. If everything below is satisfied, ingestion succeeds and the
project moves from `EVIDENCE_PENDING` to having something to research. If any
item is missing, ingestion refuses — by design, before a single byte is
requested.

> ## What is NOT acceptable
>
> - **A random GitHub CSV.** No provenance, no availability semantics, no
>   contract identity, no way to reproduce it. It cannot be audited later, so
>   it is not ingested now.
> - **An anonymous or unattributable dataset.** "Someone sent me NQ data" is not
>   a source. Results cite a `dataset_id` whose hash includes the source name.
> - **A continuous / front-month / stitched series.** Yahoo, Stooq and most free
>   sources serve these. A stitched series has a roll date and an adjustment
>   method baked invisibly into every bar; a backtest cannot disclose an
>   assumption it cannot see.
> - **Fabricated, reconstructed or "close enough" data.** Synthetic data is
>   legitimate for calibration and is permanently barred from market claims.
> - **A screenshot, a PDF, or a chart export.**
>
> These are not preferences. Refusals 1 and 2 in the ingestion command reject
> the first three automatically.

---

## 1. Provider requirements

| Requirement | Detail |
|---|---|
| Named vendor | A vendor with public documentation that can be cited in a result years later |
| Contract-level | Must serve **individual contracts**, not a continuous series. `manifest.serves_continuous_only` is a hard refusal |
| Bars | Must serve `DataKind.BARS` |
| Registered | Added to `PROVIDER_REGISTRY` with a `ProviderManifest` |
| Documented limitations | Known gaps, revision policy, and any resampling the vendor performs |

**Preferred: [databento](https://databento.com), dataset `GLBX.MDP3`** —
contract-level CME data with documented adjustment behaviour and published
availability semantics. Alternatives are acceptable if they meet every row
above; Yahoo and Stooq do not, and would fail refusal 2 even if reachable.

## 2. Contract requirements

| Requirement | Value |
|---|---|
| Instrument | **NQ** (E-mini Nasdaq-100, CME) |
| Contract | **One individual deliverable**, e.g. `NQM26` — never a product code alone |
| Expiry | **Mandatory.** Ingestion refuses without `--expiry` |
| Continuous series | **Refused** |

Expiry is mandatory because without it no roll can ever be justified from the
data, and the contract cannot be placed in time relative to its neighbours.

## 3. Timeframe and coverage

| | |
|---|---|
| Timeframe | **1-minute preferred**; 5-minute acceptable if 1m is unavailable |
| Period | **One quarter** — the smallest window that validates the pipeline |
| Derived timeframes | `15m`, `1h` are resampled **after** approval, never alongside |
| Further contracts | after the first passes all 23 items, not in parallel |

Deliberately small. A first dataset exists to prove the pipeline, and a large
one only makes the first failure more expensive to diagnose.

## 4. Credential mechanism

**Environment secret configuration, by variable name. Nothing else.**

```bash
export DATABENTO_API_KEY="..."      # in the environment's secret config
```

- The variable name is declared in the manifest's `credential_env_vars`.
- Presence is checked; the **value is never read into reporting**, never logged,
  never included in a manifest or a result.
- **Never** in source, in a commit, on a command line (shell history), in an
  issue, or pasted into a chat. The CLI has **no flag that accepts a key** — a
  test asserts no parser action is named `api_key`, `key`, `token`, `secret` or
  `password`.

If a credential is ever pasted somewhere it should not be, treat it as
compromised and rotate it.

## 5. Required metadata

All from the provider. **Nothing assumed, nothing hard-coded.**

| Field | Why it cannot be guessed |
|---|---|
| Exchange | CME — recorded, not inferred |
| Tick size | without it a price series cannot be turned into money |
| Contract multiplier | a backtest that guesses it produces P&L in invented units |
| Tick value | derived from the two above, checked against the vendor |
| Contract expiry | see §2 |
| Coverage window | declared by the provider, **not** inferred from what a query happened to return |

## 6. Timestamp requirements

| Requirement | Detail |
|---|---|
| Timezone | recorded explicitly; **UTC internally**, always |
| Bar stamping | documented: is the bar stamped at open or at close? |
| Bar completion | the policy that decides when a bar is knowable |
| `event_time` | when it happened |
| `available_at` | when this system could first have known it |
| `source_available_at` | when the **vendor** published it — **left `None` if not published** |

**This is where backtests lie by default.** `source_available_at` is never
filled in from `available_at`: doing so converts this project's bar-completion
policy into a claim about the vendor's delivery time, and those are different
assertions. When it is unknown, `availability_quality = ASSUMED_BAR_CLOSE` and
the flags `source_availability_known` / `availability_is_policy_derived` record
the fact.

Consequence: with assumed availability, `latency_sensitive_features` is
`False`. Any latency result would measure the assumption, not the market.

## 7. Session requirements

`SessionMetadata` from the provider: trading weekdays, open and close, the daily
maintenance break, and holidays.

The session calendar must come from the provider because **the missing-bar count
is computed against it**. An assumed session turns a normal close into reported
data loss, or hides real loss inside an assumed break. Without a calendar,
missing intervals report `None` — not `0`, which would claim a completeness
never measured.

## 8. Quality requirements

Run automatically after fetch. There is no path through the command that
produces an ingested dataset without a quality report.

| Check | Verdict |
|---|---|
| Duplicate timestamps | **fatal** if any |
| Invalid OHLC (high < low; open/close outside range) | **fatal** |
| Negative volume | **fatal** |
| Zero volume | recorded, **not** treated as missing |
| Missing intervals | counted against the session calendar |
| Timestamp anomalies | out-of-order or non-monotonic bars |

## 9. Provenance requirements

Recorded per response and hashed into the `dataset_id`: provider, dataset name,
contract, timeframe, requested range, retrieval time, schema version, timezone,
and coverage.

The dataset checksum covers contract, timeframe, `event_time`, **`available_at`**
and OHLCV. Re-deriving availability under a different policy is different
research and must produce a different dataset id.

Datasets are immutable. A correction is a **new** dataset with a new id, never
an edit.

---

## 10. The 23-item onboarding checklist

Every one must pass. `UNKNOWN` blocks approval exactly as `FAIL` does — a gap to
close versus a defect to fix, both disqualifying.

**Current status: 0 of 23. Item 1 blocks every item after it.**

| # | Item |
|---|---|
| 1 | Provider identity — named vendor, registered in `PROVIDER_REGISTRY` |
| 2 | Provider provenance — manifest with kinds, availability policy, limitations |
| 3 | API credential via environment secret |
| 4 | Exchange identified (CME) |
| 5 | Contract identified — one deliverable, e.g. `NQM26` |
| 6 | Expiry identified |
| 7 | Instrument specification captured |
| 8 | Tick size captured from the provider |
| 9 | Contract multiplier captured from the provider |
| 10 | Session calendar captured from the provider |
| 11 | Timezone captured explicitly |
| 12 | Historical coverage declared by the provider |
| 13 | Timestamp semantics documented |
| 14 | Source availability semantics documented (or explicitly absent) |
| 15 | Missing intervals measured against the session calendar |
| 16 | Duplicate rows checked |
| 17 | Invalid OHLC checked |
| 18 | Volume anomalies checked |
| 19 | Data-quality report generated |
| 20 | Dataset checksum generated |
| 21 | Point-in-time replay passes, including injected future observations |
| 22 | Research-grade gate passes |
| 23 | **Market-claim gate passes** — requires `DataOrigin.REAL_MARKET` |

Item 23 is the one not implied by the others. A dataset can pass 22 and fail 23
— that is exactly what synthetic data does.

---

## 11. The exact ingestion command

```bash
python -m ai_trading.history.cli data:ingest:futures \
    --provider databento \
    --contract NQM26 \
    --instrument NQ \
    --expiry 2026-06-19 \
    --start 2026-03-01 \
    --end   2026-06-01 \
    --timeframe 1m \
    --out research_artifacts/nq-nqm26-1m.json \
    --json
```

Five required arguments with **no defaults**: `--provider`, `--contract`,
`--start`, `--end`, `--timeframe`. A command that guesses a timeframe or a date
range eventually guesses wrong and produces a dataset nobody can reproduce.

### Four refusals, before a single byte is requested

| Order | Refusal | Trigger |
|---|---|---|
| 1 | Unverified source provenance | provider not in `PROVIDER_REGISTRY` |
| 2 | Continuous-only data | `manifest.serves_continuous_only` |
| 3 | Missing expiry | `--expiry` absent |
| 4 | Missing credentials | declared env var absent or empty |

Ordered cheapest-first, so the most common failure reports before a credential
check that could not have mattered.

**Exit codes:** `0` research-grade · `1` ingested but not research-grade ·
`2` refused at preflight.

---

## 12. The exact gate that must pass

Five rungs, in order. Each requires the one below it.

```
SOURCE_VALID
    ↓  named provider with a manifest
DATA_QUALITY_VALID
    ↓  no duplicates, no invalid OHLC, no negative volume
POINT_IN_TIME_VALID
    ↓  replay clean, including against injected future observations
RESEARCH_GRADE
    ↓  usable for research  ←  synthetic data can reach here
MARKET_CLAIM_ALLOWED
       requires DataOrigin.REAL_MARKET
```

Verify with:

```bash
python -m ai_trading.project.cli system:status
```

`real_data_status` must read `APPROVED` and `market_claim_status` must read
`ALLOWED`. Until then:

```
REAL_DATA_PENDING:
ICT-FAMILY-V1 is frozen and cannot be evaluated until a dataset reaches
MARKET_CLAIM_ALLOWED.
```

Two independent refusals guard this: the grade ladder, and a direct check of
`DataOrigin` in `require_market_claim_allowed()`. Either alone would do; both
are there so a future change to one cannot quietly open the gate.

---

## 13. What happens once it passes

Exactly one thing, and nothing else:

> **Run `ICT-FAMILY-V1` under `research-protocol-v1`** — 6 hypotheses × 6 labels
> = 36 declared trials, against 4 baselines, with Benjamini-Hochberg correction
> computed on that declared count.

The family is frozen at fingerprint `b3ebb0af7f01b137` and will not be adjusted
in light of what the data shows. That is the point of having frozen it before
the data existed. See
[`ict-family-v1-freeze.md`](ict-family-v1-freeze.md).

A result may be null. A null result under a pre-registered protocol is a real
finding and will be reported as one.
