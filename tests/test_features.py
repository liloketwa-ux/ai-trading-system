"""Tests for indicators, sentiment aggregation, and the feature engine."""

import numpy as np
import pandas as pd
import pytest

from ai_trading.features import FeatureConfig, FeatureEngine
from ai_trading.features import indicators as ind
from ai_trading.features import sentiment_features as sf


@pytest.fixture
def bars():
    idx = pd.date_range("2024-01-01", periods=120, freq="D")
    # Deterministic but non-trivial series.
    close = pd.Series(100.0 + np.cumsum(np.sin(np.arange(120) / 5.0)), index=idx)
    return pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": pd.Series(np.arange(120, dtype=float) + 1000.0, index=idx),
        },
        index=idx,
    )


# -- indicators ------------------------------------------------------------


def test_sma_matches_manual_mean():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    assert ind.sma(s, 3).iloc[-1] == pytest.approx(4.0)


def test_sma_leading_values_are_nan():
    assert ind.sma(pd.Series([1.0, 2.0, 3.0]), 3).isna().tolist() == [True, True, False]


def test_rsi_saturates_at_100_when_only_gains():
    rising = pd.Series(np.arange(1.0, 40.0))
    assert ind.rsi(rising, 14).iloc[-1] == pytest.approx(100.0)


def test_rsi_saturates_at_0_when_only_losses():
    falling = pd.Series(np.arange(40.0, 1.0, -1.0))
    assert ind.rsi(falling, 14).iloc[-1] == pytest.approx(0.0)


def test_rsi_stays_within_bounds(bars):
    values = ind.rsi(bars["close"], 14).dropna()
    assert values.between(0.0, 100.0).all()


def test_true_range_uses_prev_close_gap():
    high = pd.Series([10.0, 12.0])
    low = pd.Series([9.0, 11.5])
    close = pd.Series([9.5, 12.0])
    # Bar 1: high-low=0.5, |high-prev_close|=2.5, |low-prev_close|=2.0 -> 2.5
    assert ind.true_range(high, low, close).iloc[1] == pytest.approx(2.5)


def test_atr_is_positive(bars):
    assert (ind.atr(bars["high"], bars["low"], bars["close"], 14).dropna() > 0).all()


def test_macd_histogram_is_line_minus_signal(bars):
    frame = ind.macd(bars["close"])
    assert np.allclose(
        (frame["macd"] - frame["signal"]).to_numpy(), frame["histogram"].to_numpy()
    )


def test_macd_rejects_fast_not_less_than_slow():
    with pytest.raises(ValueError, match="must be less than"):
        ind.macd(pd.Series([1.0, 2.0]), fast=26, slow=12)


def test_bollinger_pct_b_is_one_at_upper_band(bars):
    frame = ind.bollinger_bands(bars["close"], 20, 2.0)
    row = frame.dropna().iloc[-1]
    close_at_upper = row["upper"]
    width = row["upper"] - row["lower"]
    assert (close_at_upper - row["lower"]) / width == pytest.approx(1.0)


def test_zscore_of_constant_series_is_nan():
    assert ind.zscore(pd.Series([5.0] * 10), 5).dropna().empty


@pytest.mark.parametrize("fn", [ind.sma, ind.ema, ind.momentum, ind.volatility, ind.zscore])
def test_indicators_reject_invalid_window(fn):
    with pytest.raises(ValueError, match="window"):
        fn(pd.Series([1.0, 2.0, 3.0]), 0)


def test_indicators_are_causal(bars):
    """Truncating the series must not change earlier indicator values."""
    full = ind.rsi(bars["close"], 14)
    truncated = ind.rsi(bars["close"].iloc[:60], 14)
    assert np.allclose(
        full.iloc[:60].dropna().to_numpy(),
        truncated.dropna().to_numpy(),
    )


# -- sentiment aggregation -------------------------------------------------


def test_documents_map_to_their_own_bar_not_an_earlier_one():
    bar_index = pd.date_range("2024-01-01", periods=3, freq="D")
    docs = pd.DataFrame(
        {
            # Exactly on bar 0, and just after bar 0 (must land on bar 1).
            "timestamp_utc": pd.to_datetime(["2024-01-01 00:00", "2024-01-01 06:00"]),
            "sentiment_score": [0.5, 0.9],
        }
    )
    agg = sf.aggregate_to_bars(docs, bar_index)
    assert agg["doc_count"].tolist() == [1.0, 1.0, 0.0]
    assert agg["sentiment_mean"].iloc[0] == pytest.approx(0.5)
    assert agg["sentiment_mean"].iloc[1] == pytest.approx(0.9)


