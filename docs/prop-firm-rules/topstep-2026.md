# Topstep — ruleset `2026.08`

**`PRIMARY_AUTOMATION_TARGET`.** Verified 2026-08-15 by operator review of
Topstep's official Help Center documentation, outside this coding environment.
Status `OFFICIAL_SOURCE_VERIFIED`, method `official_source_review`,
`retrieved_at: null` — nothing here was fetched by this code.

## Programs

| Program | Stage | Sizes |
|---|---|---|
| Trading Combine | `evaluation` | 50K / 100K / 150K |
| Express Funded (Standard) | `funded_sim` | 50K / 100K / 150K |
| Express Funded (Consistency) | `funded_sim` | 50K / 100K / 150K |
| Live Funded | `live_funded` | 50K / 100K / 150K |

Combine objectives are **not** inherited by the Express Funded or Live Funded
programs. Each is its own ruleset at its own address.

---

# Trading Combine

Source: *Trading Combine Parameters* —
`https://help.topstep.com/en/articles/8284197-trading-combine-parameters`

| Rule | 50K | 100K | 150K |
|---|---|---|---|
| `initial_balance` | 50,000 | 100,000 | 150,000 |
| `profit_target` | 3,000 | 6,000 | 9,000 |
| `max_minis` | 5 | 10 | 15 |
| `max_micros` | 50 | 100 | 150 |
| `min_trading_days` | 2 | 2 | 2 |

## The Maximum Loss Limit

Source: *What is the Maximum Loss Limit?* —
`https://help.topstep.com/en/articles/8284204-what-is-the-maximum-loss-limit`

| Size | MLL | Locks at |
|---|---|---|
| 50K | 2,000 | 50,000 |
| 100K | 3,000 | 100,000 |
| 150K | 4,500 | 150,000 |

The rule in one line: **an end-of-day trailing threshold with intraday
enforcement**. `MLLMode.EOD_TRAILING_INTRADAY_ENFORCED`.

Two clocks run on the same number, and modelling either alone gets the rule
wrong in a specific, predictable direction:

- **What it trails.** End-of-day *balance*. An intraday spike to +$1,200 that
  closes the day at +$300 moves the limit by $300, not $1,200. Trailing the
  intraday high is materially harsher than the real rule and fails accounts that
  would have survived.
- **When it bites.** Continuously, on *equity*. Open unrealised loss counts.
  Touching the limit at any tick causes immediate liquidation — a closed balance
  comfortably above the limit does not save an account whose open position is
  below it.

The remaining invariants:

- Starts at `starting_balance − trailing_amount`.
- Moves **up only**; a losing day never moves it down.
- Never exceeds the starting balance, and **freezes** once it gets there.
  Further profit stops tightening the account.
- A breach is terminal. Recovering later does not un-breach it, and a dead
  account does not keep trailing.

```python
tracker = profile.max_loss_limit.build_tracker(50_000)
tracker.limit_level                                  # 48_000
tracker.mark(t, equity=47_900, realized_balance=49_500)
#   -> LIQUIDATE_AND_FAIL, caused_by_unrealized=True
tracker.end_of_day(day, closing_balance=52_000)
tracker.limit_level, tracker.locked                  # 50_000, True
```

`build_tracker()` refuses unless the threshold, mode, timing, basis, drawdown
type, calculation method **and lock level** are all verified.

## Optional Daily Loss Limit

Source: *Daily Loss Limit in the Trading Combine and Express Funded Account* —
`https://help.topstep.com/en/articles/10490293-daily-loss-limit-in-the-trading-combine-and-express-funded-account`

The Combine ships **without** one; the account's `daily_loss_limit` is
`NOT_APPLICABLE` and its mode is `NONE`. A limit may be purchased:

| Size | Purchase-set DLL |
|---|---|
| 50K | 1,000 |
| 100K | 2,000 |
| 150K | 3,000 |

```python
DailyLossLimitMode.NONE             # default
DailyLossLimitMode.PURCHASE_SET     # bought with the account, fixed by the firm
DailyLossLimitMode.PERSONAL_MANUAL  # set by the trader; USER_SUPPLIED, not a firm rule
```

Elect one with `profile.with_daily_loss_limit(mode)`. A `PERSONAL_MANUAL` limit
requires the trader's own number and is recorded as `USER_SUPPLIED`, because the
firm never published it.

**Hitting the DLL is not a Combine eligibility violation.** It applies intraday
across the trading day, flattens open positions, cancels pending orders and
blocks new trades until the next session. The account survives and the
evaluation continues. The action is `FLATTEN_AND_LOCK_SESSION` and can never be
`LIQUIDATE_AND_FAIL`.

