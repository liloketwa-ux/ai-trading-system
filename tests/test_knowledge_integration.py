"""ICT knowledge integration: terminology in, market truth out.

The governing behaviours: a case library cannot become a probability, a
future-aware indicator output cannot enter research, and a subjective concept
cannot become a feature.
"""

from datetime import datetime, timezone

import pytest

from ai_trading.knowledge import (
    CANONICAL_CONCEPTS,
    ONTOLOGY,
    OPENMOBIUS_SOURCE,
    OPENMOBIUS_TEMPORAL_AUDIT,
    STANDARD_TEMPLATES,
    CaseIndex,
    CaseProvenance,
    CaseUseError,
    ConceptCategory,
    ConceptError,
    ConceptObjectivity,
    ConceptRegistry,
    HypothesisLineage,
    ICTHypothesisTemplate,
    KnowledgeAuthorityError,
    KnowledgeSource,
    LocalKnowledgeProvider,
    ResearchStatus,
    ReviewStatus,
    TemplateError,
    TemporalClass,
    TemporalImportError,
    TradingCase,
    TradingConcept,
    assert_importable,
    audit_summary,
    build_ontology,
    case_outcome_statistics,
    normalize_alias,
)

UTC = timezone.utc


def a_source(**kw):
    defaults = dict(name="test-source", version="v1")
    return KnowledgeSource(**{**defaults, **kw})


def a_concept(**kw):
    defaults = dict(
        canonical_name="Test Concept", category=ConceptCategory.STRUCTURE,
        definition="a definition", source=a_source(),
        objectivity=ConceptObjectivity.SUBJECTIVE,
    )
    return TradingConcept(**{**defaults, **kw})


def a_case(**kw):
    defaults = dict(
        case_id="c1", source="openmobius", asset="NQ1!", timeframe="5m",
        concepts=("fair_value_gap",), context="ctx", observations="obs",
        analysis_steps=("step 1",), lessons="a lesson",
    )
    return TradingCase(**{**defaults, **kw})


# =========================================================================
# Concept import and provenance
# =========================================================================


def test_a_concept_needs_a_definition():
    with pytest.raises(ConceptError, match="is a label"):
        a_concept(definition="")


def test_a_concept_records_its_source_and_version():
    concept = a_concept()
    assert concept.source.name == "test-source"
    assert concept.to_dict()["source_version"] == "v1"


def test_every_ontology_concept_carries_provenance():
    for concept in ONTOLOGY.all():
        assert concept.source.name == "OpenMobius-skill"
        assert concept.source_version


def test_a_concept_has_nowhere_to_store_a_success_probability():
    """A case library cannot become a win rate by accident."""
    payload = a_concept().to_dict()
    for forbidden in ("win_rate", "hit_rate", "success_rate", "expectancy",
                      "probability", "case_count", "accuracy"):
        assert forbidden not in payload
    for forbidden in ("win_rate", "hit_rate", "probability"):
        assert not hasattr(TradingConcept, forbidden)


def test_imported_concepts_start_without_evidence():
    for concept in ONTOLOGY.all():
        assert not concept.has_evidence
        assert concept.research_status is not ResearchStatus.EVIDENCE_FOUND


# =========================================================================
# Alias normalization
# =========================================================================


@pytest.mark.parametrize(("raw", "expected"), [
    ("Fair Value Gap", "fair_value_gap"),
    ("  FVG  ", "fvg"),
    ("Break-of-Structure", "break_of_structure"),
    ("CHoCH", "choch"),
    ("market   structure  shift", "market_structure_shift"),
])
def test_aliases_normalize_consistently(raw, expected):
    assert normalize_alias(raw) == expected


@pytest.mark.parametrize(("alias", "canonical"), [
    ("FVG", "Fair Value Gap"), ("imbalance", "Fair Value Gap"),
    ("stop hunt", "Liquidity Sweep"), ("liquidity grab", "Liquidity Sweep"),
    ("EQH", "Equal High"), ("MSS", "Market Structure Shift"),
    ("IDM", "Inducement"), ("supply zone", "Order Block"),
])
def test_known_aliases_resolve_to_canonical_concepts(alias, canonical):
    assert ONTOLOGY.require(alias).canonical_name == canonical


