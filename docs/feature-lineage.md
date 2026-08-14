# Feature Lineage

Every production feature traces back to raw observations, and every step in the
chain carries the timestamps that make the trace checkable.

## The chain

```
RAW OBSERVATION
  exchange REST / Solana RPC log / news feed
  ── event_time, retrieved_at, source, raw_ref
        │
        ▼
NORMALIZED OBSERVATION            ai_trading.storage.Observation
  chain-neutral names, provenance_id, schema_version
  ── available_at assigned here (bar close + source latency)
        │
        ▼
FEATURE INPUT                     store.query(decision_time, ...)
  central point-in-time filter: available_at <= decision_time
  ── unknown availability fails closed
        │
        ▼
DERIVED FEATURE                   derive_feature(...)  -> FeatureSnapshot
  available_at = max(input.available_at)
  ── inputs, derived_from, feature_version, data_quality
        │
        ▼
FUTURE STRATEGY SIGNAL            [Phase 5 — not built]
  consumes only features eligible at the decision time
```

## Worked example: `atr:v1` on ES 1h

```
1. RAW          CCXT fetch_ohlcv("ES", "1h")
                bar open 10:00, values [o,h,l,c,v]

2. NORMALIZED   Observation(
                    key="ES", kind="ohlcv", timeframe="1h",
                    event_time  = 10:00,     # bar open
                    available_at= 11:00,     # bar CLOSE + latency
                    source="ccxt:binanceusdm",
                    provenance_id=sha256(...)[:32])

3. INPUT        completed_bars(store, "ES", "1h", decision_time=15:00)
                -> bars with available_at <= 15:00
                -> the 14:00 bar (closes 15:00) is included;
                   the 15:00 bar (closes 16:00) is NOT

4. DERIVED      derive_feature("atr", bars, wilder_smooth)
                available_at = max(bar.available_at) = 15:00
                inputs       = (provenance_id of each bar,)
                feature_version = "1"
                data_quality = OK

5. SIGNAL       [Phase 5]
```

Step 3 is where the leak would be. `df.iloc[-1]` returns the 15:00 bar, which
does not close until 16:00 — an hour of future information, invisible in the
output and fatal in the result.

## Worked example: `prev_london_high`

```
1. RAW/NORM     bars across the London session of 15 Jan
2. SESSION      LONDON.previous_completed(decision_time)
                -> window 15 Jan 08:00–16:30 London, in UTC
3. DERIVED      max(high) over bars inside that window
                available_at = max(window.end, max(input.available_at))
                availability_rule = SESSION_CLOSE
```

The `max(window.end, ...)` is the guard. Yesterday's high is not knowable until
yesterday's session actually ended, so even if every input bar were somehow
available sooner, the level is not.

## Provenance fields by layer

| Layer | Identity | Time fields |
|---|---|---|
| Raw | `raw_ref` (tx sig, response id) | `event_time`, `retrieved_at` |
| Normalized | `provenance_id`, `schema_version`, `parser_version` | + `available_at`, `ingested_at` |
| Feature | `provenance_id`, `feature_version` | + `availability_rule` |
| Dataset | `dataset_id`, `checksum` | `as_of` |

`FeatureSnapshot.inputs` and `.derived_from` hold the provenance ids of the
observations consumed, so a feature value can be walked back to the exact rows
that produced it.

## Lineage for the Solana path

```
Pumpi TradeEvent (ethAmount, priceEth)      <- legacy EVM naming
        │  normalize_pumpi_trade()          <- rename happens ONCE, here
        ▼
SolanaTokenEvent (quote_amount, quote_price, quote_asset="SOL")
  raw{} preserves original field names for debugging
        │  .to_observation(ingested_at, available_at)
        ▼
Observation(kind="solana_trade", source="pumpi:pumpfun", raw_ref=tx_sig)
        │
        ▼
[RESERVED — no Solana feature calculations in Phase 4]
```

`available_at` for Solana events currently defaults to `event_time`, i.e. an
indexer lag of zero. This is **unverified** and recorded as such in
`DEFAULT_LATENCY`; it must be replaced with a measured figure before any Solana
feature is used in research.

## Verifying lineage

```python
snapshot = futures.atr(store, "ES", "1h", decision_time)
snapshot.inputs              # provenance ids of every bar consumed
snapshot.available_at        # never earlier than any input's
snapshot.data_quality        # why the value is what it is
snapshot.key                 # "atr:v1"
REGISTRY.require("atr:v1")   # the definition that produced it
```
