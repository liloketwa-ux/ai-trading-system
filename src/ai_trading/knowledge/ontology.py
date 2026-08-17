"""The canonical ICT/SMC ontology.

Eighteen concepts, each classified by whether it can be computed from price
without a human making a judgement the source material leaves open. The
classification is the load-bearing part: it decides what may become a feature.

Being honest about this is uncomfortable, because the classification is less
flattering than the literature. Very little of ICT is objective in the sense of
"two implementers reading the same description produce the same output". A fair
value gap is; an order block is not, because "the last opposing candle before
displacement" leaves *which* displacement and *how strong* to the reader. Both
are perfectly teachable. Only one is computable without our adding choices the
source never made.

Where we add those choices, the concept becomes ``PARTIALLY_OBJECTIVE`` and the
operational definition is **ours** and versioned. The distinction it preserves:
a later null result is a result about the AI Trading System's v1 definition of
an order block, not about the idea of order blocks. Those are different claims
and conflating them is how a research programme convinces itself it has
disproved something it never tested.

Operational definitions here are stated, not implemented. Implementation
against ``FeatureSnapshot`` and ``derive_feature()`` is the next phase's work,
and every one of them carries an explicit ``available_at`` rule because that is
where the previous audit found every defect.
"""

from __future__ import annotations

from .concepts import (
    ConceptCategory,
    ConceptObjectivity,
    ConceptRegistry,
    KnowledgeSource,
    ResearchStatus,
    TradingConcept,
)

__all__ = ["OPENMOBIUS_SOURCE", "build_ontology", "ONTOLOGY", "CANONICAL_CONCEPTS"]

#: Attribution for the surveyed repository. Content is **not** vendored: the
#: distribution carries no LICENSE file despite ATTRIBUTION.md referencing one,
#: so only canonical names, aliases and our own definitions are recorded here.
OPENMOBIUS_SOURCE = KnowledgeSource(
    name="OpenMobius-skill",
    version="main@2026-07-06",
    url="https://github.com/(OpenMobius-skill)",
    license="Apache-2.0 (declared in ATTRIBUTION.md; LICENSE file absent from "
            "the reviewed distribution)",
    attribution_required=True,
    redistribution_permitted=False,
    note="Surveyed as a terminology reference. No concept text, case text, "
         "embedding or code was copied. Definitions below are this project's "
         "own paraphrase of widely published ICT/SMC terminology.",
)

_V1 = "v1"


def _c(name, category, objectivity, definition, *, aliases=(), related=(),
       operational="", notes=""):
    return TradingConcept(
        canonical_name=name, category=category, objectivity=objectivity,
        definition=definition, source=OPENMOBIUS_SOURCE,
        source_version=OPENMOBIUS_SOURCE.version, aliases=tuple(aliases),
        related_concepts=tuple(related), research_notes=notes,
        research_status=(ResearchStatus.OPERATIONALIZED if operational
                         else ResearchStatus.UNTESTED),
        operational_definition=operational,
        operational_definition_version=_V1 if operational else "",
    )


