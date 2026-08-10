# AI Trading System

An AI-driven trading system that ingests diverse market signals (social media,
news, on-chain, price/volume) and uses NLP and quantitative models to generate
high-conviction trade ideas with explicit rationales and automated execution —
under strict risk, monitoring, security, and compliance controls.

> **Disclaimer:** This project is for research and engineering purposes only. It
> is **not** financial or legal advice. Any live deployment must be reviewed by
> qualified legal/compliance counsel. The system is designed to trade only on
> **public** information and to adhere to platform Terms of Service and
> applicable market regulations.

## Design

The full design document lives at
[`docs/ai-trading-system-design.md`](docs/ai-trading-system-design.md). It covers
data sources, ingestion pipeline, NLP/sentiment modeling, signal engineering,
strategy generation (ICT / momentum / mean-reversion), backtesting, risk
management, execution architecture, TradingView/Pine Script alerting,
monitoring/retraining, security & compliance, and an implementation roadmap.

## Project layout

```
src/ai_trading/
  ingestion/    # Data fetchers (news APIs, X/Twitter, on-chain, market data)
  nlp/          # Sentiment & hype models (FinBERT/RoBERTa), text analytics
  features/     # Feature engineering from raw + NLP-processed data
  strategies/   # Strategy definitions (ICT, momentum, mean-reversion, ...)
  backtest/     # Backtesting engine and performance metrics
  risk/         # Position sizing, stops, leverage/drawdown controls
  execution/    # Order manager and broker/exchange adapters
  monitoring/   # Dashboards, drift detection, retraining hooks
docs/           # Design documentation
tests/          # Test suite
```

## Status

| Module | State |
|---|---|
| `features` | **Implemented** — causal indicators (SMA/EMA/RSI/ATR/MACD/Bollinger/z-score), sentiment aggregation, hype scoring |
| `risk` | **Implemented** — risk-per-trade sizing, ATR stops, position/leverage caps, drawdown halt |
| `backtest` | **Implemented** — lookahead-safe event-driven engine, cost model, trade accounting, metrics |
| `strategies` | **Implemented** — market structure (swings, BOS, FVGs, order blocks, liquidity sweeps), ICT, momentum breakout, mean reversion |
| `ingestion`, `nlp`, `execution`, `monitoring` | Interface stubs — raise `NotImplementedError` |

No live trading is wired up: `execution` has no working broker adapter, by design.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .        # or: export PYTHONPATH=src
cp .env.example .env    # then fill in API keys
pytest
```

## Example

```python
import pandas as pd
from ai_trading.backtest import Backtester
from ai_trading.features import FeatureEngine

bars = ...  # OHLCV DataFrame with a sorted DatetimeIndex

features = FeatureEngine().build(bars)

def strategy(history: pd.DataFrame) -> float:
    """Return a target position weight; NaN means 'no decision yet'."""
    if len(history) < 50:
        return float("nan")
    close = history["close"]
    return 1.0 if close.iloc[-20:].mean() > close.iloc[-50:].mean() else 0.0

result = Backtester(100_000.0, commission_bps=1, slippage_bps=2).run(bars, strategy)
print(result.metrics)
```

### Lookahead safety

The backtester enforces causality structurally rather than by convention: the
decision for bar `i` receives only `bars.iloc[:i+1]` and fills at bar `i+1`'s
**open**. A strategy cannot see a price it would not have had, because the
engine never hands it a longer slice. The final bar's signal is deliberately
never executed. Slippage and commission are folded into the fill price so cash,
equity, and realized trade PnL stay mutually consistent.

Two tests assert this directly: one checks the history slice always ends at the
decision bar, and one checks that mutating a future bar cannot change any
earlier fill.

The same discipline extends to market structure. A swing high is only a swing
high once bars have printed to its *right* to confirm it, so acting on the pivot
at its own bar is trading on information that did not exist yet — the most
common way ICT backtests manufacture fake edge. `find_swings` can only confirm a
pivot when the confirming bars are inside the frame it was given, so on
`history[:i+1]` it cannot report a pivot more recent than `i - right`. Every
zone carries the `confirmed_index` at which it became actionable.

### Regime behaviour

Correct strategies win in the regime they are designed for and lose in its
opposite. Measured on synthetic series (1500 bars, 1bp commission, 2bp slippage):

| Strategy | Trending | Mean-reverting |
|---|---|---|
| `MomentumBreakout` | +302% (Sharpe 1.52) | −93% (Sharpe −1.67) |
| `MeanReversion` | −68% (Sharpe −1.32) | +309% (Sharpe 1.21) |

That mirror-image pattern is the point: a strategy that profits in *both*
regimes is usually reading the future, not the market. Backtest numbers on
synthetic data say nothing about live performance.

## Configuration

Copy `.env.example` to `.env` and provide credentials for the data sources and
brokers you intend to use. **Never commit secrets** — `.env` is gitignored.
