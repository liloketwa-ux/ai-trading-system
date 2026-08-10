"""Data ingestion: fetch and normalize raw signals from external sources.

Sources include news APIs (Finnhub, Benzinga, Tiingo, FMP), X/Twitter, on-chain
providers (Glassnode, Whale Alert), and market data feeds (via CCXT / broker
APIs). Fetchers normalize provider payloads into the schemas described in the
design doc (see ``docs/ai-trading-system-design.md`` section 2).
"""

from .base import DataSource

__all__ = ["DataSource"]
