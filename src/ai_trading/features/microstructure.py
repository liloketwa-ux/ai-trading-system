"""Order-book / microstructure features.

**Currently unavailable.** These require order-book snapshots or tick data,
which this system does not persist. Every function returns an
``UNAVAILABLE`` snapshot rather than an approximation.

That refusal is the feature. Spread and depth imbalance can be *plausibly*
faked from candles -- high-minus-low as a "spread proxy", volume as a "depth
proxy" -- and the fabrication is undetectable downstream while being wrong in
exactly the conditions that matter, when real spreads widen and real depth
disappears.
"""

from __future__ import annotations

from datetime import datetime

from ..storage.features import FeatureSnapshot
from ..storage.quality import DataQuality
from ..storage.records import utc
from ..storage.store import ObservationStore

__all__ = ["ORDERBOOK_FEATURES", "orderbook_feature", "has_orderbook_data"]

SOURCE = "features/microstructure"

ORDERBOOK_FEATURES = (
    "bid_ask_spread", "mid_price", "top_of_book_imbalance",
    "depth_imbalance", "order_book_depth", "trade_imbalance",
)


def has_orderbook_data(store: ObservationStore, instrument: str) -> bool:
    """Whether any order-book observation exists for this instrument."""
    return any(o.kind == "orderbook" and o.key == instrument for o in store._all())


def orderbook_feature(
    store: ObservationStore, instrument: str, decision_time: datetime, name: str,
    *, strict: bool = True,
) -> FeatureSnapshot:
    """Compute an order-book feature, or refuse if the data does not exist."""
    if name not in ORDERBOOK_FEATURES:
        raise ValueError(f"unknown microstructure feature {name!r}")

    moment = utc(decision_time)
    book = store.latest(decision_time, instrument, "orderbook", strict=strict)
    if book is None:
        return FeatureSnapshot(
            name=name, value=None, event_time=moment, available_at=moment,
            source=SOURCE, instrument=instrument,
            data_quality=DataQuality.UNAVAILABLE,
        )

    bids = book.value.get("bids") or []
    asks = book.value.get("asks") or []
    if not bids or not asks:
        return FeatureSnapshot(
            name=name, value=None, event_time=book.event_time,
            available_at=book.available_at, source=SOURCE, instrument=instrument,
            data_quality=DataQuality.MISSING,
        )

    best_bid, bid_size = bids[0]
    best_ask, ask_size = asks[0]
    values = {
        "bid_ask_spread": best_ask - best_bid,
        "mid_price": (best_bid + best_ask) / 2.0,
        "top_of_book_imbalance": (bid_size - ask_size) / (bid_size + ask_size)
        if (bid_size + ask_size) else None,
        "depth_imbalance": (
            (sum(s for _, s in bids) - sum(s for _, s in asks))
            / (sum(s for _, s in bids) + sum(s for _, s in asks))
        ) if (sum(s for _, s in bids) + sum(s for _, s in asks)) else None,
        "order_book_depth": sum(s for _, s in bids) + sum(s for _, s in asks),
        "trade_imbalance": None,  # needs tick data, not a book snapshot
    }
    value = values[name]
    return FeatureSnapshot(
        name=name, value=value, event_time=book.event_time,
        available_at=book.available_at, source=SOURCE, instrument=instrument,
        inputs=(book.provenance_id,), derived_from=(book.provenance_id,),
        data_quality=DataQuality.OK if value is not None else DataQuality.UNAVAILABLE,
    )
