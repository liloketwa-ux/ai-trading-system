"""Technical indicators.

All indicators are **causal**: the value at index ``i`` depends only on data at
indices ``<= i``. This is enforced by using only trailing rolling/EWM windows
(never ``center=True``) and is what keeps backtests free of lookahead bias.

Leading values where a window is not yet full are ``NaN``.
"""

from __future__ import annotations

import pandas as pd


def returns(close: pd.Series, periods: int = 1) -> pd.Series:
    """Simple percentage return over ``periods`` bars."""
    return close.pct_change(periods)


def log_returns(close: pd.Series, periods: int = 1) -> pd.Series:
    """Log return over ``periods`` bars."""
    import numpy as np

    return pd.Series(np.log(close / close.shift(periods)), index=close.index)


def sma(series: pd.Series, window: int) -> pd.Series:
    """Simple moving average."""
    _check_window(window)
    return series.rolling(window).mean()


def ema(series: pd.Series, window: int) -> pd.Series:
    """Exponential moving average (``adjust=False`` recursive form)."""
    _check_window(window)
    return series.ewm(span=window, adjust=False).mean()


def momentum(close: pd.Series, window: int) -> pd.Series:
    """Price change over ``window`` bars, as a fraction."""
    _check_window(window)
    return close.pct_change(window)


def volatility(close: pd.Series, window: int) -> pd.Series:
    """Rolling standard deviation of simple returns (per-bar, not annualized)."""
    _check_window(window)
    return close.pct_change().rolling(window).std(ddof=1)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Wilder's True Range.

    ``max(high - low, |high - prev_close|, |low - prev_close|)``.
    """
    prev_close = close.shift(1)
    ranges = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    """Average True Range using Wilder's smoothing (``alpha = 1/window``)."""
    _check_window(window)
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / window, adjust=False).mean()


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder's smoothing), in the range [0, 100].

    When there are no losses in the window RSI saturates at 100; when there are
    no gains it saturates at 0.
    """
    _check_window(window)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False).mean()

    rs = avg_gain / avg_loss
    out = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss == 0 -> rs is inf/NaN; RSI is 100 when there is any gain, else 0.
    out = out.where(avg_loss != 0.0, other=(avg_gain > 0.0).astype(float) * 100.0)
    return out


def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """MACD line, signal line, and histogram.

    Returns a frame with columns ``macd``, ``signal``, ``histogram``.
    """
    if fast >= slow:
        raise ValueError(f"fast ({fast}) must be less than slow ({slow})")
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame(
        {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": macd_line - signal_line,
        }
    )


def bollinger_bands(close: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands.

    Returns a frame with columns ``middle``, ``upper``, ``lower``, and
    ``pct_b`` (position within the bands; 0 = lower, 1 = upper).
    """
    _check_window(window)
    middle = sma(close, window)
    std = close.rolling(window).std(ddof=1)
    upper = middle + num_std * std
    lower = middle - num_std * std
    width = upper - lower
    return pd.DataFrame(
        {
            "middle": middle,
            "upper": upper,
            "lower": lower,
            "pct_b": (close - lower).divide(width).where(width != 0.0),
        }
    )


def zscore(series: pd.Series, window: int) -> pd.Series:
    """Rolling z-score: how many standard deviations from the rolling mean."""
    _check_window(window)
    mean = series.rolling(window).mean()
    std = series.rolling(window).std(ddof=1)
    return (series - mean).divide(std).where(std != 0.0)


def _check_window(window: int) -> None:
    if window < 1:
        raise ValueError(f"window must be >= 1, got {window}")
