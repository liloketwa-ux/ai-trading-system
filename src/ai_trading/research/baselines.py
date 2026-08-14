"""Baselines a hypothesis must beat.

An ICT combination being associated with forward returns is not evidence it
adds anything: the market may simply drift, or the setup may fire mostly in
trending conditions a moving-average crossover would also catch. The question is
always **incremental** -- does it add information beyond something simpler?

Baselines here select *comparison samples* from the same candidate events, so
the comparison is like-for-like: same instrument, same period, same label, same
costs. Only the selection rule differs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from .sampling import Event

__all__ = ["Baseline", "BASELINES", "select_baseline"]


@dataclass(frozen=True)
class Baseline:
    """A named comparison selector."""

    name: str
    description: str
    select: Callable[[list[Event], int], list[Event]]


def _random(events: list[Event], seed: int, fraction: float = 0.3) -> list[Event]:
    """Uniformly random subset -- the pure-luck reference."""
    if not events:
        return []
    rng = np.random.default_rng(seed)
    size = max(1, int(len(events) * fraction))
    return [events[i] for i in sorted(rng.choice(len(events), size=size, replace=False))]


def _hold_matched_random(events: list[Event], seed: int, target: int = 0) -> list[Event]:
    """Random selection matched to the treatment's event count.

    A random baseline that fires a different number of times is confounded by
    turnover rather than by signal quality, so the count is matched explicitly.
    """
    if not events:
        return []
    rng = np.random.default_rng(seed)
    size = min(len(events), target) if target > 0 else max(1, len(events) // 3)
    return [events[i] for i in sorted(rng.choice(len(events), size=size, replace=False))]


def _momentum(events: list[Event], seed: int) -> list[Event]:
    """Events where recent return is positive."""
    return [e for e in events if (e.features.get("bar_return") or 0) > 0]


def _mean_reversion(events: list[Event], seed: int) -> list[Event]:
    """Events where recent return is negative."""
    return [e for e in events if (e.features.get("bar_return") or 0) < 0]


def _session_only(events: list[Event], seed: int, session: str = "new_york") -> list[Event]:
    """Session membership alone -- no pattern component."""
    return [e for e in events if e.features.get("session") == session]


def _volatility_only(events: list[Event], seed: int) -> list[Event]:
    """Above-median displacement alone, ignoring every pattern component."""
    values = [e.features.get("displacement_atr") for e in events
              if e.features.get("displacement_atr") is not None]
    if not values:
        return []
    median = float(np.median(values))
    return [e for e in events
            if (e.features.get("displacement_atr") or 0) > median]


def _structure_only(events: list[Event], seed: int) -> list[Event]:
    """Market-structure shift alone, without sweep or imbalance."""
    return [e for e in events if e.features.get("mss") is True]


BASELINES = {
    b.name: b for b in [
        Baseline("random", "uniformly random subset", _random),
        Baseline("hold_matched_random", "random, count-matched to the treatment",
                 _hold_matched_random),
        Baseline("momentum", "positive recent return", _momentum),
        Baseline("mean_reversion", "negative recent return", _mean_reversion),
        Baseline("session_only", "session membership alone", _session_only),
        Baseline("volatility_only", "above-median displacement alone", _volatility_only),
        Baseline("structure_only", "market-structure shift alone", _structure_only),
    ]
}


def select_baseline(name: str, events: list[Event], seed: int, **kw) -> list[Event]:
    baseline = BASELINES.get(name)
    if baseline is None:
        raise KeyError(f"unknown baseline {name!r}")
    return baseline.select(events, seed, **kw) if kw else baseline.select(events, seed)
