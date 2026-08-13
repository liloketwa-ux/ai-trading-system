# Can this system pass a funded-account challenge?

**Short answer: it can pass one, roughly a third of the time — and so can a
random number generator. That is the finding, not a technicality.**

This document records a Monte Carlo study run with
`ai_trading.backtest.challenge` against the strategies in this repository.

## Setup

| Parameter | Value |
|---|---|
| Rules | +10% target, −5% daily loss, −10% max drawdown (static), 4 min trading days, 30-day deadline |
| Data | Synthetic geometric Brownian motion, **zero drift**, 60% annualized volatility |
| Bars | Hourly, 30 days (720 bars per run) |
| Costs | 2bp commission + 3bp slippage per side, and a zero-cost control |
| Paths | 300 per strategy |

The data is zero-drift by construction, which is the whole point: **no
exploitable edge exists in it**. Any strategy's true expected return is exactly
zero before costs. So every pass is, by construction, luck.

## Results

### Zero costs — true edge is exactly zero

| Strategy | Mean return | Equity vol | Pass rate |
|---|---|---|---|
| Momentum | +0.92% ± 1.72% | 50% | 39.3% ± 5.5% |
| Mean reversion | +0.68% ± 1.38% | 38% | 32.0% ± 5.3% |
| Random (p=0.02) | +0.81% ± 2.02% | 59% | 32.7% ± 5.3% |
| Random (p=0.10) | −0.12% ± 1.91% | 59% | 30.7% ± 5.2% |

No strategy's mean return is distinguishable from zero — exactly as theory
demands. The engine reproduces the correct null result.

### With costs (2bp commission + 3bp slippage)

| Strategy | Mean return | Equity vol | Pass rate |
|---|---|---|---|
| Momentum | **−1.72% ± 1.70%** | 50% | 34.0% ± 5.4% |
| Mean reversion | **−2.05% ± 1.33%** | 38% | 22.3% ± 4.7% |
| Random (p=0.02) | −0.08% ± 2.00% | 59% | 31.7% ± 5.3% |
| Random (p=0.10) | **−3.64% ± 1.84%** | 59% | 26.0% ± 5.0% |

Every mean return is negative, significantly so for the higher-turnover
strategies. Costs are the only systematic force in the system.

### ICT

0% pass rate over 60 paths (67% failed on drawdown). Consistent with the
regime study, where the mechanical ICT encoding showed no measurable edge.

## What this means

**1. A ~30% pass rate requires no edge whatsoever.** Random position-taking
passes 26–32% of the time. Momentum's 34% overlaps that inside the confidence
interval. Passing a challenge is not evidence a strategy works.

**2. Pass rate is driven by position size, not skill.** From the earlier sweep:

| Position weight | Momentum pass rate | Dominant failure |
|---|---|---|
| 0.25 | 0.5% | Timeout — too slow to reach +10% |
| 0.50 | 13.5% | Timeout |
| 1.00 | 35.5% | Daily loss / drawdown |

Smaller size cannot reach the target in time; larger size reaches it more often
*and* blows up more often. The challenge is a bet on variance. Turning size up
raises the pass rate and lowers expected value simultaneously.

**3. Expected value across repeated attempts is negative.** At a ~34% pass rate,
the average attempt costs roughly three challenge fees per pass, against a
strategy whose measured expected return is *negative* after costs. Passing does
not fix that — a funded account demands ongoing profitability, where negative
expectancy compounds against you rather than resolving in one lucky month.

## Caveats

These estimates are, if anything, **optimistic**:

- Synthetic GBM has no fat tails, gaps, or weekend risk. Real markets do, and
  all three hurt an account with a hard daily-loss limit.
- Adjudication is only as granular as the equity series. Real evaluations
  measure equity continuously including floating PnL; anything coarser misses
  intraday breaches.
- Slippage is modeled as a constant, not as something that widens exactly when
  a strategy most wants to exit.
- No modeling of firm-specific rules that commonly disqualify accounts:
  restrictions on automated trading, news-window bans, minimum hold times, or
  consistency requirements.

**Check whether the firm permits automated trading at all.** Many prohibit or
restrict EAs, and a rules breach voids a pass regardless of the equity curve.

## Reproducing

```python
from ai_trading.backtest import ChallengeRules, evaluate_challenge

rules = ChallengeRules(profit_target=0.10, max_daily_loss=0.05, max_drawdown=0.10)
result = evaluate_challenge(equity_curve, rules)
print(result.summary())   # "passed: +10.50% over 12 days (8 trading), ..."
```
