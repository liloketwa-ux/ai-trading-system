"""Strategy and signal interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

Side = Literal["long", "short", "flat"]


@dataclass(frozen=True)
class Signal:
    """A trade signal with an explicit, human-readable rationale."""

    symbol: str
    side: Side
    confidence: float  # 0.0 - 1.0
    rationale: str


class Strategy(ABC):
    """Base class for all strategies."""

    name: str = "base"

    @abstractmethod
    def generate(self, features: dict[str, Any]) -> list[Signal]:
        """Return zero or more signals for the given feature vector."""
        raise NotImplementedError
