"""Performance monitoring and data-drift detection."""

from __future__ import annotations

from typing import Any


class Monitor:
    """Tracks live P&L, drawdown, and model health; flags data drift.

    Placeholder: implementations will surface dashboards/alerts and trigger
    retraining when live behavior diverges from backtest expectations.
    """

    def record(self, event: dict[str, Any]) -> None:
        """Record a runtime event (trade, metric, or alert)."""
        raise NotImplementedError
