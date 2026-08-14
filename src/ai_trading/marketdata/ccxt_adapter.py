"""CCXT-backed market data provider.

Depends on the ``ccxt`` PyPI package (audit §3). The TypeScript CCXT repository
is neither vendored nor ported.

**On WebSockets.** Since CCXT v4, ``ccxt.pro`` ships inside the main package —
verified: ``ccxt.pro.binanceusdm`` exposes 59 ``watch*`` methods. There is no
separate dependency to isolate. Streaming still sits behind
:class:`MarketDataProvider` so the rest of the system never sees a ``watch*``
type; this module exposes only the pull interface, and a streaming
implementation would be a sibling class satisfying the same contract.

**On bar availability.** A candle's ``available_at`` is its close, computed as
open + timeframe. Treating the open as availability leaks the whole bar.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .provider import Capability, CapabilityError, MarketDataError, MarketDataProvider
from .types import OHLCV, FundingRate, OpenInterest, OrderBook, Provenance, Ticker, Trade

__all__ = ["CCXTMarketData", "parse_timeframe"]

_TIMEFRAME_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_timeframe(timeframe: str) -> timedelta:
    """Parse a CCXT timeframe string such as ``"15m"`` into a duration."""
    if not timeframe or len(timeframe) < 2:
        raise ValueError(f"malformed timeframe: {timeframe!r}")
    unit = timeframe[-1].lower()
    if unit not in _TIMEFRAME_UNITS:
        raise ValueError(f"unknown timeframe unit {unit!r} in {timeframe!r}")
    try:
        amount = int(timeframe[:-1])
    except ValueError as exc:
        raise ValueError(f"malformed timeframe: {timeframe!r}") from exc
    if amount <= 0:
        raise ValueError(f"timeframe amount must be > 0, got {timeframe!r}")
    return timedelta(seconds=amount * _TIMEFRAME_UNITS[unit])


def _ms_to_dt(milliseconds: float | None) -> datetime | None:
    if milliseconds is None:
        return None
    return datetime.fromtimestamp(milliseconds / 1000.0, tz=timezone.utc)


class CCXTMarketData(MarketDataProvider):
    """Normalizes a CCXT exchange into the provider contract.

    Args:
        exchange: A constructed ccxt exchange instance.
        clock: Callable returning "now", injectable for deterministic tests.

    Every fetch checks :meth:`capability` first, so an unsupported call raises
    :class:`CapabilityError` at the boundary instead of a venue-specific
    exception from inside the library.
    """

    def __init__(self, exchange, clock=None) -> None:
        if exchange is None:
            raise ValueError("exchange must not be None")
        self.exchange = exchange
        self.name = f"ccxt:{getattr(exchange, 'id', 'unknown')}"
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @classmethod
    def create(cls, exchange_id: str, config: dict | None = None, clock=None) -> "CCXTMarketData":
        """Build from an exchange id such as ``"binanceusdm"``.

        Credentials come from the caller's config (sourced from environment or a
        secret manager); none are read or defaulted here.
        """
        import ccxt

        if not hasattr(ccxt, exchange_id):
            raise ValueError(f"unknown ccxt exchange id: {exchange_id!r}")
        return cls(getattr(ccxt, exchange_id)(config or {}), clock=clock)

    # -- capability --------------------------------------------------------

    def capability(self, method: str) -> Capability:
        has = getattr(self.exchange, "has", None) or {}
        return Capability.from_ccxt(has.get(method))

    # -- provenance --------------------------------------------------------

    def _provenance(
        self, event_time: datetime, available_at: datetime | None = None, *, emulated: bool = False
    ) -> Provenance:
        now = self._clock()
        return Provenance(
            source=self.name,
            event_time=event_time,
            retrieved_at=now,
            available_at=available_at or event_time,
            emulated=emulated,
        )

    # -- market data -------------------------------------------------------

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[OHLCV]:
        capability = self.require("fetchOHLCV")
        duration = parse_timeframe(timeframe)
        since_ms = int(since.timestamp() * 1000) if since else None

        raw = self._call(
            "fetchOHLCV", self.exchange.fetch_ohlcv, symbol, timeframe, since_ms, limit
        )

        bars: list[OHLCV] = []
        for row in raw or []:
            if row is None or len(row) < 6:
                continue
            opened = _ms_to_dt(row[0])
            if opened is None:
                continue
            bars.append(
                OHLCV(
                    timestamp=opened,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    # The bar is usable only once it has closed.
                    provenance=self._provenance(
                        opened, opened + duration, emulated=capability is Capability.EMULATED
                    ),
                )
            )
        bars.sort(key=lambda b: b.timestamp)
        return bars

    def fetch_ticker(self, symbol: str) -> Ticker:
        capability = self.require("fetchTicker")
        raw = self._call("fetchTicker", self.exchange.fetch_ticker, symbol) or {}
        event_time = _ms_to_dt(raw.get("timestamp")) or self._clock()
        last = raw.get("last") or raw.get("close")
        if last is None:
            raise MarketDataError(f"{self.name}: ticker for {symbol} carries no last price")
        return Ticker(
            symbol=raw.get("symbol") or symbol,
            last=float(last),
            bid=float(raw["bid"]) if raw.get("bid") is not None else None,
            ask=float(raw["ask"]) if raw.get("ask") is not None else None,
            provenance=self._provenance(event_time, emulated=capability is Capability.EMULATED),
        )

    def fetch_order_book(self, symbol: str, limit: int | None = None) -> OrderBook:
        capability = self.require("fetchOrderBook")
        raw = self._call("fetchOrderBook", self.exchange.fetch_order_book, symbol, limit) or {}
        event_time = _ms_to_dt(raw.get("timestamp")) or self._clock()
        return OrderBook(
            symbol=raw.get("symbol") or symbol,
            bids=[(float(p), float(s)) for p, s, *_ in (raw.get("bids") or [])],
            asks=[(float(p), float(s)) for p, s, *_ in (raw.get("asks") or [])],
            provenance=self._provenance(event_time, emulated=capability is Capability.EMULATED),
        )

    def fetch_trades(
        self, symbol: str, since: datetime | None = None, limit: int | None = None
    ) -> list[Trade]:
        capability = self.require("fetchTrades")
        since_ms = int(since.timestamp() * 1000) if since else None
        raw = self._call("fetchTrades", self.exchange.fetch_trades, symbol, since_ms, limit)

        trades: list[Trade] = []
        for row in raw or []:
            event_time = _ms_to_dt(row.get("timestamp")) or self._clock()
            trades.append(
                Trade(
                    symbol=row.get("symbol") or symbol,
                    timestamp=event_time,
                    price=float(row["price"]),
                    amount=float(row["amount"]),
                    side=row.get("side") or "unknown",
                    trade_id=row.get("id"),
                    provenance=self._provenance(
                        event_time, emulated=capability is Capability.EMULATED
                    ),
                )
            )
        trades.sort(key=lambda t: t.timestamp)
        return trades

    def fetch_funding_rate(self, symbol: str) -> FundingRate:
        capability = self.require("fetchFundingRate")
        raw = self._call("fetchFundingRate", self.exchange.fetch_funding_rate, symbol) or {}
        event_time = _ms_to_dt(raw.get("timestamp")) or self._clock()
        rate = raw.get("fundingRate")
        if rate is None:
            raise MarketDataError(f"{self.name}: funding rate for {symbol} is absent")
        return FundingRate(
            symbol=raw.get("symbol") or symbol,
            rate=float(rate),
            funding_time=_ms_to_dt(raw.get("fundingTimestamp")) or event_time,
            provenance=self._provenance(event_time, emulated=capability is Capability.EMULATED),
        )

    def fetch_open_interest(self, symbol: str) -> OpenInterest:
        capability = self.require("fetchOpenInterest")
        raw = self._call("fetchOpenInterest", self.exchange.fetch_open_interest, symbol) or {}
        event_time = _ms_to_dt(raw.get("timestamp")) or self._clock()
        amount = raw.get("openInterestAmount")
        value = raw.get("openInterestValue")
        if amount is None and value is None:
            raise MarketDataError(f"{self.name}: open interest for {symbol} is absent")
        return OpenInterest(
            symbol=raw.get("symbol") or symbol,
            open_interest=float(amount if amount is not None else value),
            open_interest_value=float(value) if value is not None else None,
            provenance=self._provenance(event_time, emulated=capability is Capability.EMULATED),
        )

    # -- internals ---------------------------------------------------------

    def _call(self, method: str, fn, *args):
        """Invoke a ccxt method, wrapping venue errors in our own type.

        Errors are re-raised with context, never swallowed — a silent empty
        result would be indistinguishable from a venue with no data.
        """
        try:
            return fn(*args)
        except CapabilityError:
            raise
        except Exception as exc:  # noqa: BLE001 — re-raised with context below
            raise MarketDataError(f"{self.name}.{method} failed: {exc}") from exc
