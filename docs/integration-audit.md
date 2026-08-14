# Integration Audit

**Phase 1 deliverable.** Inspection of the systems in scope before any code was
merged. Written to be falsifiable: every claim below was verified by opening the
code, not inferred from the brief.

---

## 0. Scope corrections — read first

Four premises in the task brief do not match what is actually available. None
are blockers, but building as specified would have produced work that could not
run.

### 0.1 The CCXT repository was not supplied

`ccxt-master` is not present anywhere on this machine. The only upload was
`pumpideb-main.zip`. **No part of this audit inspected the CCXT source tree**,
and any statement here about CCXT comes from the installed Python package, not
from reading that repository.

This turns out not to matter, and the resolution is better than the one asked
for — see §3.

### 0.2 The trading system is no longer inside Quantum Workflow

The brief says the AI trading-system design lives in the Quantum Workflow repo.
It did; it was removed at explicit instruction earlier and now lives in this
repository (`liloketwa-ux/ai-trading-system`). Quantum Workflow is a Next.js
lead-qualification SaaS with no trading code.

**There is no trading design document in Quantum Workflow to audit.** The design
being extended is `docs/ai-trading-system-design.md` here.

### 0.3 The brief is written in TypeScript; the tested engine is Python

The target architecture specifies `packages/market-data/ccxt-adapter` and TS
interfaces (`interface MarketDataProvider { ... }`). Pumpi is TypeScript. But
the working, tested system — 324 passing tests, structural look-ahead
guarantees, the prop-firm adjudicator the brief says to preserve — is Python.

Implementing the brief literally means either rewriting the tested Python core
in TypeScript, or running two engines. Both are worse than the alternative in
§3. This is the single most consequential decision in this document.

### 0.4 The network policy blocks every data source

Verified this session:

| Host | Result |
|---|---|
| `api.binance.com` | 403 CONNECT (policy denial) |
| `solana-rpc.publicnode.com` | 403 CONNECT |
| `api.dexscreener.com` | 403 CONNECT |
| `datafeed.dukascopy.com` | 403 CONNECT |

Package registries (PyPI, npm) *are* allowlisted. So libraries install, but no
adapter can reach a live venue. Phase 2 is therefore built and tested against
static metadata and fixtures; it cannot be exercised end-to-end until the
environment allowlist is widened.

---

## 1. Pumpi / RocketFi (`pumpideb-main`)

A pnpm/TypeScript monorepo: `artifacts/api-server` (Express ingestion + API),
`artifacts/rocketfi` (web UI, 138 files), `lib/db` (Drizzle schema),
`contracts-rocketfi` (Solidity), plus deploy tooling.

### 1.1 What genuinely exists and is worth reusing

| Capability | File | Verdict |
|---|---|---|
| Solana RPC log indexer base | `lib/adapters/solanaRpcBase.ts` | **Reuse** — reconnect/backoff, endpoint rotation, failed-tx filtering, `getTransaction` helper. The strongest asset in the repo. |
| Pump.fun ingestion | `lib/adapters/pumpfun.ts` | **Reuse** — live |
| PumpSwap ingestion | `lib/adapters/pumpswap.ts` | **Reuse** — live |
| Raydium LaunchLab | `lib/adapters/raydium-launchlab.ts` + `launchlabDecode.ts` | **Reuse** — live, and has decode unit tests |
| DexScreener enrichment | `lib/dexscreener.ts` | **Reuse** |
| Birdeye enrichment | `lib/birdeye.ts` | **Reuse** |
| Jupiter token discovery | `lib/jupiter-tokens.ts` | **Reuse** |
| SSE streaming | `lib/tradeEmitter.ts`, `routes/feed.ts` | **Reuse as transport** |
| Trade verification | `lib/tradeVerifier.ts` (+ tests) | **Reuse** |
| SSRF-safe fetch | `lib/safeUriFetch.ts` (+ tests) | **Reuse** |

### 1.2 Finding: six of the eight named adapters are dormant

The brief lists eight platform adapters as existing ingestion. They exist as
*source files*, but the active registry is one entry:

```ts
// lib/adapters/index.ts
const ADAPTERS: AdapterEntry[] = [
  { name: "pump_stream_manager", start: startPumpStreamManager },
];
```

`startPumpStreamManager` imports exactly three indexers: `pumpfun`,
`pumpswap`, `raydium-launchlab`.

| Adapter | Has `start*()` | Actually started | Where else referenced |
|---|---|---|---|
| pump.fun | ✅ | ✅ | — |
| PumpSwap | ✅ | ✅ | — |
| Raydium LaunchLab | ✅ | ✅ | — |
| Raydium AMM | ✅ | ❌ | `routes/trades.ts` (label only) |
| Meteora | ✅ | ❌ | `birdeye.ts`, `routes/tokens.ts` (label only) |
| Orca | ✅ | ❌ | `birdeye.ts`, `routes/tokens.ts` (label only) |
| Moonshot | ✅ | ❌ | `enrichment.ts` (label only) |
| LetsBonk | ✅ | ❌ | `enrichment.ts` (label only) |
| daos.fun | ✅ | ❌ | — |

