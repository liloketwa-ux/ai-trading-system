"""Feature computation."""

from __future__ import annotations

from typing import Any


class FeatureEngine:
    """Computes model features from normalized inputs.

    Placeholder: implementations will produce price/volume, sentiment, hype,
    event-flag, and contextual features as described in the design doc.
    """

    def build(self, records: list[dict[str, Any]]) -> dict[str, float]:
        """Return a feature vector for the given records."""
        raise NotImplementedError