def test_positive_and_negative_counts():
    bar_index = pd.date_range("2024-01-01", periods=1, freq="D")
    docs = pd.DataFrame(
        {
            "timestamp_utc": pd.to_datetime(["2024-01-01"] * 3),
            "sentiment_score": [0.5, -0.5, -0.2],
        }
    )
    agg = sf.aggregate_to_bars(docs, bar_index)
    assert agg["positive_count"].iloc[0] == 1.0
    assert agg["negative_count"].iloc[0] == 2.0
    assert agg["net_sentiment"].iloc[0] == -1.0


def test_empty_docs_yields_zeroed_frame():
    bar_index = pd.date_range("2024-01-01", periods=3, freq="D")
    agg = sf.aggregate_to_bars(pd.DataFrame(), bar_index)
    assert agg["doc_count"].sum() == 0.0
    assert agg["sentiment_mean"].isna().all()


def test_docs_after_last_bar_are_dropped():
    bar_index = pd.date_range("2024-01-01", periods=2, freq="D")
    docs = pd.DataFrame(
        {"timestamp_utc": pd.to_datetime(["2024-06-01"]), "sentiment_score": [1.0]}
    )
    assert sf.aggregate_to_bars(docs, bar_index)["doc_count"].sum() == 0.0


def test_missing_columns_rejected():
    bar_index = pd.date_range("2024-01-01", periods=1, freq="D")
    with pytest.raises(KeyError):
        sf.aggregate_to_bars(pd.DataFrame({"foo": [1]}), bar_index)


def test_hype_score_flags_a_volume_spike():
    counts = pd.Series([1.0] * 24 + [10.0])
    assert sf.hype_score(counts, window=24).iloc[-1] == pytest.approx(10.0)


def test_hype_baseline_excludes_current_bar():
    """A spike must not dampen its own baseline."""
    counts = pd.Series([2.0] * 5 + [20.0])
    assert sf.hype_score(counts, window=5).iloc[-1] == pytest.approx(10.0)


def test_time_decay_weights_recent_values_higher():
    s = pd.Series([1.0, 0.0, 0.0, 0.0])
    decayed = sf.time_decayed(s, half_life=1.0)
    assert decayed.iloc[0] > decayed.iloc[-1]


def test_time_decay_rejects_non_positive_half_life():
    with pytest.raises(ValueError, match="half_life"):
        sf.time_decayed(pd.Series([1.0]), 0.0)


# -- feature engine --------------------------------------------------------


def test_engine_builds_expected_columns(bars):
    features = FeatureEngine().build(bars)
    for col in ["return_1", "sma_20", "rsi_14", "atr_14", "macd", "bollinger_pct_b"]:
        assert col in features.columns
    assert features.index.equals(bars.index)


def test_engine_includes_sentiment_when_docs_supplied(bars):
    docs = pd.DataFrame(
        {
            "timestamp_utc": bars.index[:50],
            "sentiment_score": np.linspace(-1.0, 1.0, 50),
        }
    )
    features = FeatureEngine().build(bars, docs)
    for col in ["sentiment_mean", "sentiment_decayed", "hype_score", "doc_count"]:
        assert col in features.columns


def test_engine_omits_sentiment_without_docs(bars):
    assert "sentiment_mean" not in FeatureEngine().build(bars).columns


def test_engine_respects_custom_config(bars):
    features = FeatureEngine(FeatureConfig(sma_windows=(7,), rsi_window=5)).build(bars)
    assert "sma_7" in features.columns
    assert "rsi_5" in features.columns
    assert "sma_20" not in features.columns


def test_engine_rejects_missing_columns(bars):
    with pytest.raises(KeyError, match="close"):
        FeatureEngine().build(bars.drop(columns=["close"]))


def test_engine_rejects_unsorted_index(bars):
    with pytest.raises(ValueError, match="sorted"):
        FeatureEngine().build(bars.iloc[::-1])


def test_engine_rejects_non_datetime_index(bars):
    with pytest.raises(TypeError, match="DatetimeIndex"):
        FeatureEngine().build(bars.reset_index(drop=True))
