"""Concept-to-hypothesis templates, with lineage that survives the result.

A hypothesis here is a *question*, never a signal. Nothing in this module
returns a direction, a size, or an entry. The templates exist so that a concept
can become a pre-registered question with its provenance attached, and so that
a result years later can say which concepts it came from, which feature
versions operationalised them, and which dataset tested them.

The ordering constraint is the anti-bias mechanism:

    concept definitions -> pre-registered hypotheses -> real data -> test

not:

    successful cases -> find similar historical trades

:meth:`ICTHypothesisTemplate.instantiate` therefore takes no case identifiers
and offers no way to rank templates by anything observed in the case library.
Case cards can *illustrate* a concept after the fact; they cannot select which
hypotheses get tested.

Conjunctions are counted honestly. ICT-FVG-001 is four conditions, not one, and
each additional condition shrinks the sample while multiplying the ways it can
be tuned. The template records ``condition_count`` so the multiple-testing
budget reflects what was actually searched.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .concepts import ConceptRegistry, TradingConcept

__all__ = [
    "ICTHypothesisTemplate", "HypothesisLineage", "PreRegisteredHypothesis",
    "TemplateError", "STANDARD_TEMPLATES", "build_standard_templates",
]


class TemplateError(RuntimeError):
    """A hypothesis template was built or used incoherently."""


@dataclass(frozen=True)
class HypothesisLineage:
    """Where a hypothesis came from, end to end.

    Kept so a future result can state: this hypothesis originated from concepts
    X/Y/Z in source S, was operationalised as feature versions A/B/C, and was
    tested on dataset D under protocol P.
    """

    knowledge_source: str
    knowledge_source_version: str
    concept_ids: tuple[str, ...]
    feature_ids: tuple[str, ...]
    feature_versions: tuple[str, ...]
    operational_definition_version: str
    dataset_id: str = ""
    protocol_version: str = "research-protocol-v1"

    def __post_init__(self) -> None:
        if len(self.feature_ids) != len(self.feature_versions):
            raise TemplateError(
                f"{len(self.feature_ids)} feature id(s) but "
                f"{len(self.feature_versions)} version(s); an unversioned "
                "feature cannot be traced back to the code that computed it"
            )
        if not self.concept_ids:
            raise TemplateError("a hypothesis must name the concepts it came from")

    @property
    def is_traceable(self) -> bool:
        """Whether every link in the chain is present."""
        return bool(self.knowledge_source and self.concept_ids
                    and self.feature_ids and self.operational_definition_version)

    def describe(self) -> str:
        features = ", ".join(f"{fid}:{ver}" for fid, ver
                             in zip(self.feature_ids, self.feature_versions))
        return (
            f"originated from concepts {', '.join(self.concept_ids)} in "
            f"{self.knowledge_source} ({self.knowledge_source_version}), "
            f"operationalised as {features} under operational definition "
            f"{self.operational_definition_version}"
            + (f", tested on dataset {self.dataset_id}" if self.dataset_id
               else ", not yet tested against any dataset")
        )

    def to_dict(self) -> dict:
        return {
            "knowledge_source": self.knowledge_source,
            "knowledge_source_version": self.knowledge_source_version,
            "concept_ids": list(self.concept_ids),
            "feature_ids": list(self.feature_ids),
            "feature_versions": list(self.feature_versions),
            "operational_definition_version": self.operational_definition_version,
            "dataset_id": self.dataset_id,
            "protocol_version": self.protocol_version,
            "is_traceable": self.is_traceable,
            "description": self.describe(),
        }


@dataclass(frozen=True)
class PreRegisteredHypothesis:
    """A question, registered before the data is looked at."""

    hypothesis_id: str
    statement: str
    concepts: tuple[str, ...]
    features: tuple[str, ...]
    lineage: HypothesisLineage
    label: str
    horizon_bars: int
    condition_count: int
    registered_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc))
    direction: str | None = None

    @property
    def is_signal(self) -> bool:
        """Always ``False``. A hypothesis is a question, not an instruction."""
        return False

    @property
    def fingerprint(self) -> str:
        """Content hash. A changed hypothesis is a different hypothesis."""
        payload = {
            "id": self.hypothesis_id, "statement": self.statement,
            "concepts": sorted(self.concepts), "features": sorted(self.features),
            "label": self.label, "horizon_bars": self.horizon_bars,
            "direction": self.direction,
            "operational_definition_version":
                self.lineage.operational_definition_version,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "hypothesis_id": self.hypothesis_id, "statement": self.statement,
            "concepts": list(self.concepts), "features": list(self.features),
            "label": self.label, "horizon_bars": self.horizon_bars,
            "condition_count": self.condition_count,
            "direction": self.direction, "is_signal": False,
            "fingerprint": self.fingerprint,
            "registered_at": self.registered_at.isoformat(),
            "lineage": self.lineage.to_dict(),
        }


@dataclass(frozen=True)
class ICTHypothesisTemplate:
    """Turns concepts into a pre-registered question.

    Refuses concepts that cannot be computed. A template built on a subjective
    concept would produce a hypothesis nobody can test, and discovering that at
    instantiation is better than discovering it after declaring a campaign.
    """

    template_id: str
    statement: str
    concept_names: tuple[str, ...]
    feature_ids: tuple[str, ...]
    label: str = "forward_return"
    horizon_bars: int = 12
    direction: str | None = None

    def __post_init__(self) -> None:
        if not self.template_id or not self.statement:
            raise TemplateError("a template needs an id and a statement")
        if not self.concept_names:
            raise TemplateError(f"{self.template_id} names no concepts")

    @property
    def condition_count(self) -> int:
        """Conjunction width. Four conditions is four, not one."""
        return len(self.concept_names)

    def resolve(self, registry: ConceptRegistry) -> list[TradingConcept]:
        return [registry.require(name) for name in self.concept_names]

    def blocking_concepts(self, registry: ConceptRegistry) -> list[str]:
        """Concepts with no operational definition, which block instantiation."""
        return [c.canonical_name for c in self.resolve(registry)
                if not c.operational_definition]

    def instantiate(self, registry: ConceptRegistry, *,
                    feature_versions: tuple[str, ...] = (),
                    dataset_id: str = "") -> PreRegisteredHypothesis:
        """Build the hypothesis, refusing anything not operationalised.

        Takes no case identifiers. There is no argument through which the case
        library could influence which hypotheses exist, which is what keeps the
        ordering concepts -> hypotheses -> data intact.
        """
        concepts = self.resolve(registry)
        blocking = self.blocking_concepts(registry)
        if blocking:
            raise TemplateError(
                f"{self.template_id} cannot be instantiated: "
                f"{', '.join(blocking)} lack an operational definition. A "
                "hypothesis built on an unformalised concept is untestable, and "
                "the right time to find that out is now."
            )

        versions = feature_versions or tuple(
            c.operational_definition_version for c in concepts)
        if len(versions) != len(self.feature_ids):
            raise TemplateError(
                f"{self.template_id}: {len(self.feature_ids)} feature(s) but "
                f"{len(versions)} version(s)"
            )

        source = concepts[0].source
        lineage = HypothesisLineage(
            knowledge_source=source.name,
            knowledge_source_version=source.version,
            concept_ids=tuple(c.concept_id for c in concepts),
            feature_ids=self.feature_ids,
            feature_versions=versions,
            operational_definition_version="+".join(
                sorted({c.operational_definition_version for c in concepts})),
            dataset_id=dataset_id,
        )
        return PreRegisteredHypothesis(
            hypothesis_id=self.template_id, statement=self.statement,
            concepts=tuple(c.concept_id for c in concepts),
            features=self.feature_ids, lineage=lineage, label=self.label,
            horizon_bars=self.horizon_bars,
            condition_count=self.condition_count, direction=self.direction,
        )

    def to_dict(self) -> dict:
        return {
            "template_id": self.template_id, "statement": self.statement,
            "concept_names": list(self.concept_names),
            "feature_ids": list(self.feature_ids), "label": self.label,
            "horizon_bars": self.horizon_bars,
            "condition_count": self.condition_count,
            "direction": self.direction,
        }


def build_standard_templates() -> list[ICTHypothesisTemplate]:
    """The nested family, from one condition to five.

    Nested deliberately: each template adds exactly one condition to the
    previous, so a decline in sample size and any change in effect can be
    attributed to the condition that was added rather than to a reshuffled
    definition.
    """
    return [
        ICTHypothesisTemplate(
            "ICT-LS-001",
            "Forward returns following a liquidity sweep of a confirmed swing "
            "level differ from returns following bars with no sweep.",
            ("Liquidity Sweep",), ("liquidity_sweep",)),
        ICTHypothesisTemplate(
            "ICT-DISP-001",
            "Forward returns following a liquidity sweep that is immediately "
            "followed by displacement differ from those following a sweep alone.",
            ("Liquidity Sweep", "Displacement"),
            ("liquidity_sweep", "displacement_atr")),
        ICTHypothesisTemplate(
            "ICT-FVG-001",
            "Forward returns following sweep plus displacement that leaves a "
            "fair value gap differ from those following sweep plus displacement "
            "without one.",
            ("Liquidity Sweep", "Displacement", "Fair Value Gap"),
            ("liquidity_sweep", "displacement_atr", "fvg")),
        ICTHypothesisTemplate(
            "ICT-MSS-001",
            "Forward returns following sweep, displacement and fair value gap "
            "accompanied by a market structure shift differ from the same "
            "conjunction without one.",
            ("Liquidity Sweep", "Displacement", "Fair Value Gap",
             "Market Structure Shift"),
            ("liquidity_sweep", "displacement_atr", "fvg", "mss")),
        ICTHypothesisTemplate(
            "ICT-HTF-001",
            "Forward returns following the full conjunction, filtered to "
            "agreement with a higher-timeframe premium/discount read, differ "
            "from the unfiltered conjunction.",
            ("Liquidity Sweep", "Displacement", "Fair Value Gap",
             "Market Structure Shift", "Premium"),
            ("liquidity_sweep", "displacement_atr", "fvg", "mss",
             "premium_discount_zone")),
    ]


STANDARD_TEMPLATES = build_standard_templates()
