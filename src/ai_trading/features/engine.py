"""Feature computation: assembles indicators and sentiment into a feature frame."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import indicators as ind
from . import sentiment_features as sf

OHLCV_REQUIRED = ("open", "high", "low", "close")


@dataclass(frozen=True)
class FeatureConfig:
    """Windows used when building the feature set (all expressed in bars)."""

    sma_windows: tuple[int, ...] = (20, 50, 200)
    momentum_windows: tuple[int, ...] = (5, 20)
    volatility_window: int = 20
    rsi_window: int = 14
    atr_window: int = 14
    bollinger_window: int = 20
    bollinger_std: float = 2.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    sentiment_half_life: float = 6.0
    hype_window: int = 24
    extra: dict[str, float] = field(default_factory=dict)


class FeatureEngine:
    """Builds a model-ready feature frame from OHLCV bars and scored documents.

    Every feature is causal — the row at time ``t`` uses only information
    available at or before ``t``. Rows near the start contain ``NaN`` where
    lookback windows have not yet filled; call :meth:`build` then drop or
    impute them according to the consuming model's needs.
    """

    def __init__(self, config: FeatureConfig | None = None) -> None:
        self.config = config or FeatureConfig()

    def build(
        self,
        bars: pd.DataFrame,
        docs: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Compute features for ``bars``, optionally enriched with ``docs``.

        Args:
            bars: OHLCV frame with a sorted ``DatetimeIndex`` and at least
                ``open``, ``high``, ``low``, ``close`` columns.
            docs: Optional scored documents with ``timestamp_utc`` and
                ``sentiment_score`` columns (see
                :func:`~ai_trading.features.sentiment_features.aggregate_to_bars`).

        Returns:
            A frame indexed like ``bars`` with one column per feature.
        """
        self._validate(bars)
        cfg = self.config
        close, high, low = bars["close"], bars["high"], bars["low"]
        out: dict[str, pd.Series] = {}

        out["return_1"] = ind.returns(close)
        for w in cfg.sma_windows:
            out[f"sma_{w}"] = ind.sma(close, w)
            # Distance from the average matters more to a model than its level.
            out[f"close_over_sma_{w}"] = close / ind.sma(close, w) - 1.0
        for w in cfg.momentum_windows:
            out[f"momentum_{w}"] = ind.momentum(close, w)

        out[f"volatility_{cfg.volatility_window}"] = ind.volatility(close, cfg.volatility_window)
        out[f"rsi_{cfg.rsi_window}"] = ind.rsi(close, cfg.rsi_window)

        atr_series = ind.atr(high, low, close, cfg.atr_window)
        out[f"atr_{cfg.atr_window}"] = atr_series
        # Normalized ATR is comparable across assets and price levels.
        out["atr_pct"] = atr_series / close

        macd_frame = ind.macd(close, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
        out["macd"] = macd_frame["macd"]
        out["macd_signal"] = macd_frame["signal"]
        out["macd_histogram"] = macd_frame["histogram"]

        bands = ind.bollinger_bands(close, cfg.bollinger_window, cfg.bollinger_std)
        out["bollinger_pct_b"] = bands["pct_b"]
        out["close_zscore"] = ind.zscore(close, cfg.bollinger_window)

        if "volume" in bars.columns:
            out["volume_zscore"] = ind.zscore(bars["volume"], cfg.volatility_window)

        features = pd.DataFrame(out, index=bars.index)

        if docs is not None:
            agg = sf.aggregate_to_bars(docs, bars.index)
            features["sentiment_mean"] = agg["sentiment_mean"]
            features["sentiment_net"] = agg["net_sentiment"]
            features["sentiment_decayed"] = sf.time_decayed(
                agg["sentiment_sum"], cfg.sentiment_half_life
            )
            features["doc_count"] = agg["doc_count"]
            features["hype_score"] = sf.hype_score(agg["doc_count"], cfg.hype_window)

        return features

    @staticmethod
    def _validate(bars: pd.DataFrame) -> None:
        missing = [c for c in OHLCV_REQUIRED if c not in bars.columns]
        if missing:
            raise KeyError(f"bars is missing required column(s): {missing}")
        if not isinstance(bars.index, pd.DatetimeIndex):
            raise TypeError("bars must be indexed by a DatetimeIndex")
        if not bars.index.is_monotonic_increasing:
            raise ValueError("bars index must be sorted ascending")
