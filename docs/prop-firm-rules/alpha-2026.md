# Alpha Futures — ruleset `2026.08`

**Research comparison only. Explicitly NOT a live-automation target.**

> **Verification status: `UNVERIFIED`.** Alpha Futures was not covered by the
> 2026-08-15 official source review. `alphafutures.com` remains unreachable from
> this environment. `retrieved_at: null`.

The instruction was to keep the current semi-automation classification until all
remaining rules are verified. It stands unchanged, and it stands as an
*operator statement*, not an official-source attestation.

## Automation policy (operator-supplied, NOT verified)

| Aspect | Status |
|---|---|
| `full_automation` | **prohibited** |
| AI / bots | **prohibited** |
| `semi_automation` | allowed, subject to manual execution |

Encoded as `AutomationStance.SEMI_ONLY`, so `permits_full_automation` returns
`False`. The stance is `USER_SUPPLIED`: `stance.is_verified` is `False`,
asserted by test.

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
limits, daily loss limit, `api_available`, session boundary. Account economics
were not supplied and were not verified.

This is the only profile in the registry at `VerificationLevel.UNVERIFIED` —
every rule backing a decision is unresolved.

## Why it is not the primary target

`PRIMARY_AUTOMATION_TARGET = "topstep"`. Selecting a firm that reportedly
prohibits bots as the target for an automated system would guarantee a rules
breach, and a breach voids an account no matter how the strategy performs.