The dormant five appear elsewhere only as **platform name strings** in
enrichment and route filters — not as ingestion. Treating them as live data
sources would silently produce a dataset covering three platforms while
labelled as covering eight. Reviving them is real work (each needs program-ID
and instruction-decode verification), not a registry edit.

### 1.3 Finding: `Eth` naming on a Solana system

The normalized event carries `ethAmount`, `priceEth`, `marketCapEth`,
`virtualEthReserves`, `virtualTokenReserves` — on Solana, where the quote asset
is SOL. Verified in `lib/tradeEmitter.ts`:

```ts
export interface TradeEvent {
  trade: { ethAmount: string; priceEth: string | null; ... }
}
```

This is a legacy leak from an EVM ancestor. It must be renamed at the
normalizer boundary (`quoteAmount`, `priceQuote`, `quoteMint`) and **must not**
propagate into the research schema, where a mislabelled quote asset silently
corrupts every downstream price and market-cap figure.

### 1.4 Finding: the persistence layer is four tables

Actual Drizzle schema: `deposits`, `profiles`, `tokens`, `trades`. That is all.

The brief's ~30-table research schema (`market_features`, `wallet_profiles`,
`social_posts`, `trade_adjudications`, `backtest_runs`, …) is **greenfield**,
not an extension. `tokens` and `trades` are useful seeds for token/trade
provenance and nothing more.

### 1.5 Finding: three of the intelligence engines have no seed code

| Brief section | Reusable code in Pumpi |
|---|---|
| `WalletIntelligenceEngine` | **None.** Trader addresses are captured on trades; there is no profiling, PnL attribution, or classification. |
| `SocialIntelligenceProvider` | **None.** No X/Twitter ingestion of any kind. |
| Fundamental news engine | **None.** |

Trader-address capture is a genuine head start on wallet intelligence — the
hard part (address attribution from tx decode) is done. The analytics are not.

### 1.6 Must remain isolated / must not be imported

- `artifacts/rocketfi` — full web app, wallet connect, launchpad UI. Out of scope.
- `contracts-rocketfi` — Solidity. Irrelevant to a Solana research system.
- `lib/db` auth tables (`profiles`, `deposits`), `auth-jwt.ts`, `wallet-auth.ts`,
  `email-otp.ts`, `objectStorage.ts` — application concerns. Importing these
  drags user accounts and custody into a research platform.
- `lightweight-charts` usage in the UI — reusable *as a charting choice*, but it
  is not "TradingView", and the brief is right to say so.

### 1.7 Dangerous coupling observed

1. **Adapters write directly to the DB and emit SSE in the same path.** Ingestion,
   persistence, and fan-out are not separable, so a research consumer cannot
   subscribe without also running the write path.
2. **Enrichment mutates token rows in place.** DexScreener/Birdeye overwrite
   price/liquidity with no `retrievedAt`, so a historical row cannot be
   reconstructed as it appeared at decision time. **This is a look-ahead vector**:
   backtesting against enriched token rows would use values that did not exist
   when the trade decision was made. Any research use of this data requires
   append-only snapshots with `availableAt`, not in-place updates.
3. **`process.env` read at module scope** (`solanaRpcBase.ts`) — configuration
   is not injectable, which makes deterministic fixtures awkward.

---

## 2. This repository (Python trading engine)

324 passing tests. Audited and kept:

| Module | Status | Relevance to brief |
|---|---|---|
| `backtest/engine.py` | Structural look-ahead safety | Satisfies "prevent lookahead bias" at the engine level |
| `backtest/ruleset.py` + `adjudicate.py` | Swappable `FirmRuleset` | Already the `PropFirmProfile` the brief asks for — static/trailing/locking drawdown as separate classes, session-reset timezone, intraday vs closing equity, day-counting, deadline basis |
| `backtest/challenge.py` | Adjudicator | The "277 tests" to preserve (now 324) |
| `risk/manager.py` | Sizing, caps, drawdown halt | Fails closed; the brief's risk engine extends it |
| `execution/` | Paper broker + risk-gated order manager, kill switch | Matches "BACKTEST → PAPER → LIVE", no live adapter by design |
| `monitoring/` | PSI/KS drift, divergence, events | Satisfies data-drift and calibration-tracking requirements |
| `strategies/structure.py` | Swings, BOS, FVG, order blocks, sweeps | The ICT feature vector the brief specifies, already causal |

**Gap against the brief:** no probability model, no regime engine, no
walk-forward/purged CV, no multiple-testing correction, no market-data layer,
no news/social/wallet layers.

