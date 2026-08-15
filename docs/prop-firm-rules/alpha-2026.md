# Alpha Futures — ruleset `2026.06-unverified`

**Research comparison only. Explicitly NOT a live-automation target.**

> **Verification status: USER_SUPPLIED / UNKNOWN.** `alphafutures.com`
> unreachable. `retrieved_at: null`.

## Automation policy (operator-supplied, NOT verified)

| Aspect | Status |
|---|---|
| `full_automation` | **prohibited** |
| AI / bots | **prohibited** |
| `semi_automation` | allowed, subject to manual execution |

Encoded as `AutomationStance.SEMI_ONLY`, so
`permits_full_automation` returns `False`.

## Consequence in the comparison engine

An automated run compared against Alpha yields:

```
automation_compatible: False
failure_reasons: ["firm automation stance is semi_automation_only; this run is automated"]
```

A manual run (`is_automated=False`) is compatible. Both asserted by test.

This is the one profile where a rule actively *disqualifies* the system's
intended mode of operation, which is why it is registered — a comparison set
containing only permissive firms would make the automation constraint invisible.

## ❌ UNRESOLVED

Everything else: `initial_balance`, `profit_target`, all MLL fields, position
limits, `api_available`. Account economics were not supplied and were not
verified.

## Why it is not the primary target

`PRIMARY_AUTOMATION_TARGET = "topstep"`. Selecting a firm that reportedly
prohibits bots as the target for an automated system would guarantee a rules
breach, and a breach voids an account no matter how the strategy performs.
