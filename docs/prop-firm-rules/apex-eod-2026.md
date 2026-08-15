# Apex Trader Funding — EOD PA — ruleset `2026.06-unverified`

Comparison profile. **Not** an automation target — Apex's automation policy was
not established.

> **Verification status: USER_SUPPLIED / UNKNOWN.** `apextraderfunding.com`
> unreachable. `retrieved_at: null`.

## Operator-supplied (NOT verified)

| Account | `max_drawdown` | `max_contracts` |
|---|---|---|
| 25K | 1,000 | 2 |
| 50K | 2,000 | 4 |
| 100K | 3,000 | 6 |
| 150K | 4,000 | 10 |

- `drawdown_type = eod_trailing`
- `timing = END_OF_DAY` — intraday trailing drawdown does **not** apply
- `scaling = tier_based` (limits rise with account size; asserted by test)
- `profit_split = 100%`

## ❌ UNRESOLVED

| Rule | Why |
|---|---|
| `daily_loss_limit` | **The operator explicitly instructed not to invent Apex DLL values.** Recorded UNKNOWN with that instruction in the source note. |
| `profit_target` | Not supplied, not verified |
| `mll_basis` | balance vs equity not established |
| `automation_stance` | Apex automation policy not established |
| `api_available` | not established |

Because `prohibits_vps` is UNKNOWN, `ExecutionTopology.check()` **fails closed**
for Apex on *any* deployment — including local — rather than assuming a policy.

## Note on the EOD/intraday distinction

An end-of-day trailing drawdown and an intraday trailing drawdown produce very
different failure rates from identical trading: the intraday form fails accounts
on unrealised excursions that the EOD form never sees. `timing` is modelled
explicitly so the two are never conflated, but Apex's `basis` remains unresolved.
