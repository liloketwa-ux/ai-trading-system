"""Trading strategies (ICT, momentum, mean-reversion, spread, sentiment).

Each strategy consumes a feature vector and emits ranked signals with an
explicit rationale (design-doc section 5).
"""

from .base import Signal, Strategy

__all__ = ["Signal", "Strategy"]
