# ICT / SMC Ontology

18 canonical concepts, 73 aliases. Machine-readable form:
`ai_trading.knowledge.ontology.ONTOLOGY`.

| | Count |
|---|---|
| **OBJECTIVE** — implemented in Phase 10 as `:v1` features | **5** |
| **PARTIALLY_OBJECTIVE** — need our own definition + tests | **11** |
| **SUBJECTIVE** — knowledge-only until formalised | **2** |
| With research evidence | **0** |

---

## The classification is less flattering than the literature

Very little of ICT is objective in the sense that matters here: *two
implementers reading the same description produce the same output*. A fair
value gap is. An order block is not — "the last opposing candle before
displacement" leaves *which* displacement and *how strong* to the reader.

Both are perfectly teachable. Only one is computable without our adding choices
the source never made.

Where we add those choices the concept becomes `PARTIALLY_OBJECTIVE` and the
operational definition is **ours**, versioned. The distinction it preserves is
not pedantry: a later null result is then a result about *AI Trading System
OB:v1*, not about the idea of order blocks. Those are different claims, and
conflating them is how a research programme convinces itself it has disproved
something it never tested.

## OBJECTIVE (5) — ✅ IMPLEMENTED

Computable from bars without judgement. All five are implemented as versioned,
point-in-time-safe features in Phase 10. See
[`ict-objective-features.md`](ict-objective-features.md).

| Concept | Feature | Aliases | Availability |
|---|---|---|---|
| **Fair Value Gap** | `fvg:v1` | FVG, imbalance, inefficiency, liquidity void | close of bar i+2 |
| **Displacement** | `displacement:v1` | expansion, energy candle | close of bar i |
| **Equal High** | `equal_high:v1` | EQH, relative equal high | later pivot's confirmation |
| **Equal Low** | `equal_low:v1` | EQL, relative equal low | later pivot's confirmation |
| **Liquidity Sweep** | `liquidity_sweep:v1` | stop hunt, liquidity grab, raid, turtle soup, purge | close of bar i |

Implemented does **not** mean tested. All five remain `UNTESTED` and none has
evidence.

Three carry caveats worth stating plainly:

- **Displacement** is objective *only* with a rolling ATR computed on bars ≤ i.
  A whole-array ATR makes it retrospective — that is the exact defect found in
  the surveyed indicator.
- **Liquidity Sweep** is objective *given a point-in-time swing reference*.
  Against an unconfirmed pivot it silently inherits that pivot's lookahead.
- **Equal High/Low** need a tolerance. Sources say "roughly equal"; the
  0.1 × ATR is ours, and the pair is never dissolved by later bars.

## PARTIALLY_OBJECTIVE (11) — ⏸ DEFERRED

Real concepts whose published definitions leave choices open. Each has an
operational definition of ours at `v1`, and each needs tests before use.

| Concept | What the source leaves open |
|---|---|
| **Order Block** | which displacement, how strong, and whether blocks get re-drawn |
| **Market Structure Shift** | what counts as the prevailing sequence |
| **BOS** | direction relative to that sequence |
| **CHoCH** | whether "first opposing break" is materially different from MSS |
| **Protected High** | "protected" is a claim about intent |
| **Protected Low** | as above |
| **Premium** | which reference range — dealing, session, or swing |
| **Discount** | as above |
| **Equilibrium** | as above |
| **Breaker Block** | inherits Order Block's arbitrariness |
| **Killzone** | window times vary between sources |

Two decisions worth surfacing:

**MSS and CHoCH are registered separately.** Some sources treat CHoCH as the
*first* opposing break rather than any of them. Merging them would hide the
disagreement; registering both means a test that cannot distinguish them
produces a finding about the literature rather than a quiet modelling choice.
The alias guard caught this — an early draft both aliased CHoCH to MSS and
registered it separately, which would have made lookup order-dependent.

**Order blocks never revise.** Sources describe blocks being re-drawn as price
develops. A zone that changes as price develops has no stable historical value,
so `OB:v1` creates a new block instead of moving an old one.

## SUBJECTIVE (2) — ⏸ DEFERRED

Knowledge-only. `require_operationalized()` raises.

**SMT Divergence** — requires choosing the correlated instrument, the lookback,
and what counts as failing to confirm. Formalisable in principle; needs a second
instrument's data and a stated correlation basis, neither of which exists yet.

**Inducement** — defined by *intent*. Whether liquidity was "left to attract"
entries is unobservable, and no rule in the sources distinguishes an inducement
from an ordinary swing after the fact.

Neither classification is a claim that the concept is worthless. It is a claim
about definability, and nothing more.

## Alias resolution

73 aliases fold to 18 concepts via `normalize_alias()` — case, punctuation,
spacing and hyphenation only. No stemming, no synonym expansion: deciding that
"sweep" and "raid" are the same term is a modelling judgement that belongs in an
explicit alias list, not in a string function.

Why it matters: the same idea appears as "MSS", "market structure shift",
"structure shift" and "market structure break" depending on who is teaching.
Without normalisation a hypothesis family tests one idea four times and reports
four trials, and every multiple-testing correction downstream is then wrong in
the permissive direction.

Collisions are refused at registration.

## Implementation status

| | Count | Status |
|---|---|---|
| OBJECTIVE | 5 | ✅ implemented as `:v1`, `UNTESTED` |
| PARTIALLY_OBJECTIVE | 11 | ⏸ `v1` definitions written, not implemented |
| SUBJECTIVE | 2 | ⏸ not formalised, may never be |

The 13 deferred concepts remain registered and unavailable to research;
`may_enter_feature_engine` is `False` for each, asserted by test.

Every operational definition states its own `available_at` rule, because that is
where the audit of the reference implementation found every single defect.

## Research status

**All 18 concepts: `UNTESTED` or `OPERATIONALIZED`. None has evidence.**

`OPERATIONALIZED` means "we have written down how to compute it". It does not
mean the concept predicts anything, and `has_evidence` returns `False` for
every concept in this ontology.