def test_alias_collisions_are_refused():
    """Two concepts claiming one alias would make lookup order-dependent."""
    registry = ConceptRegistry()
    registry.register(a_concept(canonical_name="First", aliases=("shared",)))
    with pytest.raises(ConceptError, match="already resolves"):
        registry.register(a_concept(canonical_name="Second", aliases=("shared",)))


def test_registering_the_same_concept_twice_is_refused():
    registry = ConceptRegistry()
    registry.register(a_concept())
    with pytest.raises(ConceptError, match="already registered"):
        registry.register(a_concept())


def test_an_unknown_name_does_not_resolve():
    assert ONTOLOGY.get("no such concept here") is None
    with pytest.raises(ConceptError, match="no concept resolves"):
        ONTOLOGY.require("no such concept here")


# =========================================================================
# Concept classification
# =========================================================================


def test_the_ontology_covers_the_required_terms():
    required = [
        "Liquidity Sweep", "Displacement", "Fair Value Gap", "Order Block",
        "Market Structure Shift", "BOS", "CHoCH", "Equal High", "Equal Low",
        "Protected High", "Protected Low", "Premium", "Discount",
        "Equilibrium", "Killzone", "SMT Divergence", "Inducement",
        "Breaker Block",
    ]
    for name in required:
        assert ONTOLOGY.get(name) is not None, name
    assert len(ONTOLOGY) == len(required)


def test_every_concept_is_classified():
    for concept in ONTOLOGY.all():
        assert concept.objectivity in set(ConceptObjectivity)


def test_only_objective_concepts_may_enter_the_feature_engine():
    for concept in ONTOLOGY.all():
        if concept.objectivity is ConceptObjectivity.OBJECTIVE:
            assert concept.may_enter_feature_engine
        else:
            assert not concept.may_enter_feature_engine


def test_an_objective_concept_must_carry_an_operational_definition():
    """Objective means we can state the computation. If we cannot, it is not."""
    with pytest.raises(ConceptError, match="carries no operational definition"):
        a_concept(objectivity=ConceptObjectivity.OBJECTIVE)


def test_partially_objective_concepts_have_our_own_versioned_definition():
    """Ours and versioned -- either stated outright or by reference to one."""
    for concept in ONTOLOGY.by_objectivity(
            ConceptObjectivity.PARTIALLY_OBJECTIVE):
        assert concept.operational_definition
        assert concept.operational_definition_version == "v1"
        assert ("AI Trading System" in concept.operational_definition
                or ":v1" in concept.operational_definition), \
            f"{concept.canonical_name} does not name a versioned own definition"


def test_subjective_concepts_stay_knowledge_only():
    subjective = ONTOLOGY.by_objectivity(ConceptObjectivity.SUBJECTIVE)
    assert {c.canonical_name for c in subjective} == {"SMT Divergence",
                                                      "Inducement"}
    for concept in subjective:
        assert not concept.operational_definition
        assert concept.objectivity.is_knowledge_only
        with pytest.raises(ConceptError, match="knowledge-only"):
            concept.require_operationalized()


def test_order_block_is_not_treated_as_objective():
    """'Last opposing candle before displacement' leaves choices to the reader."""
    assert ONTOLOGY.require("Order Block").objectivity is \
        ConceptObjectivity.PARTIALLY_OBJECTIVE


def test_fair_value_gap_is_objective():
    assert ONTOLOGY.require("FVG").objectivity is ConceptObjectivity.OBJECTIVE


def test_the_classification_counts_are_reported():
    summary = ONTOLOGY.summary()
    assert summary["objective"] == 5
    assert summary["partially_objective"] == 11
    assert summary["subjective"] == 2
    assert summary["with_evidence"] == 0


