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

## Status: `EVIDENCE_PENDING`

**The research engine is implemented. Real-market evidence is pending.**

No hypothesis has been evaluated against real market data, because no real
market dataset has been obtained. That is the entire state of the project, and
everything below should be read against it:

- **No profitability is claimed or demonstrated.** Nothing here has produced a
  P&L on a real market.
- **No claim is made that ICT works.** `ICT-FAMILY-V1` is six pre-registered
  *questions*, frozen before the data existed, with no answers yet. A null
  result is a possible and legitimate outcome.
- **Not ready for a funded account.** Prop-firm rules are modelled and the
  account simulation runs; no strategy has out-of-sample evidence to put
  through it.
- **Live automation is not ready and is not enabled.** There is no live broker
  adapter and no funded-account credential.

Results computed on synthetic data describe the generator. They are useful —
the calibration suite is what shows the machinery is not blind — and they are
never evidence about a real market.

```bash
python -m ai_trading.project.cli system:status   # derived, deterministic
python -m ai_trading.project.cli system:audit    # 8 read-only integrity checks
```

Full detail: [`docs/system-readiness.md`](docs/system-readiness.md). The single
next action is external — obtain and authorise access to a real NQ data
provider — specified in
[`docs/real-data-handoff.md`](docs/real-data-handoff.md).

### Modules

| Module | State |
|---|---|
| `features` | **Implemented** — causal indicators (SMA/EMA/RSI/ATR/MACD/Bollinger/z-score), sentiment aggregation, hype scoring |
| `risk` | **Implemented** — risk-per-trade sizing, ATR stops, position/leverage caps, drawdown halt |
| `backtest` | **Implemented** — lookahead-safe event-driven engine, cost model, trade accounting, metrics |
| `strategies` | **Implemented** — market structure (swings, BOS, FVGs, order blocks, liquidity sweeps), ICT, momentum breakout, mean reversion |
| `execution` | **Implemented (paper only)** — order/position types, paper broker, order manager with risk gate, kill switch, retries |
| `monitoring` | **Implemented** — event log, PSI/KS drift detection, live performance tracking, backtest-vs-live divergence |
| `ingestion`, `nlp` | Interface stubs — raise `NotImplementedError` |

**No live trading is wired up.** `execution` ships a paper broker only; there is
deliberately no live broker adapter. Adding one is the step that turns simulated
orders into real ones and should be a separate, explicit decision — reviewed
alongside credential handling and position reconciliation — not a side effect of
a refactor.

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

### Execution

Signals reach a broker only through `OrderManager`, which applies the kill
switch, drawdown halt, and risk sizing first — a strategy never sizes its own
position, so a bug in strategy code cannot become an oversized order. The risk
manager sets the magnitude a full position may take; the signal's `weight`
supplies direction and scales it.

```python
from ai_trading.execution import OrderManager, PaperBroker
from ai_trading.risk import RiskLimits, RiskManager
from ai_trading.strategies import Signal

broker = PaperBroker(cash=100_000.0)
broker.update_price("BTC", 100.0)

manager = OrderManager(broker, RiskManager(RiskLimits(risk_per_trade=0.01)))
report = manager.execute(Signal("BTC", 1.0, "entry"), price=100.0, atr=5.0)
print(report.accepted, report.reason, report.target_units)

manager.engage_kill_switch("incident")   # blocks new risk; flatten() still works
```

The kill switch blocks risk-*increasing* orders but always permits flattening —
a control that reduces exposure must never trap the system in a position it
cannot exit.

The broker and the backtester implement average-cost accounting independently.
A regression test drives both with identical prices, costs, sizing, and fill
timing and asserts they agree exactly; across 300 bars and hundreds of fills
including long/short flips, they match to floating-point precision.

### Monitoring

`Monitor` observes a running system and emits severity-tagged events. It
deliberately never touches the trading path — responding to a CRITICAL event
(engaging the kill switch, flattening) stays an explicit decision in the
execution layer, so monitoring cannot surprise a live system.

```python
from ai_trading.monitoring import Monitor, MonitorThresholds

monitor = Monitor(MonitorThresholds(drawdown_warning=0.10, drawdown_critical=0.15))
monitor.record_equity(timestamp, equity)          # escalates on drawdown
monitor.check_drift(reference_features, live_features)   # PSI + KS per feature
monitor.check_divergence(backtest_returns)        # paired live-vs-backtest test
print(monitor.snapshot(), monitor.healthy)
```

Drift uses two complementary measures: **PSI** for how much probability mass
moved between bins, and a two-sample **Kolmogorov-Smirnov** test for whether the
samples plausibly share a distribution at all. KS catches shifts PSI shrugs at —
a pure variance change leaves the mean untouched but moves the CDF.

Divergence is a *paired* comparison: replay the backtest over the live window
and difference bar by bar, which removes the shared market move and leaves the
implementation gap. Live trailing a backtest is the normal case, not proof of a
bug — backtests omit costs production pays — so a significant result is a prompt
to investigate, not a verdict.

Both statistics are implemented on numpy alone. A test cross-validates the KS
implementation against SciPy when it is installed (`pip install -e ".[dev]"`):
the D statistic matches exactly, and p-values agree at decision-relevant levels.
The asymptotic p-value diverges from the exact one deep in the tail, where both
are far past any threshold.

## Configuration

Copy `.env.example` to `.env` and provide credentials for the data sources and
brokers you intend to use. **Never commit secrets** — `.env` is gitignored.
