# Architecture v2

Companion to `integration-audit.md`. That document says what exists; this one
says what we build and why. Decisions are numbered so later phases can cite
them.

---

## AD-1 — Python is the decision core; Pumpi stays a TypeScript service

**Context.** The brief specifies TypeScript interfaces and a `packages/`
layout. Pumpi is TypeScript. The tested engine — 324 tests, structural
look-ahead safety, the prop-firm adjudicator the brief says to preserve — is
Python.

**Decision.** Keep the Python core. Consume Pumpi across a process boundary via
a versioned event contract. Do not port either side.

**Rationale.** Rewriting the Python core in TypeScript discards the exact
properties the brief's Definition of Done demands (no leakage, deterministic
rules, independently enforced risk) and re-earns them at full cost. Porting
Pumpi to Python discards working Solana decode logic. The brief's own
preference list allows "isolated service", and its architectural principle is
separation of concerns — which a process boundary enforces more strongly than a
module boundary.

**Consequence.** The TS interfaces in the brief become the *wire contract*, not
in-process types. `MarketDataProvider` and `ExecutionProvider` are implemented
as Python ABCs with identical method semantics.

---

## AD-2 — Provenance is a required field, not a convention

Every datum entering the system carries:

```python
source        # "ccxt:binanceusdm", "pumpi:pumpfun", "dexscreener"
event_time    # when the thing happened
retrieved_at  # when we fetched it
available_at  # when a decision could first legitimately have used it
```

`available_at` is the load-bearing one. Look-ahead is not only a bar-indexing
problem: a DexScreener liquidity figure fetched today, describing a token as it
was last week, is future information if a backtest reads it at last week's
timestamp. The audit found exactly this hazard in Pumpi's in-place enrichment
(§1.7). Features that cannot state `available_at` are barred from research use.

For exchange bars, `available_at` is the bar's **close**, never its open.

---

## AD-3 — Capability detection is tri-state and fails closed

`exchange.has[...]` returns `True`, `False`, `None`, or `'emulated'`. The
adapter maps these to an explicit enum and refuses unsupported calls with a
typed error rather than letting CCXT raise something venue-specific:

| `has` value | Capability | Behaviour |
|---|---|---|
| `True` | `SUPPORTED` | call |
| `'emulated'` | `EMULATED` | call, tag results as emulated, exclude from latency-sensitive research |
| `False` | `UNSUPPORTED` | raise `CapabilityError` |
| `None` / missing | `UNKNOWN` | raise `CapabilityError` |

Treating `'emulated'` as full support silently mixes derived and native data;
treating `None` as support produces venue-specific exceptions deep in a
research run. Both are prevented at the boundary.

---

## AD-4 — The LLM is structurally excluded from the decision path

Not a guideline — an architectural property. Modules on the path from signal to
order (`features`, `strategies`, `probability`, `adjudicator`, `risk`,
`propfirm`, `execution`) import no model client. The AI layer reads *stored
evidence* and writes *explanations*; it has no return path into sizing,
drawdown, rule enforcement, or order state.

Kill switches live in `execution` and are executable with the AI service down.

---

## AD-5 — Two research domains, one spine

Futures and Solana share everything downstream of normalization:

```
futures (ccxt) ─┐
                ├─→ normalizer → features → signal → probability
solana (pumpi) ─┘                                        │
                                                         ▼
                                    adjudicator → risk → propfirm → execution
```

Domain-specific logic lives only in feature extractors and strategies. The
adjudicator, risk engine, prop-firm engine, and execution gate are
domain-agnostic and already exist.

**Meme-coin caveat carried forward:** prop-firm evaluation applies to futures.
The Solana domain feeds research and paper trading; it does not route through a
prop-firm profile unless a firm actually offers one.

---

## AD-6 — Layout

Extends the existing package rather than introducing a parallel one.

```
src/ai_trading/
  marketdata/       # AD-3. types, provider ABC, ccxt adapter, capability, quality gate, cache
  solana/           # Pumpi event contract, normalizer, Eth→quote rename        [Phase 11]
  features/         # existing; + futures session/VWAP/liquidity features        [Phase 4]
  strategies/       # existing; ICT hypothesis vectors                           [Phase 5]
  backtest/         # existing engine + ruleset + adjudicate
  probability/      # P(TP before SL | features), calibration                    [Phase 7]
  regime/           # trend/range/vol/news classification                        [Phase 7]
  validation/       # purged CV, holdout, DSR, reality check                     [Phase 8]
  adjudicator/      # deterministic ALLOW/REJECT with machine-readable reasons   [Phase 10]
  risk/             # existing
  execution/        # existing paper broker; LIVE remains absent
  monitoring/       # existing drift/divergence
  news/             # normalized events, tiering, blackouts                      [Phase 14]
  social/           # X provider, acceleration, coordination detection           [Phase 13]
  wallet/           # wallet profiling and classification                        [Phase 12]
  ai/               # explanation layer — reads evidence, writes prose           [Phase 15]
```

---

## AD-7 — Phase order deviates from the brief in one place

The brief's Phase 2 is the market-data layer. Its Phase 8 is walk-forward and
adversarial testing. **Validation infrastructure moves earlier, to sit before
any parameter is tuned.**

Reason: the brief's own non-negotiables forbid tuning until something looks
good, and require a locked holdout evaluated once. A holdout established *after*
exploratory work has begun is already contaminated. The holdout split and trial
counter must exist before the first sweep, not after.

Everything else follows the brief's order.

---

## AD-8 — Definition of Done is a gate, not a summary

The brief's ten conditions become executable checks where possible:

| Condition | How it is enforced |
|---|---|
| No look-ahead leakage | `tests/test_causality.py` — future-mutation invariance across all strategies, indicators, features, plus a guard test proving the audit catches a planted cheater |
| Reproducible pipeline | Every research run persists seed + config + code version; `backtest_runs` keyed by run id |
| Beats baselines out of sample | Turnover- **and** volatility-matched random, momentum, mean-reversion, buy-and-hold, session-only |
| Survives realistic costs | `CostModel` presets; results reported under all three, pessimistic default |
| Survives robustness testing | Adversarial suite: cost/delay perturbation, best/worst-trade removal, alternate year/regime/session |
| Risk enforced independently | `risk/` and `execution/` have no model client; kill switches deterministic |
| Prop rules correct | `FirmRuleset` + adjudication tests (static/trailing/locking, session reset, intraday equity, day counting, deadline basis) |
| Paper matches backtest | Existing parity test: broker and backtester agree to floating-point precision |
| Decisions explainable from evidence | Adjudicator emits machine-readable reasons; AI layer may only cite stored values |
| Live fails closed | No live adapter exists; adding one is a separate reviewed decision |

---

## AD-9 — What is deliberately not built yet

- **No live execution adapter.** Absent by design, not omission.
- **No profitability claim.** The system is instrumentation for finding out
  whether edge exists, including the answer "it does not".
- **Dormant Pumpi adapters stay dormant** until their decode logic is verified
  against live program data (audit §1.2).
- **Pumpi enriched token rows stay out of research** until snapshots are
  append-only with `available_at` (audit §1.7).

---

## Open items requiring a human decision

1. **Network allowlist** — nothing reaches a venue until this is widened; Phase 2
   is testable but not runnable.
2. **Firm rules** — still needed to instantiate a real `FirmRuleset`.
3. **Automated-trading permission** — a rules breach voids a pass regardless of
   the equity curve. `allows_automated_trading` defaults to `None` (unverified)
   and must be confirmed with the firm.
4. **Solana data route** — whether Pumpi runs as a service emitting SSE, or
   exports an append-only events table this repo reads.