# =========================================================================
# Case provenance
# =========================================================================


def test_a_case_is_always_an_educational_example():
    assert a_case().case_is_educational_example


def test_a_case_cannot_be_marked_as_anything_else():
    with pytest.raises(CaseUseError, match="educational example"):
        a_case(case_is_educational_example=False)


def test_a_case_is_never_statistically_representative():
    case = a_case()
    assert not case.is_statistically_representative
    assert not case.may_support_a_probability


def test_case_outcome_statistics_always_refuses():
    """The refusal is discoverable where somebody would reach for the number."""
    with pytest.raises(CaseUseError, match="how often the pattern was taught"):
        case_outcome_statistics([a_case(), a_case(case_id="c2")])


def test_extraction_provenance_bounds_what_a_case_is_worth():
    provenance = CaseProvenance(
        source_project="Education - ICT", source_reference="video:abc123",
        extracted_by_model="qwen/qwen3-vl-plus",
        extraction_confidence="medium", review_status=ReviewStatus.PENDING)
    case = a_case(provenance=provenance)
    assert case.provenance.is_machine_extracted
    assert not case.provenance.is_human_reviewed


def test_the_case_index_reports_no_aggregate_outcomes():
    index = CaseIndex()
    index.add(a_case())
    index.add(a_case(case_id="c2", asset="ES1!"))
    summary = index.summary()
    assert summary["usable_as_sample"] is False
    for forbidden in ("win_rate", "success_rate", "outcomes", "profitable"):
        assert forbidden not in summary


def test_a_case_records_its_source_time_range():
    case = a_case(source_time_range=(datetime(2025, 6, 1, tzinfo=UTC),
                                     datetime(2025, 6, 2, tzinfo=UTC)))
    assert case.to_dict()["source_time_range"][0].startswith("2025-06-01")


# =========================================================================
# Temporal audit
# =========================================================================


def test_every_audited_output_has_a_temporal_class():
    assert len(OPENMOBIUS_TEMPORAL_AUDIT) >= 10
    for finding in OPENMOBIUS_TEMPORAL_AUDIT:
        assert finding.temporal_class in set(TemporalClass)
        assert finding.evidence
        assert finding.remediation


def test_retrospective_outputs_are_barred_from_research():
    for finding in OPENMOBIUS_TEMPORAL_AUDIT:
        if finding.temporal_class is TemporalClass.RETROSPECTIVE:
            assert finding.temporal_class.barred_from_research
            with pytest.raises(TemporalImportError):
                assert_importable(finding)


def test_unknown_outputs_are_barred_with_retrospective_ones():
    """An opaque server-side computation is not innocent until proven guilty."""
    unknown = [f for f in OPENMOBIUS_TEMPORAL_AUDIT
               if f.temporal_class is TemporalClass.UNKNOWN]
    assert unknown, "the audit should have found opaque remote outputs"
    for finding in unknown:
        assert finding.temporal_class.barred_from_research
        with pytest.raises(TemporalImportError):
            assert_importable(finding)


def test_the_swing_pivot_is_retrospective_with_a_recorded_lag():
    finding = next(f for f in OPENMOBIUS_TEMPORAL_AUDIT
                   if f.output == "swing_pivot")
    assert finding.temporal_class is TemporalClass.RETROSPECTIVE
    assert finding.confirmation_lag_bars == 2
    assert "centred fractal" in finding.evidence


def test_delayed_confirmation_is_usable_only_with_an_explicit_delay():
    delayed = [f for f in OPENMOBIUS_TEMPORAL_AUDIT
               if f.temporal_class is TemporalClass.DELAYED_CONFIRMATION]
    assert delayed
    for finding in delayed:
        assert finding.confirmation_lag_bars is not None
        assert finding.temporal_class.usable_with_explicit_delay
        assert not finding.temporal_class.importable_as_is
        with pytest.raises(TemporalImportError):
            assert_importable(finding)


