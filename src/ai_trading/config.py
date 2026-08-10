"""Runtime configuration loaded from environment variables.

Values are read from the process environment (populate a local ``.env`` from
``.env.example`` and load it with python-dotenv, or export them directly).
Secrets must never be hard-coded or committed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Top-level settings for the trading system."""

    environment: str = os.getenv("ENVIRONMENT", "development")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # Social / news / on-chain
    x_bearer_token: str | None = os.getenv("X_BEARER_TOKEN")
    finnhub_api_key: str | None = os.getenv("FINNHUB_API_KEY")
    benzinga_api_key: str | None = os.getenv("BENZINGA_API_KEY")
    tiingo_api_key: str | None = os.getenv("TIINGO_API_KEY")
    fmp_api_key: str | None = os.getenv("FMP_API_KEY")
    glassnode_api_key: str | None = os.getenv("GLASSNODE_API_KEY")

    # Brokers / exchanges
    alpaca_api_key: str | None = os.getenv("ALPACA_API_KEY")
    alpaca_api_secret: str | None = os.getenv("ALPACA_API_SECRET")
    binance_api_key: str | None = os.getenv("BINANCE_API_KEY")
    binance_api_secret: str | None = os.getenv("BINANCE_API_SECRET")


def load_settings() -> Settings:
    """Return a populated :class:`Settings` instance."""
    return Settings()
