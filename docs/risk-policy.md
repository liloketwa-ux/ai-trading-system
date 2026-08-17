# Risk Policy

## The safety hierarchy

```
Firm hard limits          ← external, verified, breaching one ends the account
    ↓
System risk limits        ← platform-wide guards
    ↓
User maximum risk         ← the trader's own ceiling
    ↓
Strategy risk budget      ← earned by research evidence
    ↓
Individual trade risk     ← volatility, correlation, execution
```

**No lower layer may override a higher one.** This is not a convention to
remember — `resolve_risk()` takes the minimum across every constraint, so a
lower layer can only ever tighten. A strategy budget of 99% against a firm
limit of 0.6% resolves to 0.6%, and there is no argument that changes that.

Ties break toward the higher-authority layer, so an explanation attributes a
shared limit to the firm rather than to a coincidentally equal user setting.

---

# User Risk Objectives

Everything in this section is a **user preference**. None of it is a firm rule,
none of it is verified against anyone's documentation, and none of it may
loosen a constraint that is.

## Why the separation is structural

A trader's daily target and a prop firm's loss limit are different kinds of
object:

| | User objective | Firm requirement |
|---|---|---|
| Origin | chosen by the trader | external fact |
| Verification | none needed — it is a preference | official source, `RuleVerificationLevel` |
| Consequence of missing it | a slower day | account terminated |
| Changeable | freely | never, by us |

Merging them produces a system that treats a personal goal with the authority
of a contractual limit, which is exactly backwards. So they live in separate
types: `UserRiskPolicy` carries `kind: "user_policy"`, `FirmProfile` carries
none of the user fields, and tests assert both directions of that separation.

Five concepts, five homes, never merged:

```
USER DAILY TARGET       UserRiskPolicy.daily_target_pct
USER MAX RISK/TRADE     UserRiskPolicy.max_risk_per_trade_pct
FIRM PROFIT TARGET      FirmProfile.profit_target
FIRM DAILY LOSS LIMIT   FirmProfile.daily_loss_limit
FIRM MAXIMUM LOSS       FirmProfile.max_loss_limit
```

For a $50K Topstep Combine these are genuinely different numbers: the firm's
profit target is $3,000 (verified), the user's daily target is $5,000 (a
preference). Confusing them would mis-state both.

## Daily target

The 10% daily target is, explicitly and unchangeably:

```
USER_DESIRED_DAILY_RETURN
```

It is **not** `MANDATORY_TRADE_TARGET`. That symbol does not exist —
`TargetSemantics` has exactly one member, so a caller cannot express the wrong
interpretation even by mistake, and `target_obliges_trading` is a property
fixed at `False` rather than a setting.

```python
daily_target_pct = 10.0
daily_target_amount = starting_daily_equity × 10%
```

Computed from the day's **starting** equity, not current. A target computed
from a rising balance recedes as the day goes well, which is not what anyone
means by "10% a day".

| Account | Daily target |
|---|---|
| $25,000 | $2,500 |
| $50,000 | $5,000 |
| $100,000 | $10,000 |
| $150,000 | $15,000 |

Three modes:

| Mode | Behaviour |
|---|---|
| `OPTIONAL` | tracked and reported; gates nothing |
| `ENFORCED_FOR_EVALUATION_SIM` | **default** — gates new trades in evaluation simulation |
| `INACTIVE` | not tracked |

The default is `ENFORCED_FOR_EVALUATION_SIM` because modelling the trader's own
stopping behaviour materially changes a pass-rate estimate. Live trading is not
forced to target 10%.

## The target never causes a trade

This is the most important property in this document, and it is enforced by the
shape of the API rather than by discipline.

**`resolve_risk()` cannot see daily-target progress.** Not "ignores it" —
there is no parameter to pass:

```python
def resolve_risk(constraints: Sequence[RiskConstraint]) -> ResolvedRisk
```

A test asserts the signature contains exactly one parameter and no name
containing `target`, `progress`, `pnl`, `shortfall` or `remaining`. No code
path exists in which being behind target increases size, lowers a signal
threshold, or manufactures a trade, because no such path can be written against
this interface.

`DailyTargetState.may_open_new_trade()` returns `False` for exactly one reason:
the target has been **met**. Being behind is never a reason to permit anything
extra. When no valid setup occurs, **`NO TRADE` is the correct outcome at any
P&L**, including a day deep below target — and `no_valid_setup` is recorded as
an ordinary state, not a failure.

## Target reached

When `realized + unrealized ≥ daily_target_amount`, state becomes
`DAILY_TARGET_REACHED` and the default action is:

```
STOP_NEW_TRADES
```

Additional risk would only enlarge a day that already met its objective.
Configurable — `CONTINUE_TRADING` and `REDUCE_RISK` exist — because some firm
programs require continued activity, and a user policy must never override a
firm requirement.

