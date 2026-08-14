"""Solana event contract and adapter lifecycle (Phase 3 groundwork).

Consumes Pumpi across a process boundary (AD-1). Legacy ``Eth`` field names are
renamed to chain-neutral quote terminology at this boundary and nowhere else.
"""

from .adapters import ADAPTER_REGISTRY, AdapterHealth, AdapterState
from .events import PARSER_VERSION, SolanaTokenEvent, normalize_pumpi_trade

__all__ = [
    "ADAPTER_REGISTRY",
    "PARSER_VERSION",
    "AdapterHealth",
    "AdapterState",
    "SolanaTokenEvent",
    "normalize_pumpi_trade",
]
