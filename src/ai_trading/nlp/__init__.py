"""NLP: sentiment scoring, hype metrics, and text analytics.

Provides domain-specific sentiment models (e.g. FinBERT / RoBERTa fine-tuned on
financial and crypto text) plus hype/engagement features. See design-doc
section 3.
"""

from .sentiment import SentimentModel

__all__ = ["SentimentModel"]
