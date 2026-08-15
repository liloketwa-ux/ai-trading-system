# Prop-Firm Rulesets

## ⚠️ Verification status of this entire directory: UNVERIFIED

**Not one rule in this build was read from a firm's official documentation.**

Network access to every firm source is blocked in this environment. Measured
during Phase 8 implementation:

| Source | Result |
|---|---|
| `www.topstep.com` | `connect_rejected` |
| `help.topstep.com` | `connect_rejected` |
| `apextraderfunding.com` | `connect_rejected` |
| `myfundedfutures.com` | `connect_rejected` |
| `alphafutures.com` | `connect_rejected` |
| `gateway.projectx.com` | `connect_rejected` |

Every value is therefore `USER_SUPPLIED` (stated by the operator, not checked)
or `UNKNOWN` (never established). **`REGISTRY.adjudication_ready()` returns an
empty list**, and that is asserted by test.

This is the instructed behaviour under rule #20, not a limitation being worked
around. A plausible number recorded as fact is worse than an explicit gap: the
gap gets filled, the fact does not get re-checked.

## How verification works

```python
RuleValue(value, status, source, label)
```

| Status | May back a compliance claim |
|---|---|
| `VERIFIED_OFFICIAL` | ✅ |
| `NOT_APPLICABLE` | ✅ (a fact, not a gap) |
| `USER_SUPPLIED` | ❌ |
| `THIRD_PARTY` | ❌ |
| `UNKNOWN` | ❌ |

`rule.get()` returns the value for display. `rule.require()` **raises**
`UnverifiedRuleError` unless the status is sufficient. Construction refuses to
attach a value to an `UNKNOWN` status at all.

`THIRD_PARTY` is excluded deliberately — third-party articles are the largest
source of stale prop-firm numbers and read as authoritative.

## Registry

`firm_id / program_id / account_size @ ruleset_version`

Published rulesets are immutable; a firm rule change means a new
`ruleset_version`. Each profile records `effective_from`, `source_url`,
`retrieved_at` and `verification_status`.

Nine profiles: Topstep ×3, Apex ×4, MFFU ×1, Alpha ×1.

## Comparison

`compare_strategy_across_firms(run, profiles)` returns one outcome **per firm**,
never aggregated — a single cross-firm score describes no firm that exists.

Where a rule is unverified, the outcome reports the **measurement** and sets
`passed = None` with `decidable = False`. Measurements are always available;
only the verdict is withheld.

## Documents

- [`topstep-2026.md`](topstep-2026.md) — primary automation target
- [`apex-eod-2026.md`](apex-eod-2026.md)
- [`mffu-rapid-2026.md`](mffu-rapid-2026.md)
- [`alpha-2026.md`](alpha-2026.md)

## Before any of this can be used

1. Reach each firm's official current documentation.
2. Replace `user_supplied(...)` with `verified(value, url, retrieved_at)`.
3. Resolve every `UNKNOWN`, especially Topstep's MLL calculation.
4. Publish a new `ruleset_version` with a real `retrieved_at`.
5. Re-run the suite — `adjudication_ready()` becoming non-empty is the signal.
