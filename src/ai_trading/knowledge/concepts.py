"""Trading concepts as knowledge, not as market truth.

The distinction this module exists to hold: a concept card describes what a
community of traders *says* about the market. It is not evidence that the
described behaviour occurs, and it carries no probability of success. An
imported concept arrives with a definition and a source, and with its research
status set to untested -- because it is.

Two guards are structural rather than advisory:

* :class:`TradingConcept` has no field for win rate, hit rate, expectancy, or
  case count. There is nowhere to put a success probability, so a case library
  cannot become one by accident.
* :attr:`TradingConcept.objectivity` gates entry to the feature engine. Only
  ``OBJECTIVE`` concepts may be computed directly; ``PARTIALLY_OBJECTIVE`` ones
  require an explicit operational definition of our own; ``SUBJECTIVE`` ones
  stay knowledge-only until somebody formalises them, which may be never.

Aliases matter more than they look. The same idea appears as "MSS", "market
structure shift", "change of character" and "CHoCH" depending on who is
teaching, and three of those are the same thing while the fourth is arguably
not. Normalising aliases to one canonical name is what stops a hypothesis
family silently testing the same idea four times and calling it four trials.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "ConceptObjectivity", "ConceptCategory", "ResearchStatus", "KnowledgeSource",
    "TradingConcept", "ConceptRegistry", "normalize_alias", "ConceptError",
]


class ConceptError(RuntimeError):
    """A concept was defined or used incoherently."""


class ConceptObjectivity(str, Enum):
    """Whether the concept can be computed from price without judgement.

    The classification is about *definability*, not about usefulness or
    validity. A subjective concept may well describe something real; it simply
    cannot be turned into a deterministic function of an OHLCV series without
    somebody first making choices the source material leaves open.
    """

    OBJECTIVE = "objective"
    PARTIALLY_OBJECTIVE = "partially_objective"
    SUBJECTIVE = "subjective"

    @property
    def may_enter_feature_engine(self) -> bool:
        """Only fully objective concepts compute directly."""
        return self is ConceptObjectivity.OBJECTIVE

    @property
    def requires_operational_definition(self) -> bool:
        """Partially objective concepts need our own definition plus tests."""
        return self is ConceptObjectivity.PARTIALLY_OBJECTIVE

    @property
    def is_knowledge_only(self) -> bool:
        return self is ConceptObjectivity.SUBJECTIVE


class ConceptCategory(str, Enum):
    LIQUIDITY = "liquidity"
    STRUCTURE = "structure"
    IMBALANCE = "imbalance"
    ZONE = "zone"
    TIME = "time"
    CORRELATION = "correlation"
    RISK = "risk"
    NARRATIVE = "narrative"


class ResearchStatus(str, Enum):
    """What this system has established about the concept. Not what it claims."""

    UNTESTED = "untested"
    OPERATIONALIZED = "operationalized"      # defined and computable, not yet tested
    UNDER_TEST = "under_test"
    NO_EVIDENCE = "no_evidence"
    EVIDENCE_FOUND = "evidence_found"

    @property
    def has_been_tested(self) -> bool:
        return self in (ResearchStatus.NO_EVIDENCE, ResearchStatus.EVIDENCE_FOUND)


@dataclass(frozen=True)
class KnowledgeSource:
    """Where a concept came from, and under what terms."""

    name: str
    version: str = ""
    url: str = ""
    license: str = ""
    attribution_required: bool = True
    redistribution_permitted: bool = False
    note: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ConceptError("a knowledge source must be named")

    def to_dict(self) -> dict:
        return {
            "name": self.name, "version": self.version, "url": self.url,
            "license": self.license,
            "attribution_required": self.attribution_required,
            "redistribution_permitted": self.redistribution_permitted,
            "note": self.note,
        }


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_alias(text: str) -> str:
    """Fold an alias to a comparison key.

    Case, punctuation, spacing and hyphenation all vary between sources for the
    same term. Everything else is left alone -- no stemming, no synonym
    expansion, because deciding that "sweep" and "raid" are the same term is a
    modelling judgement and belongs in an explicit alias list, not in a string
    function.
    """
    return _NON_ALNUM.sub("_", text.strip().lower()).strip("_")


@dataclass(frozen=True)
class TradingConcept:
    """One concept, with provenance and an honest research status.

    Deliberately absent: win rate, hit rate, expectancy, sample count, and any
    other field that could hold a probability of success. A case library counts
    how often somebody *taught* a pattern, not how often it worked.
    """

    canonical_name: str
    category: ConceptCategory
    definition: str
    source: KnowledgeSource
    objectivity: ConceptObjectivity
    aliases: tuple[str, ...] = ()
    source_version: str = ""
    related_concepts: tuple[str, ...] = ()
    research_notes: str = ""
    research_status: ResearchStatus = ResearchStatus.UNTESTED
    #: Our own definition, where one exists. Never the source's.
    operational_definition: str = ""
    operational_definition_version: str = ""

    def __post_init__(self) -> None:
        if not self.canonical_name:
            raise ConceptError("a concept needs a canonical name")
        if not self.definition:
            raise ConceptError(
                f"{self.canonical_name}: a concept without a definition is a label"
            )
        if (self.objectivity.may_enter_feature_engine
                and not self.operational_definition):
            raise ConceptError(
                f"{self.canonical_name} is OBJECTIVE but carries no operational "
                "definition. Objective means we can state the computation; if we "
                "cannot, it is PARTIALLY_OBJECTIVE at best."
            )

    @property
    def concept_id(self) -> str:
        return normalize_alias(self.canonical_name)

    @property
    def alias_keys(self) -> frozenset[str]:
        """Every string that should resolve to this concept."""
        return frozenset({self.concept_id}
                         | {normalize_alias(a) for a in self.aliases})

    @property
    def may_enter_feature_engine(self) -> bool:
        return self.objectivity.may_enter_feature_engine

    @property
    def has_evidence(self) -> bool:
        """Always ``False`` until a real-data campaign says otherwise."""
        return self.research_status is ResearchStatus.EVIDENCE_FOUND

    def require_operationalized(self) -> str:
        """Return the operational definition, or refuse."""
        if not self.operational_definition:
            raise ConceptError(
                f"{self.canonical_name} has no operational definition "
                f"({self.objectivity.value}). "
                + ("It needs one plus tests before use."
                   if self.objectivity.requires_operational_definition
                   else "It is knowledge-only until formalised.")
            )
        return self.operational_definition

    def to_dict(self) -> dict:
        return {
            "concept_id": self.concept_id,
            "canonical_name": self.canonical_name,
            "aliases": list(self.aliases),
            "category": self.category.value,
            "definition": self.definition,
            "source": self.source.to_dict(),
            "source_version": self.source_version or self.source.version,
            "related_concepts": list(self.related_concepts),
            "research_notes": self.research_notes,
            "objectivity": self.objectivity.value,
            "research_status": self.research_status.value,
            "may_enter_feature_engine": self.may_enter_feature_engine,
            "operational_definition": self.operational_definition,
            "operational_definition_version": self.operational_definition_version,
            "has_evidence": self.has_evidence,
        }


class ConceptRegistry:
    """Concepts by canonical name, with alias resolution.

    Alias collisions are rejected at registration. Two concepts claiming the
    same alias is a modelling error -- either they are the same concept or the
    alias belongs to only one of them -- and resolving it silently would make
    lookups depend on insertion order.
    """

    def __init__(self) -> None:
        self._concepts: dict[str, TradingConcept] = {}
        self._aliases: dict[str, str] = {}

    def register(self, concept: TradingConcept) -> TradingConcept:
        if concept.concept_id in self._concepts:
            raise ConceptError(f"{concept.canonical_name} is already registered")
        for key in concept.alias_keys:
            owner = self._aliases.get(key)
            if owner is not None and owner != concept.concept_id:
                raise ConceptError(
                    f"alias {key!r} already resolves to {owner!r}; two concepts "
                    "claiming one alias makes lookup depend on insertion order"
                )
        self._concepts[concept.concept_id] = concept
        for key in concept.alias_keys:
            self._aliases[key] = concept.concept_id
        return concept

    def get(self, name: str) -> TradingConcept | None:
        """Resolve by canonical name or any alias."""
        concept_id = self._aliases.get(normalize_alias(name))
        return self._concepts.get(concept_id) if concept_id else None

    def require(self, name: str) -> TradingConcept:
        concept = self.get(name)
        if concept is None:
            raise ConceptError(f"no concept resolves {name!r}")
        return concept

    def all(self) -> list[TradingConcept]:
        return sorted(self._concepts.values(), key=lambda c: c.canonical_name)

    def by_objectivity(self, objectivity: ConceptObjectivity) -> list[TradingConcept]:
        return [c for c in self.all() if c.objectivity is objectivity]

    def by_category(self, category: ConceptCategory) -> list[TradingConcept]:
        return [c for c in self.all() if c.category is category]

    def feature_eligible(self) -> list[TradingConcept]:
        return [c for c in self.all() if c.may_enter_feature_engine]

    def summary(self) -> dict:
        return {
            "concepts": len(self._concepts),
            "aliases": len(self._aliases),
            "objective": len(self.by_objectivity(ConceptObjectivity.OBJECTIVE)),
            "partially_objective": len(
                self.by_objectivity(ConceptObjectivity.PARTIALLY_OBJECTIVE)),
            "subjective": len(self.by_objectivity(ConceptObjectivity.SUBJECTIVE)),
            "with_evidence": sum(1 for c in self.all() if c.has_evidence),
        }

    def __len__(self) -> int:
        return len(self._concepts)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.get(name) is not None
