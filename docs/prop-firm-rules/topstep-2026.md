# Topstep Trading Combine — ruleset `2026.06-unverified`

**`PRIMARY_AUTOMATION_TARGET`** — on the operator's statement that Topstep
permits automated trading and provides TopstepX/ProjectX API access.

> **Verification status: USER_SUPPLIED / UNKNOWN.** `topstep.com` and
> `help.topstep.com` were unreachable. `retrieved_at: null`.

## Operator-supplied (NOT verified)

| Rule | 50K | 100K | 150K |
|---|---|---|---|
| `initial_balance` | 50,000 | 100,000 | 150,000 |
| `profit_target` | 3,000 | 6,000 | 9,000 |
| `max_minis` | 5 | 10 | 15 |
| `max_micros` | 50 | 100 | 150 |

Also operator-supplied: `min_trading_days = 2`, trading day
`17:00 CT → 15:10 CT next calendar day`, `forced_flat_time = 15:10 CT`,
`session_reopen = 17:00 CT`, `overnight_positions_allowed = false`,
consistency `best_day / total < 0.50`, `automation_allowed = true`,
`api_available = true`, `api_provider = TopstepX/ProjectX`.

## ❌ UNRESOLVED — the Maximum Loss Limit

**Every MLL field is `UNKNOWN`**, and `require_for_adjudication()` raises.

| Field | Status |
|---|---|
| `drawdown_type` | UNKNOWN |
| `threshold` | UNKNOWN |
| `calculation_method` | UNKNOWN |
| `timing` (intraday vs EOD) | UNKNOWN |
| `basis` (balance vs equity) | UNKNOWN |
| `locks_at` | UNKNOWN |

This is the single most consequential gap. The MLL is the Combine's hard
failure rule — **not** the FTMO-style 5% daily / 10% total pair, and reusing
those defaults would adjudicate a Combine under rules it does not have.

The calculation method matters as much as the threshold: a limit trailing
intraday unrealised equity is materially harsher than one computed on
end-of-day closed balance, and the difference decides whether an open loser
fails the account. Guessing is not conservative in either direction.

`daily_loss_limit` is also UNKNOWN — whether a separate DLL applies alongside
the MLL was not established.

Also unresolved: the effect of a consistency-target increase.

## Topology constraint — architectural

The operator states Topstep's API documentation requires trading activity to
originate from the user's personal device and prohibits VPS/VPN/remote-server
use.

```
cloud research  --signals-->  LOCAL execution agent  -->  TopstepX API
```

`ExecutionTopology(CLOUD).check(topstep)` **refuses**: *"topstep prohibits
remote-server execution… must originate from the user's personal device."* A
rules breach voids an account regardless of the equity curve, so this is
enforced in code rather than documented as advice.

## Prohibited practices

Simulator exploitation · stale-feed exploitation · price-display exploitation ·
spoofing · trading outside BBO · unrealistic SIM-fill exploitation ·
coordinated/cross-account hedging · prohibited HFT · unfair-advantage
technology · max size into scheduled news.

Declared via `PracticeDeclaration`; the `ComplianceGate` blocks any match.

## Current gate state

`ComplianceGate` refuses **even local deployment**, because 21 rules are
unverified. No firm API client ships — `FirmExecutionProvider` is abstract.