Where both limits would fire on the same tick, the MLL wins: an account that has
breached it is finished, and reporting a session lock-out instead would be a
friendlier answer than the truth.

## Consistency

Source: *Consistency at Topstep* —
`https://help.topstep.com/en/articles/8284208-consistency-at-topstep`

`best_day_profit / total_profit ≤ 0.50`

| Size | Recommended max best day |
|---|---|
| 50K | 1,500 |
| 100K | 3,000 |
| 150K | 4,500 |

Exceeding it **raises the profit target**. It does not fail the account, and the
code reports `CONSISTENCY_NOT_MET` rather than `RULE_VIOLATION`, with
`required_total_profit` set to the total at which the best day becomes
compliant. A $2,000 best day against $3,000 total needs $4,000 total, not a new
account.

## Trading hours — ⚠️ NOT VERIFIED

The implemented boundary is `17:00 CT → 15:10 CT`, `forced_flat = 15:10 CT`,
`reopen = 17:00 CT`, `overnight_allowed = false`, timezone `America/Chicago`.

These were **explicitly excluded from the review** pending verification against
Topstep's Permitted Products and Trading Hours, so they remain `USER_SUPPLIED`
and `Capability.SESSION_BOUNDARY_ENFORCEMENT` refuses.

This is the single reason the Combine is `PARTIALLY_VERIFIED` rather than
`ADJUDICATION_READY`, and it is not a formality: the trading-day boundary is
what decides *when* the end-of-day trailing threshold advances. An unverified
value there sits at the centre of the MLL.

## Automation and execution policy

Sources: *TopstepX API Access* —
`https://help.topstep.com/en/articles/11187768-topstepx-api-access`;
*Prohibited Trading Strategies at Topstep* —
`https://help.topstep.com/en/articles/10305426-prohibited-trading-strategies-at-topstep`

| Field | Value |
|---|---|
| `automation_stance` | `ALLOWED` |
| `api_available` | `true` |
| `api_provider` | `TopstepX` |
| `requires_local_execution` | `true` |
| `prohibits_vps` | `true` |

Capabilities: REST, WebSocket, live and historical market data, direct trade
execution, custom risk management. **No sandbox.**

Constraints: trading activity must originate from the trader's personal device.
VPS, VPN and remote-server execution are prohibited — **which includes the
container this code was written in.** `ExecutionTopology` refuses any deployment
that is not `LOCAL_DEVICE`.

### AI is not banned

Topstep prohibits technology — including AI and ultra-high-speed systems — *used
to manipulate or abuse the platform or gain an unfair advantage*. Automated
strategies themselves are permitted. Conflating the two would refuse a permitted
activity.

`ComplianceGate` rejects declared: platform and simulator exploitation, stale
feed exploitation, price-display exploitation, spoofing, quote manipulation,
trading outside the BBO, unrealistic simulated fills, coordinated cross-account
hedging, prohibited high-frequency behaviour, technology for unfair advantage,
and maximum size into scheduled news.

---

# Express Funded Accounts

Source: *Express Funded Account Parameters* —
`https://help.topstep.com/en/articles/8284215-express-funded-account-parameters`;
*Topstep Payout Policy* —
`https://help.topstep.com/en/articles/8284233-topstep-payout-policy`

## Verified

| Field | Value |
|---|---|
| `profit_split` | 0.90 (90/10) |

Payout caps, stored in `PayoutPolicy.xfa` — **payout rules, not profit
targets**, and never consulted by the eligibility simulator:

| Size | Standard | Consistency |
|---|---|---|
| 50K | 2,000 | 3,000 |
| 100K | 3,000 | 4,000 |
| 150K | 5,000 | 6,000 |

Standard and Consistency are registered as **separate programs**, since they
differ in payout structure.

## Not applicable

`profit_target` and `min_trading_days` are `NOT_APPLICABLE`: they are evaluation
objectives and an XFA does not carry them. Winning-day requirements are a payout
condition and live in the payout policy, not the risk rules.

## ⚠️ Not verified

The XFA's own MLL amounts, drawdown type, optional DLL, and scaling plan were
not covered by the review and are `UNKNOWN`. They are **not** inherited from the
Combine — copying the Combine's $2,000 MLL onto a $50K XFA would produce a
simulator that runs perfectly and is wrong. `build_tracker()` refuses.

---

# Live Funded

Registered at all three sizes with every risk rule `UNKNOWN`, so live-funded
rules cannot be silently mistaken for an XFA's. **This system does not submit
orders anywhere, least of all here.**
