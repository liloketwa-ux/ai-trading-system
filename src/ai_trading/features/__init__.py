"""Feature engineering from raw and NLP-processed data.

Turns price/volume, sentiment, hype, event, and contextual inputs into the
numerical feature sets consumed by strategies (design-doc section 4).
"""

from .engine import FeatureEngine

__all__ = ["FeatureEngine"]
