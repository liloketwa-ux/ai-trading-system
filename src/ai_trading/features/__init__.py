"""Feature engineering from raw and NLP-processed data.

Turns price/volume, sentiment, hype, and contextual inputs into the numerical
feature sets consumed by strategies (design-doc section 4). All features are
causal: the row at time ``t`` uses only data available at or before ``t``.
"""

from .engine import FeatureConfig, FeatureEngine

__all__ = ["FeatureConfig", "FeatureEngine"]