CANONICAL_CONCEPTS = [
    # ---- OBJECTIVE -----------------------------------------------------
    _c("Fair Value Gap", ConceptCategory.IMBALANCE, ConceptObjectivity.OBJECTIVE,
       "A three-candle imbalance where the first and third candles' ranges do "
       "not overlap, leaving a price band that traded through in one direction "
       "without two-sided activity.",
       aliases=("FVG", "imbalance", "inefficiency", "liquidity void"),
       related=("Displacement", "Premium", "Discount"),
       operational=(
           "Bullish when high[i] < low[i+2]; bearish when low[i] > high[i+2]. "
           "Gap band = (high[i], low[i+2]) or (high[i+2], low[i]). Minimum size "
           "0.2 x ATR(14) computed as of bar i+2 using bars <= i+2 only. "
           "formed_at = bar i+1; available_at = close of bar i+2, because the "
           "third candle is required to observe it. Mitigation is NOT stored on "
           "the record -- it is a function of a decision time, not of the gap."),
       notes="The only structural concept in this ontology that two independent "
             "implementers would compute identically from the same bars."),

    _c("Displacement", ConceptCategory.STRUCTURE, ConceptObjectivity.OBJECTIVE,
       "An unusually large directional candle or run, taken to indicate "
       "committed rather than incidental order flow.",
       aliases=("displacement candle", "expansion", "energy candle"),
       related=("Fair Value Gap", "Market Structure Shift"),
       operational=(
           "|close - open| >= 2.0 x ATR(14), where ATR is computed on bars <= i "
           "(rolling, never whole-array). Direction from sign(close - open). "
           "formed_at = available_at = close of bar i. Magnitude reported in ATR "
           "multiples as of bar i."),
       notes="Objective only once the ATR window is pinned to a rolling "
             "as-of-bar computation; a whole-array ATR makes it retrospective."),

    _c("Equal High", ConceptCategory.LIQUIDITY, ConceptObjectivity.OBJECTIVE,
       "Two or more swing highs at approximately the same price, taken to mark "
       "a pool of resting stop orders above them.",
       aliases=("EQH", "equal highs", "double top liquidity", "relative equal high"),
       related=("Liquidity Sweep", "Equal Low"),
       operational=(
           "Two confirmed swing highs whose prices differ by <= 0.1 x ATR(14) "
           "as of the later pivot's confirmation bar, separated by at least 3 "
           "bars. available_at = confirmation bar of the later pivot. The pair "
           "is never dissolved by later bars: a level that was equal remains a "
           "historical fact."),
       notes="Objective given an explicit tolerance. Sources state the tolerance "
             "qualitatively ('roughly equal'), so the 0.1 ATR is ours."),

    _c("Equal Low", ConceptCategory.LIQUIDITY, ConceptObjectivity.OBJECTIVE,
       "Two or more swing lows at approximately the same price, taken to mark a "
       "pool of resting stop orders below them.",
       aliases=("EQL", "equal lows", "double bottom liquidity", "relative equal low"),
       related=("Liquidity Sweep", "Equal High"),
       operational=(
           "Mirror of Equal High on confirmed swing lows, same tolerance and "
           "same availability rule."),
       ),

    _c("Liquidity Sweep", ConceptCategory.LIQUIDITY, ConceptObjectivity.OBJECTIVE,
       "Price trades beyond a prior high or low and closes back inside it, "
       "taken to indicate stops were triggered without a genuine breakout.",
       aliases=("stop hunt", "liquidity grab", "sweep", "raid", "turtle soup",
                "purge"),
       related=("Equal High", "Equal Low", "Inducement"),
       operational=(
           "For a confirmed swing high at price P: bar i sweeps when high[i] > P "
           "and close[i] < P. Mirror for lows. The reference level must be "
           "confirmed at or before bar i-1. formed_at = available_at = close of "
           "bar i."),
       notes="Objective *given* a point-in-time swing reference. Against an "
             "unconfirmed pivot it silently inherits that pivot's lookahead."),

    # ---- PARTIALLY OBJECTIVE -------------------------------------------
    _c("Order Block", ConceptCategory.ZONE, ConceptObjectivity.PARTIALLY_OBJECTIVE,
       "The last opposing candle before a displacement move, treated as a zone "
       "where institutional orders were placed.",
       aliases=("OB", "orderblock", "supply zone", "demand zone"),
       related=("Displacement", "Breaker Block", "Fair Value Gap"),
       operational=(
           "AI Trading System OB:v1 -- the last candle whose direction opposes a "
           "qualifying Displacement (>= 2.0 ATR) beginning within the next 3 "
           "bars. Zone = (open, low) for bullish, (high, open) for bearish. "
           "confirmed_at = the displacement bar; available_at = confirmed_at, "
           "NOT the order-block bar. Blocks are never revised: a later "
           "displacement creates a new block rather than moving an old one."),
       notes="Partially objective because 'last opposing candle' and 'strong "
             "move' are both reader's choices. The no-revision rule is ours and "
             "matters: sources describe blocks being re-drawn as price develops, "
             "which is unbacktestable."),

    _c("Market Structure Shift", ConceptCategory.STRUCTURE,
       ConceptObjectivity.PARTIALLY_OBJECTIVE,
       "A break of the prevailing sequence of highs and lows, taken to mark a "
       "change in the controlling direction.",
       aliases=("MSS", "structure shift", "market structure break"),
       related=("BOS", "Displacement", "Protected High", "Protected Low"),
       operational=(
           "AI Trading System MSS:v1 -- a close beyond the most recent confirmed "
           "swing in the direction opposing the prevailing sequence, where the "
           "prevailing sequence is the last two confirmed swing pairs. "
           "available_at = close of the breaking bar, given the reference swing "
           "was confirmed at or before the previous bar."),
       notes="Registered separately from CHoCH, which some sources define as "
             "the *first* opposing break rather than any of them. If a test "
             "cannot distinguish the two, that is a finding about the "
             "literature and should be reported as one rather than resolved by "
             "quietly merging them."),

    _c("BOS", ConceptCategory.STRUCTURE, ConceptObjectivity.PARTIALLY_OBJECTIVE,
       "A break of structure in the direction of the prevailing sequence, taken "
       "as continuation rather than reversal.",
       aliases=("break of structure", "break in structure"),
       related=("Market Structure Shift", "CHoCH"),
       operational=(
           "AI Trading System BOS:v1 -- as MSS:v1 but with the break in the same "
           "direction as the prevailing sequence. Identical availability rule."),
       notes="BOS and MSS differ only by direction relative to the prevailing "
             "sequence, so they share an implementation and a swing reference."),

    _c("CHoCH", ConceptCategory.STRUCTURE, ConceptObjectivity.PARTIALLY_OBJECTIVE,
       "Change of character: the first structural break opposing the prevailing "
       "sequence.",
       aliases=("change of character",),
       related=("Market Structure Shift", "BOS"),
       operational=(
           "AI Trading System CHOCH:v1 -- the first MSS:v1 event following a "
           "sequence of same-direction BOS events. Availability as MSS:v1."),
       notes="Registered separately from MSS because some sources treat the "
             "'first' qualifier as material. If a test cannot distinguish them, "
             "that is itself a finding."),

    _c("Protected High", ConceptCategory.STRUCTURE,
       ConceptObjectivity.PARTIALLY_OBJECTIVE,
       "A swing high whose violation would invalidate the current bullish "
       "structural read.",
       aliases=("PH", "protected swing high", "strong high"),
       related=("Protected Low", "Market Structure Shift"),
       operational=(
           "AI Trading System PROTECTED_HIGH:v1 -- the confirmed swing high "
           "immediately preceding the most recent bullish BOS. Reassigned only "
           "on a subsequent BOS, never on intra-swing price action. "
           "available_at = the BOS bar's close."),
       notes="'Protected' is a claim about intent. The operational proxy is "
             "structural position only, and should not be read as capturing the "
             "concept's meaning."),

    _c("Protected Low", ConceptCategory.STRUCTURE,
       ConceptObjectivity.PARTIALLY_OBJECTIVE,
       "A swing low whose violation would invalidate the current bearish "
       "structural read.",
       aliases=("PL", "protected swing low", "strong low"),
       related=("Protected High",),
       operational="Mirror of PROTECTED_HIGH:v1 on bearish BOS events.",
       ),

    _c("Premium", ConceptCategory.ZONE, ConceptObjectivity.PARTIALLY_OBJECTIVE,
       "The upper portion of a reference range, considered expensive and "
       "favouring sells.",
       aliases=("premium zone", "premium array"),
       related=("Discount", "Equilibrium"),
       operational=(
           "AI Trading System PREMIUM:v1 -- price above the 50% level of the "
           "range defined by the last two confirmed swings (one high, one low). "
           "available_at = confirmation bar of the later of the two swings. The "
           "range never extends to later extremes."),
       notes="Partially objective because the reference range is unspecified in "
             "the sources -- dealing range, session range and swing range all "
             "appear. Ours is the confirmed-swing range, stated explicitly."),

    _c("Discount", ConceptCategory.ZONE, ConceptObjectivity.PARTIALLY_OBJECTIVE,
       "The lower portion of a reference range, considered cheap and favouring "
       "buys.",
       aliases=("discount zone", "discount array"),
       related=("Premium", "Equilibrium"),
       operational="Mirror of PREMIUM:v1: price below the 50% level.",
       ),

    _c("Equilibrium", ConceptCategory.ZONE, ConceptObjectivity.PARTIALLY_OBJECTIVE,
       "The midpoint of a reference range, separating premium from discount.",
       aliases=("EQ", "50% level", "mean threshold"),
       related=("Premium", "Discount"),
       operational="The 50% level of the PREMIUM:v1 reference range.",
       ),

    _c("Breaker Block", ConceptCategory.ZONE,
       ConceptObjectivity.PARTIALLY_OBJECTIVE,
       "A failed order block that price traded through, subsequently treated as "
       "a zone of the opposite polarity.",
       aliases=("breaker", "failed order block"),
       related=("Order Block", "Market Structure Shift"),
       operational=(
           "AI Trading System BREAKER:v1 -- an OB:v1 zone fully traded through "
           "by a close beyond its far edge, re-polarised at that bar. "
           "available_at = the violating bar's close. The original block retains "
           "its own history rather than being mutated."),
       notes="Depends on OB:v1, so it inherits that definition's arbitrariness "
             "and cannot be more objective than its input."),

    _c("Killzone", ConceptCategory.TIME, ConceptObjectivity.PARTIALLY_OBJECTIVE,
       "A time window during which directional moves are said to be more likely.",
       aliases=("kill zone", "killzones", "session window", "macro window"),
       related=("Displacement",),
       operational=(
           "AI Trading System KILLZONE:v1 -- named UTC windows resolved through "
           "the existing DST-aware session calendar. Membership is a property of "
           "the bar's timestamp, so available_at = the bar's own close."),
       notes="The windows themselves are objective; the claim that they matter "
             "is the hypothesis. Times vary between sources, so ours are pinned "
             "in the session calendar and versioned with it."),

    # ---- SUBJECTIVE ----------------------------------------------------
    _c("SMT Divergence", ConceptCategory.CORRELATION, ConceptObjectivity.SUBJECTIVE,
       "Two correlated instruments disagreeing at a structural extreme -- one "
       "makes a new high or low and the other fails to -- taken as a warning.",
       aliases=("SMT", "smart money divergence", "smt divergence"),
       related=("Liquidity Sweep", "Market Structure Shift"),
       notes="Subjective as stated because it requires choosing the correlated "
             "pair, the lookback, and what counts as 'failing to confirm'. "
             "Formalisable in principle; would need a second instrument's data "
             "and a stated correlation basis, neither of which exists yet."),

    _c("Inducement", ConceptCategory.LIQUIDITY, ConceptObjectivity.SUBJECTIVE,
       "A minor liquidity pool deliberately left visible to attract entries "
       "before the intended move.",
       aliases=("IDM", "inducement liquidity", "bait"),
       related=("Liquidity Sweep", "Order Block"),
       notes="Subjective because it is defined by intent -- whether liquidity "
             "was 'left to attract' entries is unobservable. Distinguishing an "
             "inducement from an ordinary swing after the fact is a judgement "
             "no rule in the sources resolves."),
]


def build_ontology() -> ConceptRegistry:
    registry = ConceptRegistry()
    for concept in CANONICAL_CONCEPTS:
        registry.register(concept)
    return registry


#: Module-level singleton for convenience.
ONTOLOGY = build_ontology()
