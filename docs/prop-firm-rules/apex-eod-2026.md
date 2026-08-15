# Apex Trader Funding — EOD Performance Account — ruleset `2026.08`

Source: *EOD Performance Accounts (PA)* —
`https://apextraderfunding.com/help-center/eod-trailing-drawdown-accounts/eod-performance-accounts-pa/`

Verified 2026-08-15 by operator review, outside this coding environment.
Status `OFFICIAL_SOURCE_VERIFIED`, method `official_source_review`.

**Comparison profile.** Apex is not an automated-execution target; its
automation policy was not verified, and `ExecutionTopology` therefore refuses
every deployment.

Address: `apex / eod_pa / funded_sim / <size> @ v2026.08`

## Verified

| Size | EOD drawdown | Max contracts |
|---|---|---|
| 25K | 1,000 | 2 |
| 50K | 2,000 | 4 |
| 100K | 3,000 | 6 |
| 150K | 4,000 | 10 |

`profit_split = 1.00` (subject to eligibility). Scaling is tier-based: the
contract limit rises with account size.

## Drawdown semantics

`MLLMode.EOD_TRAILING_INTRADAY_ENFORCED` — the same *shape* as Topstep's MLL,
with different amounts:

- **No intraday trailing drawdown.** The threshold is calculated once per day
  from end-of-day balance.
- **Enforced intraday.** The level that results is then live during the
  following session.

`timing = END_OF_DAY` records how the threshold is computed; `mode` records the
whole rule, including that it bites during the day. Reading `timing` alone would
suggest an account can dip below the level intraday and survive, which it
cannot.

Apex's daily loss limit is likewise enforced intraday.

## ⚠️ Not verified

| Field | Status | Why it matters |
|---|---|---|
| `mll_locks_at` | `UNKNOWN` | whether the threshold freezes at the starting balance is the difference between a drawdown that eventually stops tightening and one that never does |
| `daily_loss_limit` | `UNKNOWN` | the source confirms a DLL is enforced; the amounts were not verified and the instruction was **not to invent them** |
| `daily_loss_limit_mode` | `UNKNOWN` | |
| `max_micros` | `UNKNOWN` | |
| `automation_stance`, `api_available` | `UNKNOWN` | |
| `min_trading_days` | `UNKNOWN` | |
| session boundary | `UNKNOWN` | |

Because the lock behaviour is unknown, `max_loss_limit.build_tracker()` raises
`UnverifiedRuleError` even though the threshold itself is verified. The amount
is only half the rule.

## Evaluation stage

**Not registered.** The instruction was explicit: do not invent missing
evaluation values. `REGISTRY.resolve("apex", "eod_pa", Stage.EVALUATION, ...)`
returns `None` rather than a plausible fabrication — asserted by test.

## Readiness

`PARTIALLY_VERIFIED`. No capability is `ADJUDICATION_READY`.
