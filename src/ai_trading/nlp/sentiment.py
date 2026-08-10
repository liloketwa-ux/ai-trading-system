"""Sentiment scoring interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence


class SentimentModel(ABC):
    """Maps text to a sentiment score in the range [-1.0, +1.0]."""

    @abstractmethod
    def score(self, texts: Sequence[str]) -> list[float]:
        """Return a sentiment score per input text."""
        raise NotImplementedError
