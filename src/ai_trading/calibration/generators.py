"""Synthetic data-generating processes with known truth.

Five datasets, each built to answer one question about the research machinery:

1. **Null** -- driftless, independent increments. Nothing to find. A system
   that finds something here is broken, and this is the only dataset where a
   positive result is unambiguously an error.
2. **Momentum** -- AR(1) returns with a positive coefficient. There is a real,
   exploitable relationship, and the system should recover it out of sample.
3. **Mean reversion** -- deviations beyond a threshold revert with a stated
   probability. Again real, and expressed differently, so a detector tuned to
   autocorrelation alone will miss it.
4. **Regime-dependent** -- the momentum coefficient is positive in one regime
   and zero or negative in another. Pooled over both, the effect is muted; the
   system must find the difference when it breaks results down by regime.
5. **Sub-cost** -- a genuine positive gross edge deliberately smaller than
   realistic costs. The hardest of the five, because the honest answer is "yes
   there is an effect, and no you cannot trade it", and a system that reports
   only statistical significance will get it wrong.

Every generator returns bars and a :class:`SealedTruth`. The bars carry no
reference to the parameters that made them.

The bars are shaped like futures data -- one instrument, one contract, a
declared session -- so they pass through the Phase 9 pipeline unmodified. They
are still synthetic, and :class:`~ai_trading.history.datasets.DataOrigin`
records that; nothing here may back a claim about a real market.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..history.availability import AvailabilityQuality, bar_close_availability
from ..history.providers import SCHEMA_VERSION, Bar
from .truth import EdgeKind, GroundTruth, SealedTruth

__all__ = [
    "CalibrationDataset", "generate_null", "generate_momentum",
    "generate_mean_reversion", "generate_regime_dependent",
    "generate_sub_cost", "ALL_GENERATORS", "SESSION_MINUTES",
]

UTC = timezone.utc
START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
BASE_PRICE = 20_000.0
#: Bars per synthetic session, used to place regime boundaries on day edges.
SESSION_MINUTES = 390

AVAILABILITY = bar_close_availability(
    "synthetic generator emits each bar at its own close; there is no feed and "
    "therefore no arrival timestamp to observe"
)


@dataclass(frozen=True)
class CalibrationDataset:
    """Bars plus the sealed answer, and the regime labels where relevant."""

    name: str
    bars: tuple[Bar, ...]
    truth: SealedTruth
    #: Regime label per bar index, for datasets that have regimes. Visible to
    #: research: a real system would observe the regime, it just would not know
    #: which regime carries the edge.
    regime_labels: tuple[str, ...] = ()

    def __len__(self) -> int:
        return len(self.bars)

    @property
    def closes(self) -> list[float]:
        return [b.close for b in self.bars]

    def split(self, fraction: float = 0.5) -> tuple["CalibrationDataset",
                                                    "CalibrationDataset"]:
        """Chronological in-sample / out-of-sample split.

        Chronological, never random: a shuffled split on autocorrelated returns
        puts neighbouring bars on both sides and leaks the very structure the
        test is trying to measure.
        """
        if not 0.0 < fraction < 1.0:
            raise ValueError("split fraction must be in (0, 1)")
        cut = int(len(self.bars) * fraction)
        labels = self.regime_labels
        return (
            CalibrationDataset(f"{self.name}-is", self.bars[:cut], self.truth,
                               labels[:cut] if labels else ()),
            CalibrationDataset(f"{self.name}-oos", self.bars[cut:], self.truth,
                               labels[cut:] if labels else ()),
        )


def _bar(index: int, price: float, previous: float, rng: random.Random,
         contract: str, timeframe: str, minutes: int) -> Bar:
    event = START + timedelta(minutes=minutes * index)
    high = max(price, previous) + abs(rng.gauss(0.0, 1.0))
    low = min(price, previous) - abs(rng.gauss(0.0, 1.0))
    return Bar(
        source="calibration_generator", event_time=event,
        available_at=AVAILABILITY.available_at(event_time=event),
        retrieved_at=datetime.now(UTC), schema_version=SCHEMA_VERSION,
        availability_quality=AvailabilityQuality.ASSUMED_BAR_CLOSE,
        instrument="SYN", contract=contract, timeframe=timeframe,
        open=round(previous, 4), high=round(high, 4), low=round(max(low, 0.01), 4),
        close=round(price, 4), volume=float(rng.randint(100, 2_000)),
    )


def _walk(returns: list[float], rng: random.Random, *, contract: str,
          timeframe: str, minutes: int) -> list[Bar]:
    """Turn a return series into bars."""
    bars: list[Bar] = []
    price = BASE_PRICE
    for index, ret in enumerate(returns):
        previous = price
        price = price * (1.0 + ret)
        bars.append(_bar(index, price, previous, rng, contract, timeframe, minutes))
    return bars


def generate_null(n: int = 6_000, *, seed: int = 11, sigma: float = 0.004,
                  contract: str = "SYNZ26", timeframe: str = "5m",
                  minutes: int = 5) -> CalibrationDataset:
    """Independent, zero-mean returns. Nothing to find."""
    rng = random.Random(seed)
    returns = [rng.gauss(0.0, sigma) for _ in range(n)]
    truth = GroundTruth(
        EdgeKind.NONE, effect_size=0.0, expected_gross_bps=0.0, seed=seed,
        note="iid normal returns, zero drift, no autocorrelation by construction",
    )
    return CalibrationDataset("null", tuple(_walk(returns, rng, contract=contract,
                                                  timeframe=timeframe,
                                                  minutes=minutes)),
                              SealedTruth(truth, "null"))


def generate_momentum(n: int = 6_000, *, seed: int = 21, phi: float = 0.25,
                      sigma: float = 0.004, contract: str = "SYNZ26",
                      timeframe: str = "5m",
                      minutes: int = 5) -> CalibrationDataset:
    """AR(1) returns: ``r_t = phi * r_{t-1} + eps``.

    ``phi`` is the whole edge. At 0.25 the relationship is well inside what a
    few thousand observations can resolve, and far below anything that would
    look plausible in a real market -- the dataset is a calibration target, not
    a simulation.
    """
    rng = random.Random(seed)
    returns: list[float] = [rng.gauss(0.0, sigma)]
    for _ in range(n - 1):
        returns.append(phi * returns[-1] + rng.gauss(0.0, sigma))

    #: With returns AR(1), conditioning on the sign of r_{t-1} gives an
    #: expected next return of phi * E[|r|] = phi * sigma * sqrt(2/pi).
    expected = phi * sigma * math.sqrt(2.0 / math.pi)
    truth = GroundTruth(
        EdgeKind.MOMENTUM, effect_size=phi,
        expected_gross_bps=expected * 10_000, seed=seed,
        note=f"AR(1) with phi={phi}; sign-conditioned expectancy "
             f"{expected * 10_000:.3f} bps per bar",
    )
    return CalibrationDataset("momentum",
                              tuple(_walk(returns, rng, contract=contract,
                                          timeframe=timeframe, minutes=minutes)),
                              SealedTruth(truth, "momentum"))


def generate_mean_reversion(n: int = 6_000, *, seed: int = 31,
                            reversal_probability: float = 0.62,
                            threshold_sigma: float = 1.5, lookback: int = 20,
                            sigma: float = 0.004, contract: str = "SYNZ26",
                            timeframe: str = "5m",
                            minutes: int = 5) -> CalibrationDataset:
    """Stretched moves revert with a stated probability.

    Expressed as a *probability*, not a coefficient, on purpose. A detector
    that only looks for autocorrelation in returns will find this dataset much
    weaker than it is, which is the point -- the machinery should not be tuned
    to one shape of edge.
    """
    rng = random.Random(seed)
    returns: list[float] = []
    prices: list[float] = [BASE_PRICE]

    for index in range(n):
        drift = 0.0
        if index >= lookback:
            window = prices[-lookback:]
            mean = sum(window) / len(window)
            variance = sum((p - mean) ** 2 for p in window) / len(window)
            deviation = prices[-1] - mean
            spread = math.sqrt(variance) if variance > 0 else 0.0
            if spread > 0 and abs(deviation) > threshold_sigma * spread:
                reverts = rng.random() < reversal_probability
                direction = -1.0 if deviation > 0 else 1.0
                drift = direction * sigma * (1.0 if reverts else -1.0)
        ret = drift + rng.gauss(0.0, sigma)
        returns.append(ret)
        prices.append(prices[-1] * (1.0 + ret))

    edge = 2.0 * reversal_probability - 1.0
    truth = GroundTruth(
        EdgeKind.MEAN_REVERSION, effect_size=reversal_probability,
        expected_gross_bps=edge * sigma * 10_000, seed=seed,
        note=(f"deviations beyond {threshold_sigma} sigma of a {lookback}-bar mean "
              f"revert with probability {reversal_probability}"),
    )
    rng_bars = random.Random(seed + 1)
    return CalibrationDataset("mean_reversion",
                              tuple(_walk(returns, rng_bars, contract=contract,
                                          timeframe=timeframe, minutes=minutes)),
                              SealedTruth(truth, "mean_reversion"))


def generate_regime_dependent(n: int = 8_000, *, seed: int = 41,
                              phi_a: float = 0.22, phi_b: float = -0.05,
                              regime_length: int = 500, sigma: float = 0.004,
                              contract: str = "SYNZ26", timeframe: str = "5m",
                              minutes: int = 5) -> CalibrationDataset:
    """Alternating regimes with different momentum coefficients.

    Regime A carries a positive relationship; regime B carries a slightly
    negative one. Pooled, the two partly cancel, so a system that reports only
    the aggregate will understate the effect badly. Finding the *difference* is
    what is being calibrated.
    """
    rng = random.Random(seed)
    returns: list[float] = [rng.gauss(0.0, sigma)]
    labels: list[str] = ["A"]

    for index in range(1, n):
        regime = "A" if (index // regime_length) % 2 == 0 else "B"
        phi = phi_a if regime == "A" else phi_b
        returns.append(phi * returns[-1] + rng.gauss(0.0, sigma))
        labels.append(regime)

    truth = GroundTruth(
        EdgeKind.REGIME_DEPENDENT, effect_size=phi_a - phi_b,
        expected_gross_bps=(phi_a - phi_b) * sigma * math.sqrt(2.0 / math.pi) * 10_000,
        seed=seed, regimes=("A", "B"),
        regime_effects={"A": phi_a, "B": phi_b},
        note=f"regime A phi={phi_a}, regime B phi={phi_b}, "
             f"{regime_length}-bar blocks",
    )
    return CalibrationDataset("regime_dependent",
                              tuple(_walk(returns, rng, contract=contract,
                                          timeframe=timeframe, minutes=minutes)),
                              SealedTruth(truth, "regime_dependent"),
                              tuple(labels))


def generate_sub_cost(n: int = 8_000, *, seed: int = 51, phi: float = 0.05,
                      sigma: float = 0.004, contract: str = "SYNZ26",
                      timeframe: str = "5m",
                      minutes: int = 5) -> CalibrationDataset:
    """A real edge, too small to survive costs.

    ``phi`` is positive and detectable given enough observations, and the
    resulting gross expectancy is a fraction of a basis point -- well under any
    realistic round-trip cost. The correct verdict is that the effect exists
    and is economically unattractive. Reporting only the p-value gets this
    dataset wrong, which is why it is here.
    """
    rng = random.Random(seed)
    returns: list[float] = [rng.gauss(0.0, sigma)]
    for _ in range(n - 1):
        returns.append(phi * returns[-1] + rng.gauss(0.0, sigma))

    expected = phi * sigma * math.sqrt(2.0 / math.pi)
    truth = GroundTruth(
        EdgeKind.SUB_COST, effect_size=phi,
        expected_gross_bps=expected * 10_000, seed=seed,
        note=(f"AR(1) phi={phi}; gross expectancy {expected * 10_000:.4f} bps, "
              "far below realistic round-trip cost"),
    )
    return CalibrationDataset("sub_cost",
                              tuple(_walk(returns, rng, contract=contract,
                                          timeframe=timeframe, minutes=minutes)),
                              SealedTruth(truth, "sub_cost"))


ALL_GENERATORS = {
    "null": generate_null,
    "momentum": generate_momentum,
    "mean_reversion": generate_mean_reversion,
    "regime_dependent": generate_regime_dependent,
    "sub_cost": generate_sub_cost,
}
