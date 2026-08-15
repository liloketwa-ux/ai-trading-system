# MyFundedFutures — Rapid — ruleset `2026.06-unverified`

Comparison profile.

> **Verification status: USER_SUPPLIED / UNKNOWN.** `myfundedfutures.com`
> unreachable. `retrieved_at: null`.

## Operator-supplied (NOT verified)

| Rule | Value |
|---|---|
| `profit_split` | 90% |
| `payout_cadence` | daily |
| `activation_fee` | 0 |
| `min_trading_days` | 2 |
| `drawdown_type` | EOD |
| `daily_loss_limit` | none (encoded as `0`) |
| `evaluation_consistency` | 50% |

## ❌ UNRESOLVED — deliberately

Per instruction, **account-size-specific values were not assumed**:

| Rule | Status |
|---|---|
| `initial_balance` | UNKNOWN |
| `profit_target` | UNKNOWN |
| `mll_threshold` | UNKNOWN |
| `max_minis` / `max_micros` | UNKNOWN |
| `mll_basis` | UNKNOWN |
| `automation_stance` | UNKNOWN |

The profile is registered with `account_size = 0` precisely because no verified
size-specific ruleset exists. This is a placeholder shape, not a usable account
model.

## On `daily_loss_limit = 0`

Zero encodes *"no daily loss limit"* rather than *"a limit of zero"*. The
comparison engine treats a falsy limit as no constraint and records no
violations. Still `USER_SUPPLIED` — the absence of a DLL is itself a rule
requiring verification.