---

## 3. CCXT — resolution

The TS repo is absent, but **CCXT ships a first-class Python package**, and
PyPI is allowlisted. Installed and verified this session: `ccxt 4.5.73`,
103 exchanges.

Two findings that shape the adapter design:

**3.1 Capability detection works entirely offline.** `exchange.has` is static
metadata requiring no network, so the capability layer the brief demands is
fully testable in this environment:

| capability | binanceusdm | okx | bybit | kraken |
|---|---|---|---|---|
| `fetchOHLCV` | True | True | True | True |
| `fetchFundingRate` | True | True | **emulated** | False |
| `fetchOpenInterest` | True | True | True | **None** |
| `setLeverage` | True | True | True | False |

**`has` is not boolean.** It returns `True`, `False`, `None` (absent), or the
string `'emulated'`. A truthiness check treats `'emulated'` as full support and
`None` as a hard no — both wrong. The adapter models this as a tri-state.

**3.2 The CCXT Pro question is moot.** Since v4, `ccxt.pro` is bundled in the
main package — verified: `ccxt.pro.binanceusdm` exposes 59 `watch*` methods, and
the sync class already carries 52. No separate dependency, no separate types.
WebSocket support still sits behind the same interface so the core never
depends on `watch*` shapes.

**Decision: depend on the `ccxt` PyPI package. Do not vendor, fork, or port the
TS repo.**

---

## 4. Proposed dependency boundaries

The governing constraint: the tested Python core keeps the guarantees
(causality, deterministic adjudication, fail-closed risk). Pumpi keeps its
runtime. Neither is rewritten.

```
┌──────────────────────────────────────────────────────────┐
│  Pumpi ingestion service  (TypeScript, unchanged repo)   │
│  pump.fun · PumpSwap · LaunchLab  → normalized events    │
└───────────────────────┬──────────────────────────────────┘
                        │  wire contract only:
                        │  SolanaTokenEvent JSON over SSE/HTTP,
                        │  or an append-only events table
┌───────────────────────▼──────────────────────────────────┐
│  ai-trading-system  (Python — this repo)                 │
│                                                          │
│  marketdata/  ← ccxt (PyPI)   solana/ ← Pumpi contract   │
│         └──────────┬───────────────────┘                 │
│                    ▼                                     │
│   features → strategies → probability → adjudicator      │
│                    → risk → propfirm → execution         │
└──────────────────────────────────────────────────────────┘
```

**Rules:**

1. **No source-level merge of Pumpi.** It is consumed across a process boundary
   via a versioned event contract. Pumpi's `package.json`, Solidity, UI, and
   auth never enter this repo.
2. **CCXT is a package dependency**, never vendored.
3. **The normalizer is the only place** Pumpi's `Eth`-named fields are allowed to
   appear; they are renamed on the way in and the parser version is recorded.
4. **Enriched Pumpi token rows are not a research source** until snapshots are
   append-only with `availableAt`. Trade events (immutable, tx-anchored) are
   safe; mutated token rows are not.
5. **The LLM never touches** arithmetic, sizing, drawdown, order state, or rule
   enforcement — enforced structurally by keeping those in tested modules with
   no model calls in the path.

---

## 5. Verdict table

| Component | Reuse directly | Adapt | Isolate | Rewrite |
|---|---|---|---|---|
| `solanaRpcBase.ts` | ✅ | | | |
| pump.fun / PumpSwap / LaunchLab indexers | ✅ | | | |
| DexScreener / Birdeye / Jupiter clients | ✅ | | | |
| SSE emitter | | ✅ (as contract) | | |
| Pumpi `TradeEvent` shape | | ✅ (rename `Eth`→quote) | | |
| Dormant 5 adapters | | | ✅ (revive later, verify decode) | |
| Pumpi token enrichment | | | | ✅ (append-only + `availableAt`) |
| Pumpi DB schema | | | | ✅ (research schema is greenfield) |
| RocketFi UI / contracts / auth | | | ✅ (never import) | |
| Wallet / social / news engines | | | | ✅ (no seed code exists) |
| CCXT | ✅ (PyPI package) | | | |
| Python backtest / risk / propfirm / execution | ✅ | | | |

---

## 6. What this audit could not establish

Stated explicitly so it is not mistaken for coverage:

- **CCXT source was never read** — repo absent. Claims rest on the installed package.
- **No adapter was executed.** Network is blocked, so Pumpi's decode correctness,
  RPC reconnect behaviour, and enrichment accuracy are unverified at runtime.
  Assessment is from reading source and its unit tests only.
- **Pumpi's data quality is unmeasured.** No claim is made about completeness,
  duplicate rate, or latency of its historical trade data.
- **The dormant adapters' decode logic was not validated** against live program
  data; only `launchlabDecode` has tests.
