"""Tests for the normalized market-data layer (Phase 2).

Everything here runs offline. Capability detection reads CCXT's static
metadata, and the adapter is exercised against a fake exchange, so no test
touches a venue.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from ai_trading.marketdata import (
    OHLCV,
    CCXTMarketData,
    Capability,
    CapabilityError,
    MarketDataError,
    Provenance,
    QualityGateError,
    Severity,
    bars_to_frame,
    check_quality,
    parse_timeframe,
)

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def prov(event=T0, available=None, **kw):
    return Provenance(
        source="test", event_time=event, retrieved_at=event,
        available_at=available or event, **kw
    )


def bar(ts, o=100.0, h=101.0, low=99.0, c=100.5, v=1000.0, available=None):
    return OHLCV(ts, o, h, low, c, v, prov(ts, available))


# -- provenance (AD-2) -----------------------------------------------------


def test_provenance_normalizes_naive_datetimes_to_utc():
    p = Provenance("s", datetime(2024, 1, 1), datetime(2024, 1, 1), datetime(2024, 1, 1))
    assert p.event_time.tzinfo is timezone.utc


def test_provenance_rejects_availability_before_the_event():
    """A datum cannot be usable before it exists -- that is look-ahead."""
    with pytest.raises(ValueError, match="precedes event_time"):
        Provenance("s", T0, T0, T0 - timedelta(hours=1))


def test_provenance_latency():
    p = Provenance("s", T0, T0 + timedelta(seconds=5), T0)
    assert p.latency == timedelta(seconds=5)


def test_provenance_requires_a_source():
    with pytest.raises(ValueError, match="source"):
        Provenance("", T0, T0, T0)


# -- OHLCV validation ------------------------------------------------------


def test_ohlcv_rejects_high_below_low():
    with pytest.raises(ValueError, match="below low"):
        OHLCV(T0, 100, 90, 95, 92, 1, prov())


def test_ohlcv_rejects_close_outside_the_range():
    with pytest.raises(ValueError, match="close"):
        OHLCV(T0, 100, 101, 99, 105, 1, prov())


def test_ohlcv_rejects_negative_volume():
    with pytest.raises(ValueError, match="volume"):
        OHLCV(T0, 100, 101, 99, 100, -1, prov())


def test_bars_to_frame_sorts_and_carries_availability():
    bars = [bar(T0 + timedelta(hours=2)), bar(T0), bar(T0 + timedelta(hours=1))]
    frame = bars_to_frame(bars)
    assert frame.index.is_monotonic_increasing
    assert "available_at" in frame.columns
    assert list(frame.columns[:5]) == ["open", "high", "low", "close", "volume"]


def test_bars_to_frame_handles_no_bars():
    assert bars_to_frame([]).empty


# -- capability (AD-3) -----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, Capability.SUPPORTED),
        (False, Capability.UNSUPPORTED),
        ("emulated", Capability.EMULATED),
        ("EMULATED", Capability.EMULATED),
        (None, Capability.UNKNOWN),
        ("something-else", Capability.UNKNOWN),
    ],
)
def test_capability_maps_every_ccxt_value(raw, expected):
    assert Capability.from_ccxt(raw) is expected


def test_emulated_is_usable_but_not_native():
    """The distinction that a truthiness check destroys."""
    assert Capability.EMULATED.usable
    assert not Capability.EMULATED.is_native
    assert Capability.SUPPORTED.is_native


def test_unknown_is_not_usable():
    assert not Capability.UNKNOWN.usable
    assert not Capability.UNSUPPORTED.usable


# -- fake exchange ---------------------------------------------------------


class FakeExchange:
    """Minimal ccxt-shaped double. No network."""

    id = "fake"

    def __init__(self, has=None, ohlcv=None, fail=None):
        self.has = has if has is not None else {
            "fetchOHLCV": True, "fetchTicker": True, "fetchOrderBook": True,
            "fetchTrades": True, "fetchFundingRate": True, "fetchOpenInterest": True,
        }
        self._ohlcv = ohlcv or []
        self._fail = fail or set()

    def _guard(self, name):
        if name in self._fail:
            raise RuntimeError(f"venue exploded in {name}")

    def fetch_ohlcv(self, symbol, timeframe, since, limit):
        self._guard("fetch_ohlcv")
        return self._ohlcv

    def fetch_ticker(self, symbol):
        self._guard("fetch_ticker")
        return {"symbol": symbol, "last": 100.0, "bid": 99.5, "ask": 100.5,
                "timestamp": int(T0.timestamp() * 1000)}

    def fetch_order_book(self, symbol, limit):
        self._guard("fetch_order_book")
        return {"symbol": symbol, "bids": [[99.5, 2.0], [99.0, 3.0]],
                "asks": [[100.5, 1.0]], "timestamp": int(T0.timestamp() * 1000)}

    def fetch_trades(self, symbol, since, limit):
        self._guard("fetch_trades")
        return [{"symbol": symbol, "price": 100.0, "amount": 1.0, "side": "buy",
                 "id": "t1", "timestamp": int(T0.timestamp() * 1000)}]

    def fetch_funding_rate(self, symbol):
        self._guard("fetch_funding_rate")
        return {"symbol": symbol, "fundingRate": 0.0001,
                "timestamp": int(T0.timestamp() * 1000)}

    def fetch_open_interest(self, symbol):
        self._guard("fetch_open_interest")
        return {"symbol": symbol, "openInterestAmount": 1234.0,
                "openInterestValue": 123400.0, "timestamp": int(T0.timestamp() * 1000)}


def make_provider(**kw):
    return CCXTMarketData(FakeExchange(**kw), clock=lambda: T0 + timedelta(seconds=1))


# -- adapter ---------------------------------------------------------------


def test_timeframe_parsing():
    assert parse_timeframe("1m") == timedelta(minutes=1)
    assert parse_timeframe("15m") == timedelta(minutes=15)
    assert parse_timeframe("4h") == timedelta(hours=4)
    assert parse_timeframe("1d") == timedelta(days=1)


@pytest.mark.parametrize("bad", ["", "m", "0m", "-5m", "10x", "abc"])
def test_timeframe_parsing_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_timeframe(bad)


def test_bar_becomes_available_at_its_close_not_its_open():
    """Treating the open as availability leaks the entire bar."""
    ms = int(T0.timestamp() * 1000)
    provider = make_provider(ohlcv=[[ms, 100.0, 101.0, 99.0, 100.5, 5.0]])
    bars = provider.fetch_ohlcv("BTC/USDT", "1h")

    assert bars[0].timestamp == T0
    assert bars[0].provenance.available_at == T0 + timedelta(hours=1)


def test_unsupported_method_raises_at_the_boundary():
    provider = make_provider(has={"fetchOHLCV": True, "fetchFundingRate": False})
    with pytest.raises(CapabilityError, match="fetchFundingRate"):
        provider.fetch_funding_rate("BTC/USDT")


def test_unknown_capability_also_refuses():
    """A missing key means unknown, which must refuse rather than try."""
    provider = make_provider(has={"fetchOHLCV": True})
    with pytest.raises(CapabilityError, match="fetchOpenInterest"):
        provider.fetch_open_interest("BTC/USDT")


def test_emulated_capability_is_called_and_tagged():
    provider = make_provider(has={"fetchFundingRate": "emulated"})
    rate = provider.fetch_funding_rate("BTC/USDT")
    assert rate.rate == pytest.approx(0.0001)
    assert rate.provenance.emulated is True


def test_native_capability_is_not_tagged_emulated():
    assert make_provider().fetch_funding_rate("BTC/USDT").provenance.emulated is False


def test_venue_errors_are_wrapped_not_swallowed():
    provider = make_provider(fail={"fetch_ticker"})
    with pytest.raises(MarketDataError, match="fetchTicker failed"):
        provider.fetch_ticker("BTC/USDT")


def test_ticker_spread_and_bps():
    ticker = make_provider().fetch_ticker("BTC/USDT")
    assert ticker.spread == pytest.approx(1.0)
    assert ticker.spread_bps == pytest.approx(100.0)


def test_order_book_derived_fields():
    book = make_provider().fetch_order_book("BTC/USDT")
    assert book.best_bid == 99.5
    assert book.best_ask == 100.5
    assert book.mid == pytest.approx(100.0)
    assert book.depth("bid") == pytest.approx(5.0)


def test_trades_and_open_interest_normalize():
    provider = make_provider()
    trades = provider.fetch_trades("BTC/USDT")
    assert trades[0].side == "buy" and trades[0].trade_id == "t1"
    oi = provider.fetch_open_interest("BTC/USDT")
    assert oi.open_interest == pytest.approx(1234.0)


def test_capability_report_covers_every_method():
    report = make_provider().capability_report()
    assert set(report) == {
        "fetchOHLCV", "fetchTicker", "fetchOrderBook",
        "fetchTrades", "fetchFundingRate", "fetchOpenInterest",
    }


def test_ohlcv_rows_are_sorted_and_malformed_rows_skipped():
    ms = int(T0.timestamp() * 1000)
    hour = 3_600_000
    provider = make_provider(ohlcv=[
        [ms + hour, 100, 101, 99, 100.5, 1],
        [ms, 100, 101, 99, 100.5, 1],
        None,
        [ms + 2 * hour],  # too short
    ])
    bars = provider.fetch_ohlcv("BTC/USDT", "1h")
    assert len(bars) == 2
    assert bars[0].timestamp < bars[1].timestamp


def test_create_rejects_unknown_exchange_id():
    with pytest.raises(ValueError, match="unknown ccxt exchange"):
        CCXTMarketData.create("definitely_not_an_exchange")


# -- real ccxt metadata (offline) ------------------------------------------


def test_real_ccxt_capability_detection_is_offline():
    """Verifies against installed ccxt without any network call."""
    ccxt = pytest.importorskip("ccxt")
    provider = CCXTMarketData(ccxt.binanceusdm())

    assert provider.capability("fetchOHLCV") is Capability.SUPPORTED
    assert provider.supports("fetchOrderBook")
    report = provider.capability_report()
    assert all(isinstance(v, Capability) for v in report.values())


def test_real_venues_differ_in_support():
    """The reason capability detection exists at all."""
    ccxt = pytest.importorskip("ccxt")
    kraken = CCXTMarketData(ccxt.kraken())
    # Kraken spot reports no funding rate; asking must refuse, not explode.
    assert not kraken.supports("fetchFundingRate")
    with pytest.raises(CapabilityError):
        kraken.fetch_funding_rate("BTC/USD")


# -- quality gate ----------------------------------------------------------


def frame(n=100, freq="h", start="2024-01-01"):
    index = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    close = pd.Series(100.0 + pd.Series(range(n)) * 0.01).to_numpy()
    return pd.DataFrame(
        {"open": close, "high": close * 1.001, "low": close * 0.999,
         "close": close, "volume": [1000.0] * n},
        index=index,
    )


def test_clean_data_passes():
    report = check_quality(frame(), symbol="X", timeframe="1h")
    assert report.ok
    report.raise_if_failed()


def test_empty_frame_is_critical():
    empty = pd.DataFrame(columns=["open", "high", "low", "close"],
                         index=pd.DatetimeIndex([], tz="UTC"))
    assert not check_quality(empty).ok


def test_missing_columns_reported():
    bad = pd.DataFrame({"close": [1.0]}, index=pd.date_range("2024-01-01", periods=1, tz="UTC"))
    assert check_quality(bad).of("schema")


def test_duplicate_timestamps_are_critical():
    data = frame(10)
    duped = pd.concat([data, data.iloc[[3]]]).sort_index()
    report = check_quality(duped)
    assert report.of("duplicate_timestamps")
    assert not report.ok


def test_out_of_order_index_is_critical():
    report = check_quality(frame(10).iloc[::-1])
    assert report.of("out_of_order")


def test_high_below_low_is_critical():
    data = frame(10)
    data.iloc[5, data.columns.get_loc("high")] = 1.0
    assert not check_quality(data).ok


def test_nonpositive_price_is_critical():
    data = frame(10)
    data.iloc[5] = [0.0, 0.0, 0.0, 0.0, 1.0]
    assert check_quality(data).of("nonpositive_price")


def test_intraweek_gap_is_flagged():
    data = frame(50).drop(frame(50).index[20:25])
    report = check_quality(data, expected_interval=timedelta(hours=1))
    assert report.of("missing_bars")


def test_no_gap_reported_without_an_expected_interval():
    data = frame(50).drop(frame(50).index[20:25])
    assert not check_quality(data).of("missing_bars")


def test_weekend_gap_is_info_when_sessions_are_expected():
    index = pd.DatetimeIndex(
        [pd.Timestamp("2024-01-05 12:00Z"), pd.Timestamp("2024-01-08 12:00Z")]
    )  # Friday -> Monday
    data = pd.DataFrame({"open": [1.0, 1.0], "high": [1.0, 1.0], "low": [1.0, 1.0],
                         "close": [1.0, 1.0], "volume": [1.0, 1.0]}, index=index)
    report = check_quality(data, expected_interval=timedelta(days=1))
    weekend = report.of("weekend_gap")
    assert weekend and weekend[0].severity is Severity.INFO


def test_zero_volume_flagged():
    data = frame(100)
    data.iloc[10:40, data.columns.get_loc("volume")] = 0.0
    assert check_quality(data).of("zero_volume")


def test_price_spike_flagged():
    data = frame(100)
    data.iloc[50, data.columns.get_loc("close")] = 1e6
    data.iloc[50, data.columns.get_loc("high")] = 1e6
    assert check_quality(data, spike_sigma=5.0).of("price_spike")


def test_stale_data_is_critical():
    report = check_quality(
        frame(10), max_staleness=timedelta(hours=1),
        now=datetime(2030, 1, 1, tzinfo=timezone.utc),
    )
    assert report.of("stale_data")
    assert not report.ok


def test_availability_before_open_is_caught():
    """The look-ahead check on the availability column itself."""
    data = frame(10)
    data["available_at"] = data.index - pd.Timedelta(hours=5)
    assert check_quality(data).of("available_before_event")


def test_raise_if_failed_fails_loudly():
    data = frame(10)
    data.iloc[5, data.columns.get_loc("high")] = 1.0
    with pytest.raises(QualityGateError):
        check_quality(data, symbol="X").raise_if_failed()


def test_report_summary_is_readable():
    assert "rows" in check_quality(frame(), symbol="X", timeframe="1h").summary()
