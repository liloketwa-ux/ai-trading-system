# ICT Knowledge Integration

**Purpose: knowledge integration. Not strategy optimization, not live trading.**

What was added: a terminology layer, an ontology with our own operational
definitions, a temporal audit recorded as enforceable data, and hypothesis
templates carrying lineage. What was not added: a single feature, signal, or
market claim.

---

## The one-sentence summary

OpenMobius told us **what these concepts are called and how they are taught**.
It could not tell us whether any of them work, and its indicator could not tell
us what was knowable at any past bar. So we took the vocabulary, wrote our own
definitions, and left every claim untested.

## What was integrated

| Layer | Module | Purpose |
|---|---|---|
| Concepts | `knowledge/concepts.py` | `TradingConcept` with provenance and objectivity |
| Cases | `knowledge/cases.py` | `TradingCase`, permanently educational |
| Ontology | `knowledge/ontology.py` | 18 canonical concepts, 73 aliases |
| Temporal audit | `knowledge/temporal_audit.py` | 11 findings, enforced by `assert_importable` |
| Templates | `knowledge/templates.py` | `ICTHypothesisTemplate` + lineage |
| Retrieval | `knowledge/provider.py` | advisory, deterministic |

Nothing vendored: no card text, no case data, no embeddings, no code, no
dependency.

## Three structural guards

These are enforced by what the types **cannot** express, not by convention.

### 1. A case library cannot become a probability

`TradingConcept` has no field for win rate, hit rate, expectancy or case count.
There is nowhere to put a success probability.

`case_outcome_statistics()` exists solely to raise:

> refusing to compute outcome statistics over N case cards. Cases are
> educational examples selected for teaching value, so the sample is chosen by
> outcome. Any rate computed from them measures how often the pattern was
> taught, not how often it worked.

The refusal is placed where somebody would reach for the number, rather than in
a paragraph of a document they did not read.

`case_is_educational_example` is `True` and rejects any attempt to set it
`False`. `is_statistically_representative` returns `False`, always.

### 2. Future-aware signals cannot enter research

`assert_importable()` raises for anything not `POINT_IN_TIME_SAFE`. Ten of
eleven audited outputs raise, including every structural one.

`DELAYED_CONFIRMATION` also raises — it is usable only once the delay is
modelled, and modelling it is our work rather than theirs.

### 3. The case library cannot select hypotheses

`ICTHypothesisTemplate.instantiate()` takes no case identifiers. There is no
argument through which the library could influence which hypotheses exist, so
the required ordering holds by construction:

```
concept definitions → pre-registered hypotheses → real data → statistical test
```

not

```
successful cases → find similar historical trades
```

A test asserts the signature contains no `case`, `cases`, `outcomes` or
`examples` parameter.

## Hypothesis family

Nested, one condition added at a time, so a change in sample size or effect is
attributable to the condition that was added rather than to a reshuffled
definition.

| ID | Conditions | Adds |
|---|---|---|
| `ICT-LS-001` | 1 | liquidity sweep |
| `ICT-DISP-001` | 2 | + displacement |
| `ICT-FVG-001` | 3 | + fair value gap |
| `ICT-MSS-001` | 4 | + market structure shift |
| `ICT-HTF-001` | 5 | + higher-timeframe premium/discount |

`condition_count` is recorded so the multiple-testing budget reflects what was
actually searched. `ICT-FVG-001` is three conditions, not one.

A hypothesis is a **question**. `is_signal` returns `False`, and the payload
carries no side, entry, stop, target or size.

## Lineage

Every hypothesis records the full chain, so a future result can say:

> originated from concepts `liquidity_sweep`, `displacement`, `fair_value_gap`
> in OpenMobius-skill (main@2026-07-06), operationalised as
> `liquidity_sweep:v1`, `displacement_atr:v1`, `fvg:v1` under operational
> definition v1, tested on dataset `nq-nqm26-real_market-abc123`

Fields: `knowledge_source`, `knowledge_source_version`, `concept_ids`,
`feature_ids`, `feature_versions`, `operational_definition_version`,
`dataset_id`, `protocol_version` (defaults to `research-protocol-v1`).

Unversioned features are refused: a feature that cannot be traced back to the
code that computed it breaks the chain.

## Retrieval

`TradingKnowledgeProvider` exposes exactly five methods: `search_concepts`,
`get_concept`, `search_cases`, `get_case`, `related_concepts`. No `evaluate`,
no `score_setup`, no `suggest_trade`, no `override`.

`require_no_authority_over()` raises for feature calculations, temporal
integrity, risk rules, adjudication and prop-firm rules.

Retrieval is **deterministic** — lexical rank with ties broken by identifier.
A hypothesis family that reorders between runs cannot be pre-registered, so
reproducibility outranks recall at this corpus size.

## Visualization and the knowledge panel

Neither is built. The visual grammar is worth reusing (FVG/OB rectangles,
liquidity lines, BOS/CHoCH markers, entry/stop/target, pivot labels), and any
future panel renders **our validated features**, never OpenMobius calculations.

The panel's intended honesty:

```
Concept:              Fair Value Gap
Definition source:    OpenMobius-skill (terminology only)
Operational defn:     AI Trading System FVG:v1
Research status:      UNTESTED
Historical evidence:  NONE
```

Preferable to presenting educational concepts as established market laws — which
is what a panel showing only the concept name and a confident description would
do.

## Licence position

`ATTRIBUTION.md` declares Apache-2.0 and links `./LICENSE`. **That file is
absent.** The same file states 380 concepts and 584 cases; the actual counts are
726 and 1,282.

Apache-2.0 would permit vendoring with attribution, but the grant is
unverifiable from what was supplied and the underlying material derives from
named third parties' course content and 306 YouTube videos. So we took the
minimum-metadata path: canonical names, aliases and structural observations —
facts about terminology, not expressive content. All 18 definitions are our own
paraphrase of widely published ICT/SMC terms.

Attribution is preserved in `KnowledgeSource`, recording the licence state
verbatim including the missing file, with `redistribution_permitted=False`.

## What this phase deliberately did not do

No real-market research. No holdout spent. No ICT parameters optimised. No
BUY/SELL signals. No probability from case counts. No case used as a training
target. No future-aware indicator value imported. No live trading. No new
runtime dependency.

## The boundary for the next phase

Everything above is knowledge. The next phase is the first that touches
computation, and it may begin only when **all** of these hold:

1. A real NQ dataset reaches `MARKET_CLAIM_ALLOWED` — all 23 onboarding items,
   which currently stand at 0 because no provider is reachable.
2. The 5 objective concepts are implemented as features through
   `FeatureSnapshot` / `derive_feature()`, each with its own `available_at`
   rule and point-in-time tests.
3. The 11 partially objective `v1` definitions are implemented and tested, or
   explicitly deferred.
4. The hypothesis family is declared under `research-protocol-v1` with its
   trial count fixed before any data is examined.

Until then the concepts stay `UNTESTED`, the ICT gate stays closed, and nothing
in this layer computes anything about a market.