def test_the_order_block_lookahead_is_recorded():
    finding = next(f for f in OPENMOBIUS_TEMPORAL_AUDIT
                   if f.output == "order_block")
    assert finding.confirmation_lag_bars == 3
    assert "lookahead" in finding.evidence


def test_the_forward_scanning_output_has_no_repairing_delay():
    finding = next(f for f in OPENMOBIUS_TEMPORAL_AUDIT
                   if f.output == "fvg_mitigation_pct")
    assert finding.temporal_class is TemporalClass.RETROSPECTIVE
    assert finding.confirmation_lag_bars is None
    assert "not importable" in finding.remediation


def test_at_most_one_output_is_importable_as_is():
    summary = audit_summary()
    assert summary["importable_as_is"] == ["volume_anomaly"]
    assert len(summary["barred_from_research"]) >= 8


def test_no_structural_output_is_importable():
    """The headline finding: nothing structural survives the audit."""
    structural = {"swing_pivot", "order_block", "liquidity_sweep",
                  "bos_choch", "displacement", "premium_discount_equilibrium",
                  "equal_highs_lows", "trailing_extremes"}
    importable = set(audit_summary()["importable_as_is"])
    assert not (structural & importable)


# =========================================================================
# Hypothesis templates and lineage
# =========================================================================


def test_the_standard_family_is_nested():
    counts = [t.condition_count for t in STANDARD_TEMPLATES]
    assert counts == [1, 2, 3, 4, 5]


def test_every_standard_template_instantiates():
    for template in STANDARD_TEMPLATES:
        hypothesis = template.instantiate(ONTOLOGY)
        assert hypothesis.hypothesis_id == template.template_id


def test_a_hypothesis_is_never_a_signal():
    hypothesis = STANDARD_TEMPLATES[0].instantiate(ONTOLOGY)
    assert not hypothesis.is_signal
    for forbidden in ("side", "entry", "stop", "target", "size", "action"):
        assert forbidden not in hypothesis.to_dict()


def test_a_template_on_a_subjective_concept_is_refused():
    template = ICTHypothesisTemplate(
        "ICT-SMT-001", "does SMT divergence predict anything",
        ("SMT Divergence",), ("smt",))
    assert template.blocking_concepts(ONTOLOGY) == ["SMT Divergence"]
    with pytest.raises(TemplateError, match="lack an operational definition"):
        template.instantiate(ONTOLOGY)


def test_instantiation_cannot_be_influenced_by_the_case_library():
    """Concepts -> hypotheses -> data, never cases -> hypotheses."""
    import inspect

    parameters = set(
        inspect.signature(ICTHypothesisTemplate.instantiate).parameters)
    for forbidden in ("case", "cases", "case_ids", "examples", "outcomes"):
        assert forbidden not in parameters


def test_a_changed_hypothesis_gets_a_different_fingerprint():
    base = STANDARD_TEMPLATES[0].instantiate(ONTOLOGY)
    changed = ICTHypothesisTemplate(
        "ICT-LS-001", STANDARD_TEMPLATES[0].statement,
        ("Liquidity Sweep",), ("liquidity_sweep",), horizon_bars=24,
    ).instantiate(ONTOLOGY)
    assert base.fingerprint != changed.fingerprint


def test_the_fingerprint_is_stable_for_the_same_definition():
    assert (STANDARD_TEMPLATES[0].instantiate(ONTOLOGY).fingerprint
            == STANDARD_TEMPLATES[0].instantiate(ONTOLOGY).fingerprint)


def test_lineage_records_the_whole_chain():
    hypothesis = STANDARD_TEMPLATES[2].instantiate(
        ONTOLOGY, dataset_id="nq-nqm26-real_market-abc123")
    lineage = hypothesis.lineage
    assert lineage.is_traceable
    assert lineage.knowledge_source == "OpenMobius-skill"
    assert set(lineage.concept_ids) == {"liquidity_sweep", "displacement",
                                        "fair_value_gap"}
    assert lineage.feature_ids == ("liquidity_sweep", "displacement_atr", "fvg")
    assert lineage.dataset_id == "nq-nqm26-real_market-abc123"


