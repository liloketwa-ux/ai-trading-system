"""ICT/SMC knowledge integration: terminology, not market truth.

Concepts describe what traders say. Cases are educational examples. Neither is
evidence, and nothing here produces a trade signal. The layer exists so that a
future research result can name the concepts it came from and the operational
definitions that made them computable.
"""

from .cases import (
    CaseIndex,
    CaseProvenance,
    CaseUseError,
    ReviewStatus,
    TradingCase,
    case_outcome_statistics,
)
from .concepts import (
    ConceptCategory,
    ConceptError,
    ConceptObjectivity,
    ConceptRegistry,
    KnowledgeSource,
    ResearchStatus,
    TradingConcept,
    normalize_alias,
)
from .ontology import CANONICAL_CONCEPTS, ONTOLOGY, OPENMOBIUS_SOURCE, build_ontology
from .provider import (
    KnowledgeAuthorityError,
    LocalKnowledgeProvider,
    SearchHit,
    TradingKnowledgeProvider,
)
from .templates import (
    STANDARD_TEMPLATES,
    HypothesisLineage,
    ICTHypothesisTemplate,
    PreRegisteredHypothesis,
    TemplateError,
    build_standard_templates,
)
from .temporal_audit import (
    OPENMOBIUS_TEMPORAL_AUDIT,
    TemporalClass,
    TemporalFinding,
    TemporalImportError,
    assert_importable,
    audit_summary,
)

__all__ = [
    "CANONICAL_CONCEPTS", "ONTOLOGY", "OPENMOBIUS_SOURCE",
    "OPENMOBIUS_TEMPORAL_AUDIT", "STANDARD_TEMPLATES", "CaseIndex",
    "CaseProvenance", "CaseUseError", "ConceptCategory", "ConceptError",
    "ConceptObjectivity", "ConceptRegistry", "HypothesisLineage",
    "ICTHypothesisTemplate", "KnowledgeAuthorityError", "KnowledgeSource",
    "LocalKnowledgeProvider", "PreRegisteredHypothesis", "ResearchStatus",
    "ReviewStatus", "SearchHit", "TemplateError", "TemporalClass",
    "TemporalFinding", "TemporalImportError", "TradingCase", "TradingConcept",
    "TradingKnowledgeProvider", "assert_importable", "audit_summary",
    "build_ontology", "build_standard_templates", "case_outcome_statistics",
    "normalize_alias",
]
