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
  risk/         # Position sizing, stops, leverage/drawdown/VaR controls
  execution/    # Order manager and broker/exchange adapters
  monitoring/   # Dashboards, drift detection, retraining hooks
docs/           # Design documentation
tests/          # Test suite
```

> **Status:** Scaffold only. Modules define interfaces and raise
> `NotImplementedError` — no trading logic is implemented yet.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in API keys
pytest
```

## Configuration

Copy `.env.example` to `.env` and provide credentials for the data sources and
brokers you intend to use. **Never commit secrets** — `.env` is gitignored.
