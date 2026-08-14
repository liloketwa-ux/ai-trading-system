"""Feature registry: what each feature is, and what it needs.

A registry entry is the contract for a feature. It names the inputs, the
availability rule, and the calculation version, so a research run can record
exactly which definitions produced its numbers.

Versions are immutable. Changing a calculation means registering a new version,
never editing an existing one -- silently redefining ``atr:v1`` retroactively
changes the meaning of every result that ever cited it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..storage.quality import AvailabilityRule
from .contract import Domain, FeatureStatus

__all__ = ["FeatureSpec", "FeatureRegistry", "REGISTRY"]


@dataclass(frozen=True)
class FeatureSpec:
    """Definition of one versioned feature."""

    feature_name: str
    description: str
    domain: Domain
    calculation_version: str = "1"
    instrument_type: tuple[str, ...] = ("futures",)
    timeframes: tuple[str, ...] = ()
    required_inputs: tuple[str, ...] = ()
    availability_rule: AvailabilityRule = AvailabilityRule.BAR_CLOSE
    source: str = "computed"
    status: FeatureStatus = FeatureStatus.IMPLEMENTED
    parameters: dict = field(default_factory=dict)
    notes: str = ""

    @property
    def key(self) -> str:
        return f"{self.feature_name}:v{self.calculation_version}"


class FeatureRegistry:
    """Immutable-by-version catalogue of feature definitions."""

    def __init__(self) -> None:
        self._specs: dict[str, FeatureSpec] = {}

    def register(self, spec: FeatureSpec) -> FeatureSpec:
        existing = self._specs.get(spec.key)
        if existing is not None and existing != spec:
            raise ValueError(
                f"{spec.key} is already registered with a different definition. "
                "Feature versions are immutable -- register a new version instead "
                "of redefining an existing one."
            )
        self._specs[spec.key] = spec
        return spec

    def get(self, key: str) -> FeatureSpec | None:
        return self._specs.get(key)

    def require(self, key: str) -> FeatureSpec:
        spec = self._specs.get(key)
        if spec is None:
            raise KeyError(f"unregistered feature {key!r}")
        return spec

    def versions_of(self, feature_name: str) -> list[str]:
        return sorted(
            s.calculation_version for s in self._specs.values()
            if s.feature_name == feature_name
        )

    def by_domain(self, domain: Domain) -> list[FeatureSpec]:
        return sorted(
            (s for s in self._specs.values() if s.domain is domain),
            key=lambda s: s.key,
        )

    def by_status(self, status: FeatureStatus) -> list[FeatureSpec]:
        return [s for s in self._specs.values() if s.status is status]

    def implemented(self) -> list[FeatureSpec]:
        return self.by_status(FeatureStatus.IMPLEMENTED)

    def all(self) -> list[FeatureSpec]:
        return sorted(self._specs.values(), key=lambda s: s.key)

    def __len__(self) -> int:
        return len(self._specs)

    def __contains__(self, key: str) -> bool:
        return key in self._specs


REGISTRY = FeatureRegistry()

_BARS = ("ohlcv",)
_TF = ("5m", "15m", "1h", "4h", "1d")


def _reg(name, desc, domain, **kw):
    return REGISTRY.register(
        FeatureSpec(name, desc, domain, required_inputs=kw.pop("inputs", _BARS),
                    timeframes=kw.pop("timeframes", _TF), **kw)
    )


# -- price / volatility ----------------------------------------------------
_reg("true_range", "max(h-l, |h-prev_close|, |l-prev_close|)", Domain.VOLATILITY)
_reg("atr", "Wilder-smoothed average true range", Domain.VOLATILITY,
     parameters={"window": 14})
_reg("realized_volatility", "stdev of log returns, annualized", Domain.VOLATILITY,
     parameters={"window": 20})
_reg("rolling_volatility", "stdev of simple returns over a window", Domain.VOLATILITY,
     parameters={"window": 20})
_reg("bar_return", "close-to-close simple return", Domain.PRICE)
_reg("gap", "this bar's open versus the previous close", Domain.PRICE)
_reg("range_expansion", "bar range relative to its trailing average", Domain.VOLATILITY,
     parameters={"window": 20})

# -- market structure ------------------------------------------------------
_reg("swing_high", "confirmed pivot high (needs right-side bars)", Domain.MARKET_STRUCTURE,
     parameters={"left": 2, "right": 2})
_reg("swing_low", "confirmed pivot low (needs right-side bars)", Domain.MARKET_STRUCTURE,
     parameters={"left": 2, "right": 2})
_reg("structure_state", "HH/HL/LH/LL classification of the last two pivots",
     Domain.MARKET_STRUCTURE)
_reg("trend_state", "up/down/range from confirmed pivot sequence", Domain.MARKET_STRUCTURE)
_reg("break_of_structure", "close beyond the last confirmed opposing pivot",
     Domain.MARKET_STRUCTURE)
_reg("displacement", "bar range in ATR units -- objective magnitude only",
     Domain.MARKET_STRUCTURE, parameters={"atr_window": 14})

# -- session ---------------------------------------------------------------
_reg("session_vwap", "volume-weighted average price within a session",
     Domain.SESSION, availability_rule=AvailabilityRule.INTRABAR,
     timeframes=("5m", "15m", "1h"))
_reg("vwap_distance", "close relative to session VWAP, in percent", Domain.SESSION,
     availability_rule=AvailabilityRule.INTRABAR, timeframes=("5m", "15m", "1h"))
_reg("vwap_slope", "change in session VWAP over N bars", Domain.SESSION,
     availability_rule=AvailabilityRule.INTRABAR, timeframes=("5m", "15m", "1h"))
_reg("session_high", "highest price so far in the current session", Domain.SESSION,
     availability_rule=AvailabilityRule.INTRABAR)
_reg("session_low", "lowest price so far in the current session", Domain.SESSION,
     availability_rule=AvailabilityRule.INTRABAR)

# -- previous-period levels (session-close availability) -------------------
for _period in ("day", "week"):
    for _level in ("high", "low", "open", "close"):
        _reg(f"prev_{_period}_{_level}", f"previous {_period} {_level}",
             Domain.LIQUIDITY, availability_rule=AvailabilityRule.SESSION_CLOSE)

# -- liquidity references --------------------------------------------------
_reg("prior_swing_highs", "confirmed pivot highs as candidate liquidity references",
     Domain.LIQUIDITY)
_reg("prior_swing_lows", "confirmed pivot lows as candidate liquidity references",
     Domain.LIQUIDITY)
_reg("equal_highs", "clustered pivot highs within a tolerance", Domain.LIQUIDITY,
     parameters={"tolerance_pct": 0.0005})
_reg("equal_lows", "clustered pivot lows within a tolerance", Domain.LIQUIDITY,
     parameters={"tolerance_pct": 0.0005})

# -- derivatives -----------------------------------------------------------
_reg("funding_rate", "perpetual funding rate", Domain.DERIVATIVES,
     inputs=("funding",), availability_rule=AvailabilityRule.INPUT_MAX, source="exchange")
_reg("open_interest", "open contracts", Domain.DERIVATIVES, inputs=("open_interest",),
     availability_rule=AvailabilityRule.INPUT_MAX, source="exchange")
_reg("mark_price", "venue mark price", Domain.DERIVATIVES, inputs=("mark",),
     availability_rule=AvailabilityRule.INPUT_MAX, source="exchange")
_reg("index_price", "underlying index price", Domain.DERIVATIVES, inputs=("index",),
     availability_rule=AvailabilityRule.INPUT_MAX, source="exchange")
_reg("basis", "(mark - index) / index", Domain.DERIVATIVES, inputs=("mark", "index"),
     availability_rule=AvailabilityRule.INPUT_MAX, source="exchange")

# -- microstructure: requires book data we do not currently have -----------
for _name, _desc in [
    ("bid_ask_spread", "best ask minus best bid"),
    ("mid_price", "midpoint of best bid and ask"),
    ("top_of_book_imbalance", "best bid size versus best ask size"),
    ("depth_imbalance", "aggregate bid depth versus ask depth"),
    ("order_book_depth", "total resting size to N levels"),
    ("trade_imbalance", "buy-initiated versus sell-initiated volume"),
]:
    _reg(_name, _desc, Domain.MICROSTRUCTURE, inputs=("orderbook",),
         status=FeatureStatus.UNAVAILABLE, source="exchange",
         notes="requires order-book/tick data; must never be synthesized from candles")

# -- reserved: Solana / sentiment (interfaces only, Phase 4 defers) --------
for _name, _desc in [
    ("token_age", "time since token creation"),
    ("trade_acceleration", "trade-count rate of change"),
    ("buy_sell_imbalance", "buy versus sell volume"),
    ("unique_trader_acceleration", "unique-trader rate of change"),
    ("liquidity_change", "pool liquidity rate of change"),
    ("holder_growth", "holder-count rate of change"),
    ("wallet_activity", "activity of tracked wallets"),
]:
    _reg(_name, _desc, Domain.ON_CHAIN, inputs=("solana_trade",),
         instrument_type=("solana_token",), timeframes=(),
         status=FeatureStatus.RESERVED, source="pumpi",
         notes="interface reserved; calculation deferred beyond Phase 4")
