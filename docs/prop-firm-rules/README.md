# Prop-Firm Rulesets

Ruleset version **`2026.08`**, verified **2026-08-15**.

## Verification status

The rules in this build were **reviewed by the operator against each firm's
official current documentation, outside this coding environment**, and attested
with a source URL, document title and review date. Nothing here was fetched by
this code — network access to every firm source is still blocked in this
container:

| Source | Result |
|---|---|
| `help.topstep.com` | `connect_rejected` |
| `apextraderfunding.com` | `connect_rejected` |
| `myfundedfutures.com` | `connect_rejected` |
| `alphafutures.com` | `connect_rejected` |

That distinction is preserved in the record rather than papered over.
`OFFICIAL_SOURCE_VERIFIED` means *a person read the firm's page and attested to
the value*; `VERIFIED_OFFICIAL` means *this code fetched the page*. Both are
sufficient to back a compliance decision. Only the second can be re-derived
automatically, and no field in this build claims it — asserted by test.

**The verification architecture is unchanged.** `RuleValue.require()` still
fails closed, and every field the review did not cover is still `UNKNOWN` and
still refuses. What changed is the inputs, not the gate.

## How verification works

```python
RuleValue(value, status, source, label)
```

| Status | May back a compliance claim | Meaning |
|---|---|---|
| `OFFICIAL_SOURCE_VERIFIED` | ✅ | a person read the firm's official page |
| `VERIFIED_OFFICIAL` | ✅ | this code fetched the firm's page |
| `NOT_APPLICABLE` | ✅ | the rule does not exist for this program |
| `USER_SUPPLIED` | ❌ | stated, not checked |
| `THIRD_PARTY` | ❌ | an article, not the firm |
| `UNKNOWN` | ❌ | never established |

`rule.get()` returns the value for display. `rule.require()` **raises**
`UnverifiedRuleError` unless the status is sufficient. Construction refuses to
attach a value to an `UNKNOWN` status at all.

### `NOT_APPLICABLE` is not `UNKNOWN`

A Trading Combine has no daily loss limit; a Rapid evaluation has none at any
size; an Express Funded account has no evaluation profit target. Those are
facts, and a fully specified ruleset must not be blocked by them. `UNKNOWN` is
reserved for a rule that exists and was not established — Apex's daily loss
limit, which the source confirms is enforced but whose amount was not verified.

The two are opposite states with opposite consequences, and the code treats them
that way: `NOT_APPLICABLE` satisfies readiness and refuses to produce a value;
`UNKNOWN` blocks readiness.

### Per-field provenance

Every rule — verified or not — emits a `FieldProvenance` record carrying
`field_name`, `value`, `status`, `source_url`, `source_title`, `retrieved_at`,
`verified_at`, `verification_method` and `ruleset_version`. An audit trail that
listed only the sourced fields could not answer the question it exists for.

## The ruleset address space

```
PropFirm → Program → Stage → Account Size → Ruleset Version
```

There is no such thing as "Topstep's drawdown". There is the Trading Combine's,
at the evaluation stage, for a $50K account, as of ruleset `2026.08`. Every
lookup names all five:

```python
REGISTRY.resolve("topstep", "trading_combine", Stage.EVALUATION, 50_000)
```

Omit the version and you get the latest; pass `as_of=<date>` and you get the
ruleset that was in force then, because a rule published in September does not
retroactively govern an evaluation traded in July. Published versions are
immutable — a rule change is a new version, never an edit.

**25 profiles**: Topstep Trading Combine ×3, Express Funded Standard ×3, Express
Funded Consistency ×3, Live Funded ×3; Apex EOD PA ×4; MFFU Rapid evaluation ×4
and funded ×4; Alpha ×1.

## Readiness is per capability

A profile that is 90% verified is not "verified with caveats" — it will
adjudicate confidently right up until it touches the unverified rule. So
readiness is reported three ways (`ADJUDICATION_READY`, `PARTIALLY_VERIFIED`,
`UNVERIFIED`) and scoped to what each capability actually needs:

```python
profile.readiness(Capability.LOSS_LIMIT_TRACKING)      # ADJUDICATION_READY
profile.readiness(Capability.SESSION_BOUNDARY_ENFORCEMENT)  # UNVERIFIED
profile.missing_for(Capability.FULL_ADJUDICATION)
```

