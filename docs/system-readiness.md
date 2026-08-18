# System Readiness

**`project_status = EVIDENCE_PENDING`**

The research engine is built. It has never been run on a real market. Those two
sentences are the whole report; everything below is detail.

Generated form of this document:

```bash
python -m ai_trading.project.cli system:status
python -m ai_trading.project.cli system:audit
```

| | |
|---|---|
| `project_status` | `EVIDENCE_PENDING` |
| `research_protocol_version` | `research-protocol-v1` (frozen) |
| `ict_family_version` | `ICT-FAMILY-V1` (`v1`) |
| `ict_family_fingerprint` | `b3ebb0af7f01b137` |
| `test_count` | 1,555 |
| `real_data_status` | `NOT_AVAILABLE` |
| `market_claim_status` | `BLOCKED` |
| `live_execution_status` | `DISABLED` |
| `primary_prop_target` | `TOPSTEP_COMBINE_100K` |
| `next_required_external_action` | `PROVIDE_APPROVED_REAL_NQ_DATA` |

Every value is **derived**, not typed. The fingerprint comes from
`verify_frozen()`, which fails if the family drifted. The prop target is
resolved out of the rules registry and raises if that ruleset is missing. The
live-execution verdict enumerates `Broker` subclasses shipped in the package.
The test count is produced by running pytest's collector. There is no timestamp
anywhere in the payload, so two runs at one commit produce identical bytes.

---

## READY

Implemented, tested, and usable **for engineering and for synthetic research**.

| Area | What is there |
|---|---|
| Temporal integrity | `event_time` / `available_at` / `source_available_at` kept distinct; availability composes as max over inputs; point-in-time replay including injected future observations |
| Feature engine | Streaming, append-only. Prefix determinism is the governing property: features for bars 0..n are identical whether or not later bars exist |
| Objective ICT features | Five, versioned: `liquidity_sweep:v1`, `displacement:v1`, `fvg:v1`, `equal_high:v1`, `equal_low:v1` |
| Hypothesis family | `ICT-FAMILY-V1`, frozen: 6 hypotheses × 6 labels = **36 declared trials**, 24 baseline comparisons |
| Datasets | Immutable, content-addressed; checksum includes `available_at`, so re-derived availability is a different dataset |
| Holdout | Locked, ledgered, spent once per research version; only `FINAL_HOLDOUT_EVAL` may reach it |
| Validation | Purged and embargoed walk-forward, per-fold reporting, robustness matrix (cost / slippage / bar-delay), trade-removal analysis |
| Multiple testing | Benjamini-Hochberg, Bonferroni, Deflated Sharpe, computed against the **declared** trial count |
| Baselines | `random`, `hold_matched_random`, `momentum`, `mean_reversion`, required before any verdict |
| Calibration | Five synthetic generators with sealed ground truth; the machinery detects known edges, rejects the null, and refuses a sub-cost edge on economic grounds |
| Backtester | Event-driven, lookahead-safe, explicit cost and execution models |
| Prop-firm rules | 25 rulesets across four firms; 160 source-verified rules each carrying a url, a `verified_at` date and a verification method; MLL, DLL modes, consistency, payouts |
| Risk | Layered resolution by minimum; user policy (10% desired daily return, 2% ceiling, 0.25% baseline) structurally unable to raise a firm limit |
| Knowledge | OpenMobius terminology only — 18 concepts, 73 aliases — with a temporal audit barring 9 of 11 audited outputs from research |

## NOT READY

Nothing here may support a statement about a real market.

- **No result exists.** `ICT-FAMILY-V1` has never been evaluated. Not "evaluated
  and inconclusive" — never run.
- **Synthetic results describe the generator.** The calibration work is
  legitimate and is evidence that the machinery is not blind. It is not
  evidence about NQ, and no ladder rung exists that would let it become so.
- **No edge is claimed.** Not for ICT, not for momentum, not for mean
  reversion. The hypotheses are questions with no answers yet.
- **Latency-sensitive features are unusable.** With no published
  `source_available_at`, availability is policy-derived; a latency result would
  measure the assumption rather than the market.
- **No continuous series.** Contract-level data only, because an adjustment
  method baked invisibly into every bar is not something a backtest can
  disclose. No adjustment implementation exists.
- **Prop-firm pass probability is unestimated.** The rules are modelled and the
  account simulation runs; what has never been supplied is a strategy with
  out-of-sample evidence to simulate.

## BLOCKED

Requires resources this system cannot obtain for itself.

| Blocker | Detail |
|---|---|
| **Network egress** | Every market-data host is refused at this environment's proxy (`CONNECT tunnel failed, 403`): databento, Yahoo, Stooq, CME, Tiingo. GitHub and package registries are reachable; data vendors are not |
| **Provider credential** | Must arrive through the environment's secret configuration, by variable name. Never in source, a commit, a command line, or a chat message |
| **Provider adapter** | `PROVIDER_REGISTRY` is empty on purpose — an empty registry is what makes ingestion refuse rather than improvise |
| **Solana indexer** | Pumpi latency modelling stays `UNVERIFIED` without one |

Onboarding progress: **0 of 23 items.** Item 1 blocks every item after it. See
[`real-data-handoff.md`](real-data-handoff.md).

## PROHIBITED

Enforced in code, not by discipline. `require_action_permitted()` refuses each
by name; `require_real_data_approved()` guards the campaign.

- Running `ICT-FAMILY-V1` on synthetic data for evidence
- Using OpenMobius case examples as statistical evidence
- Altering hypothesis definitions, thresholds, or event windows
- Adding features to the family, expanding it, or reordering it
- Spending the locked holdout
- Producing trade signals
- Optimising for Topstep pass probability
- Enabling live execution, or accepting funded-account credentials

## NEXT ACTION

One action. It is external, and it is not one this system can take.

> **Obtain and authorise access to a real NQ data provider** — network egress
> plus a credential in the environment's secret configuration.

Once the first dataset reaches `MARKET_CLAIM_ALLOWED`, the next permitted
research action is:

> **Run `ICT-FAMILY-V1` under `research-protocol-v1`.**

Nothing supersedes that. This document deliberately contains no roadmap beyond
it: a list of speculative future phases would compete for attention with the
one action that actually unblocks the project.

---

## Integrity audit

Eight checks, read-only, all passing. Each *exercises* the property rather than
asserting a guard exists — `hasattr(engine, "refuses_lookahead")` passes forever
once someone adds the attribute.

| Check | How it is exercised |
|---|---|
| `no_future_data_access` | prefix determinism at cuts 20/40/60/79 over 80 bars |
| `no_holdout_leakage` | all four non-holdout purposes probed against holdout dates |
| `no_mutable_v1_family` | fingerprint verified; absent methods; addition attempted and refused |
| `no_unverified_rules_as_verified` | 160 source-verified rules checked for url + date + method; 51 `NOT_APPLICABLE` rules checked for an explaining note |
| `no_openmobius_cases_in_research` | case statistics called and required to refuse; every non-safe output required to refuse import |
| `no_live_execution_route` | `Broker` subclasses shipped in the package enumerated |
| `no_credentials_in_code` | AST scan of 128 files for secret-shaped assignments; CLI flags checked |
| `no_synthetic_market_claims` | a `RESEARCH_GRADE` synthetic dataset built and refused by the gate |

```bash
python -m ai_trading.project.cli system:audit   # exit 1 on a critical failure
```
