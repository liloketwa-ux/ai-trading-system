"""Pumpi event normalization with chain-neutral quote terminology.

Pumpi's wire shape carries ``ethAmount``, ``priceEth``, ``marketCapEth`` and
``virtualEthReserves`` on a **Solana** system, where the quote asset is SOL.
That is a legacy leak from an EVM ancestor. Left alone it silently mislabels
every price and market cap downstream, and a mislabelled quote asset is the kind
of error that survives review because the numbers look plausible.

The rename happens **here and only here**. Raw field names are preserved in
``raw`` for compatibility and debugging, and the reinterpretation is explicit:
``quote_asset`` records what the quote actually is rather than assuming it.
Nothing downstream is permitted to see the ``Eth`` names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..storage.records import Observation, utc

__all__ = ["SolanaTokenEvent", "normalize_pumpi_trade", "PARSER_VERSION"]

PARSER_VERSION = "pumpi-normalizer/1"

#: Pumpi field -> chain-neutral name. The quote asset on Solana is SOL, never ETH.
_RENAMES = {
    "ethAmount": "quote_amount",
    "priceEth": "quote_price",
    "marketCapEth": "market_cap_quote",
    "virtualEthReserves": "virtual_quote_reserves",
    "virtualTokenReserves": "virtual_token_reserves",
    "volumeEth": "volume_quote",
}


@dataclass(frozen=True)
class SolanaTokenEvent:
    """A normalized Solana token event with full provenance."""

    timestamp: datetime
    platform: str
    token_address: str
    chain: str = "solana"
    quote_asset: str = "SOL"
    symbol: str | None = None
    name: str | None = None
    trader_address: str | None = None
    side: str | None = None
    token_amount: float | None = None
    quote_amount: float | None = None
    quote_price: float | None = None
    liquidity: float | None = None
    market_cap_quote: float | None = None
    transaction_hash: str | None = None
    slot: int | None = None
    parser_version: str = PARSER_VERSION
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.token_address:
            raise ValueError("token_address is required")
        if not self.platform:
            raise ValueError("platform is required")
        if self.side is not None and self.side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {self.side!r}")
        object.__setattr__(self, "timestamp", utc(self.timestamp))

    def to_observation(self, ingested_at: datetime, available_at: datetime | None = None) -> Observation:
        """Convert to a stored observation.

        ``available_at`` defaults to the event time: an on-chain trade is public
        the moment its transaction confirms. Indexing lag should be supplied
        explicitly when it is known rather than assumed to be zero.
        """
        return Observation(
            key=self.token_address,
            kind="solana_trade",
            event_time=self.timestamp,
            available_at=available_at if available_at is not None else self.timestamp,
            ingested_at=ingested_at,
            source=f"pumpi:{self.platform}",
            raw_ref=self.transaction_hash,
            value={
                "side": self.side,
                "token_amount": self.token_amount,
                "quote_amount": self.quote_amount,
                "quote_price": self.quote_price,
                "quote_asset": self.quote_asset,
                "market_cap_quote": self.market_cap_quote,
                "liquidity": self.liquidity,
                "trader_address": self.trader_address,
                "slot": self.slot,
                "parser_version": self.parser_version,
            },
        )


def _number(value: Any) -> float | None:
    """Pumpi serializes numerics as strings; convert without inventing zeros."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_pumpi_trade(
    payload: dict[str, Any],
    *,
    quote_asset: str = "SOL",
    default_timestamp: datetime | None = None,
) -> SolanaTokenEvent:
    """Normalize one Pumpi ``TradeEvent`` payload.

    Accepts either the full SSE envelope (``{"type": "trade", "trade": {...},
    "token": {...}}``) or a bare trade object.

    Raises:
        ValueError: if required identity fields are absent. A trade without a
            token address or transaction hash cannot be given provenance, and
            guessing one would defeat the point of provenance.
    """
    trade = payload.get("trade", payload)
    token = payload.get("token", {})

    token_address = trade.get("tokenAddress") or token.get("address")
    if not token_address:
        raise ValueError("pumpi payload has no token address")

    raw_timestamp = trade.get("timestamp")
    if raw_timestamp:
        timestamp = (
            datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00"))
            if isinstance(raw_timestamp, str)
            else datetime.fromtimestamp(float(raw_timestamp), tz=timezone.utc)
        )
    elif default_timestamp is not None:
        timestamp = default_timestamp
    else:
        raise ValueError("pumpi payload has no timestamp and no default supplied")

    is_buy = trade.get("isBuy")
    raw_fields = {k: v for k, v in trade.items() if k in _RENAMES}
    raw_fields.update({k: v for k, v in token.items() if k in _RENAMES})

    return SolanaTokenEvent(
        timestamp=timestamp,
        platform=trade.get("platform") or token.get("platform") or "unknown",
        token_address=token_address,
        chain=token.get("chain") or "solana",
        quote_asset=quote_asset,
        symbol=token.get("symbol"),
        name=token.get("name"),
        trader_address=trade.get("traderAddress"),
        side=None if is_buy is None else ("buy" if is_buy else "sell"),
        token_amount=_number(trade.get("tokenAmount")),
        # The rename: Pumpi's "eth" amount is the SOL quote amount.
        quote_amount=_number(trade.get("ethAmount")),
        quote_price=_number(trade.get("priceEth") or token.get("priceEth")),
        market_cap_quote=_number(token.get("marketCapEth")),
        transaction_hash=trade.get("txHash"),
        slot=trade.get("slot"),
        raw=raw_fields,
    )
