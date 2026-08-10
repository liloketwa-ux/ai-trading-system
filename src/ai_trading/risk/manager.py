"""Risk controls applied to signals before execution."""

from __future__ import annotations

from typing import Any


class RiskManager:
    """Enforces position sizing and portfolio-level risk limits.

    Placeholder: implementations will apply fixed-fractional/volatility sizing,
    stop-loss/take-profit, leverage caps, correlation and drawdown limits, and
    VaR/CVaR budgeting.
    """

    def size(self, signal: Any, portfolio: Any) -> float:
        """Return the position size (in units) permitted for a signal."""
        raise NotImplementedError
