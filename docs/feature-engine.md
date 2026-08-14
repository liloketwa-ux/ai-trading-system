# Feature Engine

Phase 4. Deterministic, point-in-time features with temporal provenance.

Every feature is a `FeatureSnapshot` carrying `feature_name`, `value`,
`event_time`, `available_at`, `source`, `feature_version`, `instrument`,
`timeframe`, `inputs`, `provenance_id`, `derived_from`, `data_quality` and
`availability_rule`. A feature cannot exist without temporal provenance.

## The two rules that matter

**1. Availability is never assigned by hand.** Derived features go through
`derive_feature()`, which sets `available_at = max(input.available_at)`.
Declaring anything earlier raises `TemporalIntegrityError`. A feature may be
knowable *later* than its inputs — a session-close level, a batch computation —
and that later timestamp is modelled explicitly via `AvailabilityRule`.

**2. Inputs come only from the store.** Feature functions take a
`decision_time` and read through `store.query()` / `reconstruct_state()` /
`latest_completed_bar()`. None of them accept a DataFrame and index off the
end. `df.iloc[-1]` is exactly how an unclosed higher-timeframe bar gets consumed
as though it were complete.

## Feature catalog

48 registered definitions: **35 implemented**, **7 reserved**, **6 unavailable**.

### Volatility

| Feature | Version | Formula |
|---|---|---|
| `true_range` | v1 | `max(h−l, |h−prev_close|, |l−prev_close|)` |
| `atr` | v1 | Wilder smoothing of true range, `α = 1/window` (default 14) |
| `realized_volatility` | v1 | `stdev(log returns) × √periods_per_year` |
| `rolling_volatility` | v1 | `stdev(simple returns)` over a window |
| `range_expansion` | v1 | latest bar range ÷ trailing mean range |

### Price

| Feature | Version | Formula |
|---|---|---|
| `bar_return` | v1 | `close_t / close_{t−1} − 1` |
| `gap` | v1 | `open_t / close_{t−1} − 1` |

### Market structure — objective observations only

| Feature | Version | Definition |
|---|---|---|
| `swing_high` / `swing_low` | v1 | pivot strictly beyond `left` and `right` neighbours |
| `structure_state` | v1 | HH/LH and HL/LL from the last two confirmed pivots |
| `trend_state` | v1 | `up` / `down` / `range` from the pivot sequence |
| `break_of_structure` | v1 | close beyond the last confirmed opposing pivot |
| `displacement` | v1 | bar range ÷ ATR — **magnitude only, no interpretation** |

No ICT entry logic, no "smart money" labelling. These are measurements.

### Session (timezone-aware, versioned)

| Feature | Version | Availability |
|---|---|---|
| `session_vwap` | v1 | `INTRABAR` — evolves as the session proceeds |
| `vwap_distance` | v1 | `INTRABAR` |
| `vwap_slope` | v1 | `INTRABAR` |
| `session_high` / `session_low` | v1 | `INTRABAR` |

VWAP uses typical price `(h+l+c)/3` weighted by volume. Bars with **missing**
volume are excluded and the result is marked `STALE` — never weighted as zero.

Sessions: `asia:v1` (Asia/Tokyo 09:00–15:00), `london:v1` (Europe/London
08:00–16:30), `new_york:v1` (America/New_York 09:30–16:00), `cme_equity:v1`
(America/Chicago 17:00–16:00 next day). All DST-aware via `zoneinfo`.

### Previous-period levels — `SESSION_CLOSE` availability

`prev_day_{high,low,open,close}`, `prev_week_{high,low,open,close}`.

**The critical rule:** a previous-period level is available only once that
period's session has actually closed. Using the current session's running high
as "the day's high" during that same session is look-ahead.

### Liquidity references

`prior_swing_highs`, `prior_swing_lows`, `equal_highs`, `equal_lows`, plus the
previous-period levels above. These are **candidate liquidity references** —
prices where resting orders plausibly sit — not signals.

### Derivatives

`funding_rate`, `open_interest`, `mark_price`, `index_price`, `basis`.

Each carries capability provenance: `native`, `emulated`, `unsupported`,
`unknown`. An emulated funding rate is derived by CCXT from other endpoints, not
reported by the venue. Research may demand `native_only=True`; mixing the two
produces a series whose meaning changes partway through.

`basis = (mark − index) / index`, available only once **both** inputs are.

### Microstructure — UNAVAILABLE

`bid_ask_spread`, `mid_price`, `top_of_book_imbalance`, `depth_imbalance`,
`order_book_depth`, `trade_imbalance`.

All six return `DataQuality.UNAVAILABLE`. They require order-book or tick data
this system does not persist. **They are never synthesized from candles** — a
high-minus-low "spread proxy" is undetectable downstream and wrong in exactly
the conditions that matter, when real spreads widen.

### Reserved — Solana (interfaces only)

`token_age`, `trade_acceleration`, `buy_sell_imbalance`,
`unique_trader_acceleration`, `liquidity_change`, `holder_growth`,
`wallet_activity`. Registered as `RESERVED`. No calculation, no scores.

## Data quality semantics

| Quality | Meaning | `usable` |
|---|---|---|
| `OK` | computed normally | yes |
| `ZERO` | genuinely observed as zero | yes |
| `STALE` | last known value, degraded | no (but `has_value`) |
| `MISSING` | input never received | no |
| `UNAVAILABLE` | source cannot provide it | no |
| `NOT_APPLICABLE` | meaningless here (e.g. VWAP outside a session) | no |

**Missing volume is not zero volume.** A feature returns `value=None` with a
quality, never a fabricated number. `is_eligible_at()` requires *both*
availability and usable quality — a value that arrived on time but is `MISSING`
is still refused.

## Feature versioning

Keys are `name:vN` — `atr:v1`, `session_vwap:v1`. The registry refuses to
redefine an existing version with different content; a calculation change
requires a new version. Silently redefining `atr:v1` retroactively changes the
meaning of every result that cited it.

## Source latency

`available_at` for a bar is its **close** plus the source's modelled latency.
Latency confidence is `MEASURED`, `DECLARED` or `UNVERIFIED`. Everything ships
`UNVERIFIED` — nothing has been measured, because the network is unavailable.
Assuming zero lag is the optimistic error, and optimistic availability errors
are look-ahead.

## Limitations

- **No feature has run against real market data.** All verification is against
  synthetic fixtures.
- **All source latency is `UNVERIFIED`.** Solana indexer lag in particular is
  assumed zero and is certainly not.
- **Microstructure is entirely unavailable** pending order-book persistence.
- `rolling_volatility`, `session_high`, `session_low`, `vwap_slope`,
  `equal_highs`, `equal_lows` are registered but not yet implemented as
  point-in-time functions — the legacy vectorized `indicators.py` covers
  equivalent maths for the existing strategy path.
- Weekly previous-period levels are registered; only session-based (daily)
  levels have a point-in-time implementation.
