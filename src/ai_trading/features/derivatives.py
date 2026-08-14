"""Derivatives features with native/emulated provenance preserved.

CCXT reports capability as ``True``, ``'emulated'``, ``False`` or ``None``. An
emulated funding rate is *derived by the library* from other endpoints rather
than reported by the venue -- it may be close, it may lag, and it is not the
same measurement. Mixing the two into one column produces a series whose meaning
changes partway through, which is invisible in a chart and fatal in a study.

So provenance travels with the value, and research can demand
``native_only=True`` to exclude emulated observations entirely.
"""

from __future__ import annotations

from datetime import datetime

from ..marketdata.provider import Capability
from ..storage.features import FeatureSnapshot
from ..storage.quality import AvailabilityRule, DataQuality
from ..storage.records import utc
from ..storage.store import ObservationStore

__all__ = ["derivative_feature", "funding_rate", "open_interest", "mark_price",
           "index_price", "basis", "CapabilityProvenance"]

SOURCE = "features/derivatives"


class CapabilityProvenance:
    """Capability recorded alongside a derivatives value."""

    NATIVE = "native"
    EMULATED = "emulated"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"

    @staticmethod
    def from_capability(capability: Capability) -> str:
        return {
            Capability.SUPPORTED: CapabilityProvenance.NATIVE,
            Capability.EMULATED: CapabilityProvenance.EMULATED,
            Capability.UNSUPPORTED: CapabilityProvenance.UNSUPPORTED,
            Capability.UNKNOWN: CapabilityProvenance.UNKNOWN,
        }[capability]


def _missing(name, decision_time, quality, instrument, note=""):
    moment = utc(decision_time)
    return FeatureSnapshot(
        name=name, value=None, event_time=moment, available_at=moment,
        source=SOURCE, instrument=instrument, data_quality=quality,
    )


def derivative_feature(
    store: ObservationStore,
    instrument: str,
    decision_time: datetime,
    *,
    kind: str,
    name: str,
    field: str = "value",
    native_only: bool = False,
    strict: bool = True,
) -> FeatureSnapshot:
    """Read a derivatives observation point-in-time, preserving provenance.

    Args:
        native_only: Exclude observations the venue did not report natively.
            Emulated values are still returned when False, but always tagged.
    """
    observation = store.latest(decision_time, instrument, kind, strict=strict)
    if observation is None:
        return _missing(name, decision_time, DataQuality.MISSING, instrument)

    provenance = observation.value.get("capability", CapabilityProvenance.UNKNOWN)
    if native_only and provenance != CapabilityProvenance.NATIVE:
        return _missing(name, decision_time, DataQuality.UNAVAILABLE, instrument)

    value = observation.value.get(field)
    if value is None:
        return _missing(name, decision_time, DataQuality.MISSING, instrument)

    return FeatureSnapshot(
        name=name,
        value=float(value),
        event_time=observation.event_time,
        available_at=observation.available_at,
        source=f"{SOURCE}:{observation.source}",
        instrument=instrument,
        inputs=(observation.provenance_id,),
        derived_from=(observation.provenance_id,),
        availability_rule=AvailabilityRule.INPUT_MAX,
        data_quality=DataQuality.OK,
    )


def funding_rate(store, instrument, decision_time, **kw) -> FeatureSnapshot:
    return derivative_feature(store, instrument, decision_time,
                              kind="funding", name="funding_rate", field="rate", **kw)


def open_interest(store, instrument, decision_time, **kw) -> FeatureSnapshot:
    return derivative_feature(store, instrument, decision_time,
                              kind="open_interest", name="open_interest",
                              field="open_interest", **kw)


def mark_price(store, instrument, decision_time, **kw) -> FeatureSnapshot:
    return derivative_feature(store, instrument, decision_time,
                              kind="mark", name="mark_price", field="price", **kw)


def index_price(store, instrument, decision_time, **kw) -> FeatureSnapshot:
    return derivative_feature(store, instrument, decision_time,
                              kind="index", name="index_price", field="price", **kw)


def basis(store, instrument, decision_time, **kw) -> FeatureSnapshot:
    """(mark - index) / index, available only once both inputs are."""
    mark = mark_price(store, instrument, decision_time, **kw)
    index = index_price(store, instrument, decision_time, **kw)
    if not (mark.usable and index.usable) or not index.value:
        quality = mark.data_quality if not mark.usable else index.data_quality
        return _missing("basis", decision_time, quality, instrument)

    return FeatureSnapshot(
        name="basis",
        value=(mark.value - index.value) / index.value,
        event_time=max(mark.event_time, index.event_time),
        # Knowable only once BOTH inputs are.
        available_at=max(mark.available_at, index.available_at),
        source=SOURCE,
        instrument=instrument,
        inputs=(mark.provenance_id, index.provenance_id),
        derived_from=(mark.provenance_id, index.provenance_id),
        availability_rule=AvailabilityRule.INPUT_MAX,
    )
