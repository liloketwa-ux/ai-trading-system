"""Advisory knowledge retrieval. Never authoritative.

The provider answers "what does the literature say about X". It has no path to
a feature value, a risk limit, an adjudication, or a firm rule, and that is
enforced by what the interface does not expose rather than by a convention.

Retrieval is deterministic: the same query returns the same ordering every
time. Vector search with a nondeterministic index would make a research result
depend on which neighbour happened to surface, and a hypothesis family that
changes between runs cannot be pre-registered. The default implementation is a
plain lexical rank over canonical names and aliases -- unglamorous, exactly
reproducible, and sufficient for a corpus of eighteen concepts.

No embeddings are imported. The surveyed repository ships 768-dimension vectors
inline in every card; copying them would be redistributing derived content from
a source whose licence file is absent.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .cases import CaseIndex, TradingCase
from .concepts import ConceptRegistry, TradingConcept, normalize_alias

__all__ = [
    "TradingKnowledgeProvider", "LocalKnowledgeProvider", "SearchHit",
    "KnowledgeAuthorityError",
]


class KnowledgeAuthorityError(RuntimeError):
    """Knowledge was asked to decide something it has no authority over."""


@dataclass(frozen=True)
class SearchHit:
    """One result, with the score that produced its position."""

    identifier: str
    title: str
    score: float
    matched_on: str

    def to_dict(self) -> dict:
        return {"identifier": self.identifier, "title": self.title,
                "score": self.score, "matched_on": self.matched_on}


class TradingKnowledgeProvider(ABC):
    """Advisory lookup over concepts and cases.

    The five methods are the whole surface. There is deliberately no
    ``evaluate``, ``score_setup``, ``suggest_trade`` or ``override`` -- the
    provider informs a human or a document, and nothing else.
    """

    @abstractmethod
    def search_concepts(self, query: str, *, limit: int = 10) -> list[SearchHit]: ...

    @abstractmethod
    def get_concept(self, identifier: str) -> TradingConcept | None: ...

    @abstractmethod
    def search_cases(self, query: str, *, limit: int = 10) -> list[SearchHit]: ...

    @abstractmethod
    def get_case(self, identifier: str) -> TradingCase | None: ...

    @abstractmethod
    def related_concepts(self, identifier: str) -> list[TradingConcept]: ...

    @property
    def is_advisory(self) -> bool:
        """Always ``True``. Knowledge never overrides computation."""
        return True

    def require_no_authority_over(self, subject: str) -> None:
        """Refuse, always. Present so the refusal is discoverable in code."""
        raise KnowledgeAuthorityError(
            f"the knowledge provider is advisory and has no authority over "
            f"{subject}. Feature values, temporal integrity, risk limits, "
            "adjudication and prop-firm rules are decided by their own "
            "subsystems against verified inputs, never by what the literature "
            "says should happen."
        )


def _score(query_tokens: set[str], text: str) -> float:
    """Token overlap, normalised by query length. Deterministic."""
    if not query_tokens:
        return 0.0
    tokens = set(normalize_alias(text).split("_")) - {""}
    if not tokens:
        return 0.0
    return len(query_tokens & tokens) / len(query_tokens)


class LocalKnowledgeProvider(TradingKnowledgeProvider):
    """Deterministic lexical retrieval over the registry and case index."""

    def __init__(self, concepts: ConceptRegistry,
                 cases: CaseIndex | None = None) -> None:
        self._concepts = concepts
        self._cases = cases or CaseIndex()

    # -- concepts ---------------------------------------------------------
    def search_concepts(self, query: str, *, limit: int = 10) -> list[SearchHit]:
        tokens = set(normalize_alias(query).split("_")) - {""}
        hits: list[SearchHit] = []
        for concept in self._concepts.all():
            best, matched = 0.0, ""
            for candidate in (concept.canonical_name, *concept.aliases):
                score = _score(tokens, candidate)
                if score > best:
                    best, matched = score, candidate
            definition_score = _score(tokens, concept.definition) * 0.5
            if definition_score > best:
                best, matched = definition_score, "definition"
            if best > 0:
                hits.append(SearchHit(concept.concept_id, concept.canonical_name,
                                      round(best, 6), matched))
        # Sort by score then identifier: ties break the same way every run.
        hits.sort(key=lambda h: (-h.score, h.identifier))
        return hits[:limit]

    def get_concept(self, identifier: str) -> TradingConcept | None:
        return self._concepts.get(identifier)

    def related_concepts(self, identifier: str) -> list[TradingConcept]:
        concept = self._concepts.get(identifier)
        if concept is None:
            return []
        found = [self._concepts.get(name) for name in concept.related_concepts]
        return sorted((c for c in found if c is not None),
                      key=lambda c: c.canonical_name)

    # -- cases ------------------------------------------------------------
    def search_cases(self, query: str, *, limit: int = 10) -> list[SearchHit]:
        tokens = set(normalize_alias(query).split("_")) - {""}
        hits: list[SearchHit] = []
        for case in self._cases.all():
            best, matched = 0.0, ""
            for field_name, text in (("asset", case.asset),
                                     ("concepts", " ".join(case.concepts)),
                                     ("lessons", case.lessons)):
                score = _score(tokens, text)
                if score > best:
                    best, matched = score, field_name
            if best > 0:
                hits.append(SearchHit(case.case_id, case.asset or case.case_id,
                                      round(best, 6), matched))
        hits.sort(key=lambda h: (-h.score, h.identifier))
        return hits[:limit]

    def get_case(self, identifier: str) -> TradingCase | None:
        return self._cases.get(identifier)

    def summary(self) -> dict:
        return {"concepts": self._concepts.summary(),
                "cases": self._cases.summary(), "advisory": True,
                "retrieval": "deterministic lexical rank"}
