# Data Lineage

Every observation in the store carries enough provenance to answer: where did
this come from, when was it true, when could we have known it, and which code
produced it.

## The record

```python
Observation(
    key, kind,                    # instrument/token, observation family
    event_time, available_at,     # see temporal-integrity.md
    ingested_at, source,
    value,                        # payload
    schema_version,               # shape of `value`
    dataset_version,              # frozen dataset membership
    provenance_id,                # content-addressed identity
    raw_ref,                      # tx signature / response id
    timeframe,                    # bar size where applicable
    derived_from,                 # provenance ids of inputs
)
```

`provenance_id` is a SHA-256 over the identifying fields, so identity is
content-addressed: the same observation ingested twice gets the same id and
deduplicates, while different content under the same id is rejected as
corruption.

## Kinds

`ohlcv`, `liquidity`, `holders`, `social`, `news`, `wallet`, `solana_trade`,
`feature:<name>`. Kinds are open — new sources add new kinds rather than
widening an existing schema.

## Dataset versions

A research run references **exactly one** dataset version. Without that, a
result is unreproducible: "we ran it on the BTC data" is uncheckable a year
later, because the store has been appended to since.

```python
version = build_dataset_version(store, dataset_id="btc-1h", as_of=cutoff)
version.checksum   # SHA-256 over sorted member provenance ids
version.verify(store)
```

A dataset version is itself point-in-time: only observations available by
`as_of` are members, so rebuilding it later with the same `as_of` yields the
same checksum even after the store has grown. Verified by test.

Records with unknown availability are excluded unless explicitly included, and
including them makes the version unusable for point-in-time research.

## Storage backends

| Backend | Use |
|---|---|
| `InMemoryStore` | tests, small studies; fully deterministic |
| `ParquetStore` | durable; one immutable file per append batch, never rewritten |

Batch files are append-only on disk as well as in the API.

## Solana lineage and the quote-asset rename

Pumpi emits `ethAmount`, `priceEth`, `marketCapEth`, `virtualEthReserves` on a
**Solana** system where the quote asset is SOL — a legacy leak from an EVM
ancestor. Left alone it mislabels every downstream price and market cap, and the
numbers stay plausible enough to survive review.

The rename happens at the boundary in `solana/events.py` and nowhere else:

| Pumpi | Normalized |
|---|---|
| `ethAmount` | `quote_amount` |
| `priceEth` | `quote_price` |
| `marketCapEth` | `market_cap_quote` |
| `virtualEthReserves` | `virtual_quote_reserves` |

Raw field names are preserved in `SolanaTokenEvent.raw` for debugging, and
`quote_asset` records what the quote actually is rather than assuming. A test
asserts no `eth`-named attribute survives onto the normalized event.

Trade-level provenance is preserved end to end: transaction signature, trader
address, platform, slot, and `parser_version`.

## Adapter lifecycle

Source code existing is not the same as an adapter working.

```
DISABLED < PRESENT < UNIT_TESTED < RUNTIME_VERIFIED
        < HISTORICALLY_VALIDATED < PRODUCTION_ENABLED
```

Only `HISTORICALLY_VALIDATED` and above are `usable_for_research`. Promotion
requires stated evidence and cannot skip levels — an adapter cannot be
historically validated without first having been seen to run.

Current state, per the Phase 1 audit and encoded in `ADAPTER_REGISTRY`:

| Adapter | State | Note |
|---|---|---|
| `raydium_launchlab` | UNIT_TESTED | started; `launchlabDecode` has tests |
| `pumpfun`, `pumpswap` | PRESENT | started; decode unverified here |
| `raydium_amm`, `meteora`, `orca`, `moonshot`, `letsbonk` | PRESENT | **not started** by Pumpi's registry |

**No adapter currently qualifies as research-usable**, asserted by test. Nothing
can quietly promote itself by having a file on disk.