Requiring every field would refuse a loss-limit tracker whose every input is
sourced because the payout cadence is not. Requiring none would let an
unverified session boundary silently decide when an end-of-day threshold
advances. Neither is acceptable, so the dependency is declared per capability.

### Current state

| Profile | Full adjudication | Loss-limit tracking |
|---|---|---|
| Topstep Trading Combine (50/100/150K) | `PARTIALLY_VERIFIED` | ✅ `ADJUDICATION_READY` |
| Topstep Express Funded (Std/Cons) | `PARTIALLY_VERIFIED` | ❌ MLL not verified |
| Topstep Live Funded | `PARTIALLY_VERIFIED` | ❌ nothing verified |
| Apex EOD PA (25–150K) | `PARTIALLY_VERIFIED` | ❌ lock behaviour unknown |
| MFFU Rapid evaluation | `PARTIALLY_VERIFIED` | ❌ enforcement clock unknown |
| MFFU Rapid funded | `PARTIALLY_VERIFIED` | ❌ nothing verified |
| Alpha Futures | `UNVERIFIED` | ❌ |

No profile is `ADJUDICATION_READY` overall. The Combine is one field short: the
trading-day boundary, which was explicitly excluded from the review pending
verification against Topstep's Permitted Products and Trading Hours.

## Loss limits

Two objects, deliberately not one.

**Maximum Loss Limit** — an eligibility rule. Breach it and the account is gone.
**Daily Loss Limit** — a risk control. Hit it and the platform flattens the book
and locks the session; the evaluation continues tomorrow.

Collapsing them produces a simulator that fails accounts the firm would merely
have paused. See [`topstep-2026.md`](topstep-2026.md) for the MLL's exact
semantics.

## Consistency

Missing the consistency guideline yields `CONSISTENCY_NOT_MET`, never
`RULE_VIOLATION`. It raises the profit target rather than ending the account:

```python
result = profile.consistency.evaluate(best_day_profit=2_000, total_profit=3_000)
result.outcome                  # EligibilityOutcome.CONSISTENCY_NOT_MET
result.required_total_profit    # 4_000.0
```

Reporting it as a failure would tell a trader their account is dead when it is
merely slower — and would make the simulator's pass rate wrong in the
pessimistic direction for exactly the strategies that produce one large winner.

## Comparison

`compare_strategy_across_firms(run, profiles)` returns one outcome **per firm**,
never aggregated — a single cross-firm score describes no firm that exists.

Where a rule is unverified, the outcome reports the **measurement** and sets
`passed = None` with `decidable = False`. Each outcome also carries
`eligibility` (`ELIGIBLE` / `CONSISTENCY_NOT_MET` / `RULE_VIOLATION` /
`UNDETERMINED`), `daily_loss_lockouts` (never failures) and
`not_applicable_rules`.

## Documents

- [`topstep-2026.md`](topstep-2026.md) — primary automation target
- [`apex-eod-2026.md`](apex-eod-2026.md)
- [`mffu-rapid-2026.md`](mffu-rapid-2026.md)
- [`alpha-2026.md`](alpha-2026.md)

## Still needing verification

1. **Topstep trading-day boundary** — the only thing between the Combine and
   full adjudication readiness.
2. **Topstep Express Funded risk rules** — MLL amounts, drawdown type, optional
   DLL, scaling plan, winning-day requirement.
3. **Topstep Live Funded** — everything.
4. **Apex** — MLL lock behaviour, DLL amounts, micro limits, automation policy,
   minimum trading days.
5. **MFFU** — whether the EOD maximum loss is also enforced intraday; contract
   limits; the entire funded stage.
6. **Alpha Futures** — everything; the semi-automation classification stands
   pending verification.

## No live execution

No firm API client ships. `FirmExecutionProvider` is an abstract interface with
no implementation, `LiveExecutionPrerequisites` starts with six unmet
conditions, and `ExecutionTopology` refuses any deployment that is not the
trader's own device — Topstep's verified policy prohibits VPS, VPN and
remote-server execution, which includes the container this code was written in.
