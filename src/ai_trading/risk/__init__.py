"""Risk management: sizing, stops, leverage, drawdown, and VaR limits.

See design-doc section 7.
"""

from .manager import RiskManager

__all__ = ["RiskManager"]