def test_lineage_describes_itself_in_one_sentence():
    description = STANDARD_TEMPLATES[0].instantiate(ONTOLOGY).lineage.describe()
    assert "originated from concepts" in description
    assert "not yet tested against any dataset" in description


def test_lineage_refuses_unversioned_features():
    with pytest.raises(TemplateError, match="unversioned feature"):
        HypothesisLineage("src", "v1", ("a",), ("f1", "f2"), ("v1",), "v1")


def test_lineage_requires_at_least_one_concept():
    with pytest.raises(TemplateError, match="must name the concepts"):
        HypothesisLineage("src", "v1", (), ("f1",), ("v1",), "v1")


def test_hypotheses_default_to_the_frozen_protocol():
    lineage = STANDARD_TEMPLATES[0].instantiate(ONTOLOGY).lineage
    assert lineage.protocol_version == "research-protocol-v1"


# =========================================================================
# Retrieval
# =========================================================================


def provider():
    index = CaseIndex()
    index.add(a_case(case_id="case-nq-1", asset="NQ1!"))
    index.add(a_case(case_id="case-es-1", asset="ES1!",
                     concepts=("order_block",)))
    return LocalKnowledgeProvider(ONTOLOGY, index)


def test_concept_search_is_deterministic():
    """A hypothesis family that changes between runs cannot be pre-registered."""
    first = [h.identifier for h in provider().search_concepts("liquidity sweep")]
    for _ in range(5):
        assert [h.identifier for h in
                provider().search_concepts("liquidity sweep")] == first


def test_case_search_is_deterministic():
    first = [h.identifier for h in provider().search_cases("NQ1!")]
    for _ in range(5):
        assert [h.identifier for h in provider().search_cases("NQ1!")] == first


def test_search_finds_the_expected_concept():
    hits = provider().search_concepts("fair value gap")
    assert hits[0].identifier == "fair_value_gap"


def test_search_matches_on_an_alias():
    hits = provider().search_concepts("stop hunt")
    assert hits[0].identifier == "liquidity_sweep"


def test_related_concepts_resolve():
    related = provider().related_concepts("fair_value_gap")
    assert "Displacement" in {c.canonical_name for c in related}


def test_the_provider_is_advisory_and_says_so():
    assert provider().is_advisory


def test_the_provider_refuses_authority_over_the_subsystems():
    for subject in ("feature calculation", "temporal integrity", "risk limits",
                    "adjudication", "prop-firm rules"):
        with pytest.raises(KnowledgeAuthorityError, match="advisory"):
            provider().require_no_authority_over(subject)


def test_the_provider_exposes_no_decision_methods():
    from ai_trading.knowledge import TradingKnowledgeProvider

    for forbidden in ("evaluate", "score_setup", "suggest_trade", "override",
                      "signal", "decide"):
        assert not hasattr(TradingKnowledgeProvider, forbidden)


# =========================================================================
# Licence and attribution
# =========================================================================


def test_the_source_records_its_licence_state():
    assert "Apache-2.0" in OPENMOBIUS_SOURCE.license
    assert "LICENSE file absent" in OPENMOBIUS_SOURCE.license


def test_redistribution_is_not_claimed():
    assert not OPENMOBIUS_SOURCE.redistribution_permitted
    assert OPENMOBIUS_SOURCE.attribution_required


def test_no_source_content_is_vendored():
    """Definitions are ours; the survey copied names and aliases only."""
    assert "own paraphrase" in OPENMOBIUS_SOURCE.note
    for concept in CANONICAL_CONCEPTS:
        assert concept.definition
        assert "definition_per_source" not in concept.to_dict()


def test_no_embeddings_are_imported():
    for concept in ONTOLOGY.all():
        payload = concept.to_dict()
        assert "_embedding" not in payload
        assert "embedding" not in payload


def test_a_fresh_ontology_matches_the_singleton():
    assert build_ontology().summary() == ONTOLOGY.summary()