Unrealized P&L counts, so the target can be reached intraday on an open
position.

### Tracked state

```
daily_target_amount        daily_target_remaining
daily_target_progress      daily_target_reached
daily_target_progress_pct
```

## Maximum risk per trade

```python
max_risk_per_trade_pct = 2.0      # absolute user ceiling
baseline_risk_per_trade_pct = 0.25 # working default, well beneath it
```

**2% is a cap, not a default.** A system that defaults to its own ceiling has
no headroom left to express that one setup is better than another. The two are
separate fields, and construction refuses a baseline above the ceiling.

The actual `risk_per_trade` is resolved per trade from the hierarchy. The
ceiling only binds when nothing else is tighter.

## Strategy quality tiers

Risk is earned by research evidence:

| Tier | Eligibility | Budget |
|---|---|---|
| `INSUFFICIENT_SAMPLE` | `NO_LIVE_RISK` | 0% |
| `OUT_OF_SAMPLE_FAILURE` | `NO_LIVE_RISK` | 0% |
| `PROMISING` | `PAPER_ONLY` | 0% |
| `SURVIVES_ROBUSTNESS` | `LIMITED_RISK_ELIGIBLE` | 25% of the user ceiling |
| `ROBUST_CANDIDATE` | `FULL_RISK_POLICY_ELIGIBLE` | up to 100% of the user ceiling |

Tier and eligibility are separate types on purpose: the tier is a *finding*
about research, the eligibility is a *permission* granted on the back of it.
Keeping them apart means a tier can be redefined without silently
re-authorising capital.

`FULL_RISK_POLICY_ELIGIBLE` means eligible for the policy ceiling, **not
entitled to it**. No larger percentage is assigned automatically; the resolved
risk is still the minimum across firm, system, user, strategy and trade layers,
and this permission cannot raise any of them.

Budgets are expressed as **fractions of the user's own ceiling**, not as fixed
percentages. "0.75% for a robust strategy" would be a number with no
derivation; a fraction of a stated ceiling at least inherits its justification.
Final percentages are not assigned here and will not be until research evidence
supports them.

The two failing tiers return **zero**, not a small number. A strategy that
failed out of sample does not get a reduced allocation; it gets none.

## Target feasibility

A diagnostic, not a control:

```
Daily target                       = 10.0%
Historical 95th-percentile daily   =  2.1%
                                   → TARGET_MAY_BE_INFEASIBLE
```

A target only the best day in twenty can reach is not an objective, it is a
description of an outlier. The warning is **informational**. It carries no
sizing instruction — asserted by a test that checks the payload contains no
`recommended_risk` or `suggested_size` field — and the correct response is to
revise the target or accept missing it, never to trade larger.

Requires at least 30 observed days; below that it reports
`INSUFFICIENT_HISTORY` rather than guessing.

## Research metrics

Reported together, deliberately:

```
percentage_of_days_target_reached   maximum_daily_return
daily_target_hit_rate               maximum_daily_loss
median_daily_return                 days_with_no_trade
mean_daily_return                   days_with_overtrade_attempts
expectancy                          max_drawdown
daily_return_volatility             longest_losing_streak
tail_loss_p95
```

Optimising target-hit rate alone selects for strategies that reach 10% often
and lose the account occasionally. The drawdown, tail and streak figures are in
the same object so the trade-off is visible rather than discoverable later.

---

## Conflicts between user objectives and firm limits

One conflict exists today, and it is by design rather than by accident.

**A 10% daily target against a Topstep $50K Combine.** The account's Maximum
Loss Limit is $2,000 and the firm's whole profit target is $3,000. A 10% daily
target is $5,000 — larger than the entire evaluation objective, and 2.5× the
total loss allowance. Reaching it in one day would require risk far beyond what
the MLL can absorb.

The system's response is to **report the conflict, not resolve it by trading
larger**:

- `resolve_risk()` binds on the firm's MLL capacity, which is lower than the
  2% user ceiling. Verified by test.
- `assess_target_feasibility()` returns `TARGET_MAY_BE_INFEASIBLE` once daily
  return history exists.
- Nothing raises position size to close the gap.

The user target is a preference and the MLL is a contractual limit. Where they
disagree, the limit wins and the preference goes unmet. That is the correct
outcome, and it is worth being explicit that a 10% daily objective on a
prop-firm evaluation account is not reachable within the firm's own risk
allowance — the target and the account are in tension, and no amount of sizing
resolves it safely.

---

## What is not implemented

No live execution. No credentials. No risk parameter tuned to make an
evaluation pass. Strategy tiers carry no assigned percentages beyond fractions
of a user-stated ceiling, and will not until research evidence exists to
justify them.
