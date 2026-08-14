# Temporal Integrity

The hard invariant of this system:

> **A decision made at time `D` may use an observation only if
> `available_at <= D`.**

Not `event_time <= D`. The distinction is the whole point, and conflating them
is the most common way a research pipeline produces a number that cannot be
reproduced in production.

## Why event time is not enough

A liquidity figure describing a pool *as it stood on Monday*, fetched on
Friday, has `event_time = Monday`. Joining it into Monday's feature row looks
correct — the timestamps match. It is still look-ahead: on Monday nobody had it.

The Phase 1 audit found this exact hazard live in Pumpi, which mutates token
rows in place during enrichment with no `retrievedAt`. Those rows cannot be
reconstructed as they appeared at decision time, so they are barred from
research use until snapshots become append-only.

## The three timestamps

| Field | Meaning |
|---|---|
| `event_time` | when the underlying thing happened |
| `available_at` | when a decision could **first** have used it |
| `ingested_at` | when we wrote it down |

For an OHLCV bar, `available_at` is the bar's **close**, never its open. A bar
opening at 00:00 on a 1h timeframe is knowable at 01:00; treating the open as
availability leaks the entire bar.

## Unknown availability fails closed

Availability is tri-valued, not merely present or absent:

```python
Availability.KNOWN     # available_at is established
Availability.UNKNOWN   # UNKNOWN_AVAILABILITY — excluded from research
```

A record whose availability cannot be established is **never silently treated as
usable**. `store.query()` excludes it always, and under the default `strict=True`
its mere presence raises `UnknownAvailabilityError` — so a backtest stops rather
than quietly running on a filtered subset it did not know had been filtered.

Resolving availability creates a **new** record via `with_availability()`; it
never mutates the original.

## Central enforcement

The filter lives in `ObservationStore.query()` and nowhere else. Every read path
routes through it. A per-call-site filter is one forgotten predicate away from
silent leakage, and there is no realistic review process that catches a missing
`where` clause in the tenth notebook.

```python
store.reconstruct_state(decision_time, key)   # the world as knowable at D
store.latest(decision_time, key, kind)        # one kind
store.query(decision_time, key=..., strict=True)
```

## Derived features inherit the latest input's availability

A feature computed from several inputs is knowable only once the **last** of
them is, so its `available_at` is the maximum over its inputs. Declaring
anything earlier is refused:

```python
derive_feature("combo", [bar, liquidity], compute, available_at=too_early)
# TemporalIntegrityError: declared available_at precedes its inputs' availability
```

Chained derivations propagate the bound transitively. An input with unknown
availability makes the whole derivation illegal rather than merely suspect.

## Restatements

Two observations may share an `event_time` and differ in `available_at` — a
source correcting itself. Both are legitimate, and which to use depends on the
question:

| Policy | Answers | Use for |
|---|---|---|
| `LATEST_KNOWN` (default) | "what did we believe about that instant, as of now" | most research |
| `FIRST_KNOWN` | "what did a live system act on" | modelling systems that never revisit |

`LATEST_KNOWN` is **not** look-ahead: at the decision time in question, the
correction genuinely was known. Before it arrives it remains invisible, which is
the property that matters.

This was a real design correction during Phase 3 — the first implementation
preferred first-knowledge permanently, which meant a later decision could never
see a correction it demonstrably had.

## The append-only guarantee

`ObservationStore` exposes `append()` and no update or delete. Re-appending an
identical record is idempotent (ingestion retries are expected); appending
*different* content under an existing provenance id raises, because that is
corruption rather than a retry.

Enrichment appends a new observation with a later `available_at`. It never
overwrites. That is what makes point-in-time reconstruction possible at all.

## The attack suite

`tests/test_temporal_integrity.py` — 26 tests written as attacks. Each appends
data that arrives later and asserts the earlier reconstructed state is untouched:

1. later enrichment cannot alter earlier state
2. late-arriving social data cannot leak backward
3. future news is absent from earlier market state
4. future wallet activity absent from earlier wallet intelligence
5. later liquidity snapshots do not alter earlier liquidity
6. later holder counts do not alter earlier features
7. candle availability equals its close, not its open
8. derived features cannot precede their inputs' availability
9. merged views keep the latest *valid* record only
10. queries fail closed when provenance is missing

Plus append-only enforcement and restatement-policy coverage.
