# Robustness Protocol

Phase 7. A result that survives only at the exact cost and delay assumed is not
a finding — it is a coincidence with a decimal point.

## Perturbation matrix

| Axis | Points | Note |
|---|---|---|
| Cost | 1.0×, 1.25×, 1.5×, 2.0×, 3.0× | commissions + fees + spread |
| Slippage | 1.0×, 1.5×, 2.0×, 3.0× | on top of the base model |
| Delay | 0, 1, 2, 3 **bars** | see below |

**Delay is expressed in bars, not milliseconds.** The dataset's finest
resolution bounds what can honestly be claimed: on hourly bars a 250ms delay is
unobservable, and reporting sensitivity to it would imply a precision the data
does not contain. Phase 6 demonstrated this directly — 0ms, 250ms and 1000ms
produced byte-identical results on hourly data, and that is now a test.

Each curve reports where expectancy crosses zero (`breaks_at`), whether it
survives every point, total degradation, and an interpolated break-even
multiplier.

## Trade-removal analysis

`remove_best_1`, `remove_best_5`, `remove_best_10`, `remove_worst_1`,
`remove_worst_5`, `remove_top_5pct_wins`.

A candidate whose edge disappears when its single best trade is deleted did not
have an edge; it had one lucky trade and a lot of noise. `outlier_dependent` is
reported on the headline record.

## Contract rolls

Stitching contracts without adjustment manufactures a price jump the trader
never experienced. Back-adjusting removes the jump but distorts absolute price
levels, so percentage returns computed from it are wrong — increasingly so with
history length. There is no free option, so the policy is explicit.

| Roll method | Adjustment |
|---|---|
| `NONE` (default) | `NONE` — individual contracts only |
| `CALENDAR` / `VOLUME` / `OPEN_INTEREST` | `NONE` / `BACK_ADJUSTED` / `RATIO_ADJUSTED` |

**Default fails closed.** `RollPolicy().assert_continuous_claim()` raises
`ContinuityError`. Continuous adjustment is *not implemented*, so results cover
individual contracts and must not be described as continuous history.

## Funding and financing

Components are separated: price PnL, trading fees, spread, slippage, funding,
borrow. Each carries a status — `MEASURED`, `ESTIMATED`, `NOT_APPLICABLE`,
`UNAVAILABLE`.

**A net figure missing a material component is refused, not rounded.**
`PnLBreakdown.net()` raises `EconomicConfidenceError` while any component is
`UNAVAILABLE`. Futures having no perpetual funding is `NOT_APPLICABLE` — a fact,
not a gap — and does not block a net claim.

## Verdicts

Ordered least to most favourable; the most disqualifying condition wins.

| Verdict | Trigger |
|---|---|
| `INSUFFICIENT_SAMPLE` | below the trade or window gate |
| `OUT_OF_SAMPLE_FAILURE` | mean out-of-sample expectancy ≤ 0 |
| `UNSTABLE` | median ≤ 0, too few positive windows, catastrophic window, or outlier dependence |
| `COST_SENSITIVE` | breaks at or below the cost gate |
| `EXECUTION_SENSITIVE` | breaks at or below the delay gate |
| `REGIME_DEPENDENT` | positive in too few adequately-sampled regimes |
| `SURVIVES_ROBUSTNESS` | passes gates, no perturbation matrix supplied |
| `ROBUST_CANDIDATE` | every gate, matrix present, not outlier-dependent |

## Criteria are configuration

Defaults: 100 trades, 5 windows, ≥50% positive windows, positive mean **and**
median expectancy, no window worse than −50%, survives ≥1.5× costs and ≥1 bar
delay, best-trade dependence under 50%, positive in ≥2 regimes with ≥30
observations each.

Thresholds chosen because they made a candidate look good are how robustness
testing becomes robustness theatre, so the **criteria version travels with every
verdict** — a threshold changed later cannot silently re-grade historical
results.

## Instrument failures are never aggregated away

Each of ES, NQ, YM, GC, CL is graded independently. The overall verdict is the
**weakest** instrument, never the best: a candidate that works on ES and fails on
NQ is an ES candidate at best, and reporting the maximum would hide exactly the
failure worth knowing about. Instrument-specific candidates record that
intention explicitly.

## Sample-size rule

21 synthetic trades do not qualify for a walk-forward conclusion. Below the
gates the verdict is `INSUFFICIENT_SAMPLE` and no other status can be reached —
the reporting system makes thin evidence obvious rather than burying it.

## Holdout

Remains unspent. Producing a report never touches it, asserted by test.
