"""Feature engineering from raw and NLP-processed data.

Turns price/volume, sentiment, hype, and contextual inputs into the numerical
feature sets consumed by strategies (design-doc section 4). All features are
causal: the row at time ``t`` uses only data available at or before ``t``.
"""

from . import derivatives, futures, microstructure
from .contract import AvailabilityRule, DataQuality, Domain, FeatureStatus
from .engine import FeatureConfig, FeatureEngine
from .latency import DEFAULT_LATENCY, LatencyConfidence, LatencyModel, SourceLatency
from .registry import REGISTRY, FeatureRegistry, FeatureSpec
from .sessions import ASIA, CME_EQUITY, LONDON, NEW_YORK, SESSIONS, SessionDefinition
from .timeframes import Timeframe, bar_available_at, bar_close, completed_bars, latest_completed_bar

__all__ = [
    "ASIA", "CME_EQUITY", "DEFAULT_LATENCY", "LONDON", "NEW_YORK", "REGISTRY", "SESSIONS",
    "AvailabilityRule", "DataQuality", "Domain", "FeatureConfig", "FeatureEngine",
    "FeatureRegistry", "FeatureSpec", "FeatureStatus", "LatencyConfidence", "LatencyModel",
    "SessionDefinition", "SourceLatency", "Timeframe", "bar_available_at", "bar_close",
    "completed_bars", "derivatives", "futures", "latest_completed_bar", "microstructure",
]
