"""Phase 4 feature contract: quality semantics and computation availability.

Two ideas beyond the Phase 3 snapshot.

**Data quality is not a value.** A missing volume is not zero volume. A stale
price is not a current price. Collapsing those into a float silently changes
what a feature means -- a strategy reading ``volume == 0`` cannot tell "the
venue reported no trades" from "we never received the bar". So every feature
carries a :class:`DataQuality` alongside its value, and a value of ``None`` with
quality ``MISSING`` is a different fact from ``0.0`` with quality ``OK``.

**Availability can exceed the inputs' availability.** The Phase 3 rule is that a
derived feature is knowable no *earlier* than its last input. It may legitimately
be knowable *later* -- a feature that must wait for a session to close, or for a
batch job to run, has a computational availability of its own. That later
timestamp must be modelled explicitly rather than assumed away, which is what
:class:`AvailabilityRule` records.
"""

from __future__ import annotations

from enum import Enum

# Defined in the storage layer so the dependency points one way: features may
# import storage, never the reverse. Re-exported here as part of the contract.
from ..storage.quality import AvailabilityRule, DataQuality

__all__ = ["DataQuality", "AvailabilityRule", "FeatureStatus", "Domain", "QualityError"]


class QualityError(RuntimeError):
    """A feature was used in a way its data quality forbids."""


class FeatureStatus(str, Enum):
    """Lifecycle of a feature definition."""

    IMPLEMENTED = "implemented"
    RESERVED = "reserved"        # interface defined, calculation deferred
    UNAVAILABLE = "unavailable"  # requires source data we do not have
    DEPRECATED = "deprecated"


class Domain(str, Enum):
    PRICE = "price"
    VOLUME = "volume"
    VOLATILITY = "volatility"
    MARKET_STRUCTURE = "market_structure"
    SESSION = "session"
    LIQUIDITY = "liquidity"
    DERIVATIVES = "derivatives"
    MACRO = "macro"
    MICROSTRUCTURE = "microstructure"
    SENTIMENT = "sentiment"    # reserved
    ON_CHAIN = "on_chain"      # reserved
