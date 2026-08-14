"""Outcome labels.

Labels are the one place future prices are legitimate: an outcome is by
definition observed after the decision. The danger is that a label is
structurally identical to a feature -- a float attached to a timestamp -- so
nothing stops it being joined back in as an input except discipline.

Discipline is replaced here by type. :class:`Label` is not a
``FeatureSnapshot`` and has no ``available_at``; it carries ``resolved_at``
instead, the instant the outcome became known. Anything consuming features
takes ``FeatureSnapshot``; a ``Label`` will not fit, and the evaluation pipeline
is the only thing that holds both.

Definitions are immutable and versioned. Changing what ``hit_2R`` means after an
experiment has run silently rewrites what its results were measuring.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from ..storage.records import Observation, utc

__all__ = [
    "LabelKind", "LabelDefinition", "Label", "compute_forward_return",
    "compute_r_multiple", "LabelError",
]


class LabelError(RuntimeError):
    """A label could not be resolved."""


class LabelKind(str, Enum):
    FORWARD_RETURN = "forward_return"
    R_MULTIPLE = "r_multiple"
    TIME_TO_TARGET = "time_to_target"
    EXCURSION = "excursion"


@dataclass(frozen=True)
class LabelDefinition:
    """An immutable, versioned outcome definition.

    Attributes:
        name: Label identifier, e.g. ``forward_return_1h``.
        kind: Family of outcome.
        version: Definition version. Immutable once an experiment cites it.
        horizon: How far forward the outcome looks.
        target_r: Target distance in R, for R-multiple labels.
        stop_r: Stop distance in R (always 1.0 by construction of R).
        stop_definition: How the stop distance is derived, e.g. ``"1.0*atr_14"``.
        cost_assumptions: Costs applied when producing the net outcome.
        tie_policy: Which side wins when target and stop are both touched in one
            bar. ``"stop"`` is pessimistic and the default -- bar data cannot say
            which came first, and assuming the favourable order inflates every
            R-multiple result.
    """

    name: str
    kind: LabelKind
    horizon: timedelta
    version: str = "1"
    target_r: float | None = None
    stop_r: float = 1.0
    stop_definition: str = ""
    cost_assumptions: dict[str, float] = field(default_factory=dict)
    tie_policy: str = "stop"
    notes: str = ""

    def __post_init__(self) -> None:
        if self.horizon <= timedelta(0):
            raise ValueError(f"{self.name}: horizon must be positive")
        if self.kind is LabelKind.R_MULTIPLE and not self.target_r:
            raise ValueError(f"{self.name}: R-multiple labels need target_r")
        if self.stop_r <= 0:
            raise ValueError(f"{self.name}: stop_r must be > 0")
        if self.tie_policy not in ("stop", "target"):
            raise ValueError(f"{self.name}: tie_policy must be 'stop' or 'target'")

    @property
    def key(self) -> str:
        return f"{self.name}:v{self.version}"

    @property
    def checksum(self) -> str:
        payload = json.dumps(
            {
                "name": self.name, "kind": self.kind.value,
                "horizon_s": self.horizon.total_seconds(),
                "version": self.version, "target_r": self.target_r,
                "stop_r": self.stop_r, "stop_definition": self.stop_definition,
                "cost_assumptions": self.cost_assumptions,
                "tie_policy": self.tie_policy,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "key": self.key, "kind": self.kind.value,
            "horizon_seconds": self.horizon.total_seconds(),
            "target_r": self.target_r, "stop_r": self.stop_r,
            "stop_definition": self.stop_definition,
            "cost_assumptions": dict(self.cost_assumptions),
            "tie_policy": self.tie_policy, "checksum": self.checksum,
        }


@dataclass(frozen=True)
class Label:
    """A resolved outcome.

    Deliberately **not** a ``FeatureSnapshot``: it has ``resolved_at`` rather
    than ``available_at``, so it cannot be passed where a feature is expected.
    """

    definition_key: str
    instrument: str
    decision_time: datetime
    resolved_at: datetime
    value: float | None
    resolved: bool
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_time", utc(self.decision_time))
        object.__setattr__(self, "resolved_at", utc(self.resolved_at))
        if self.resolved_at < self.decision_time:
            raise LabelError("an outcome cannot resolve before the decision")


def _bar_values(bar: Observation) -> tuple[float, float, float, float]:
    v = bar.value
    return v["open"], v["high"], v["low"], v["close"]


def compute_forward_return(
    definition: LabelDefinition,
    instrument: str,
    decision_time: datetime,
    entry_price: float,
    future_bars: list[Observation],
    *,
    direction: int = 1,
    cost_bps: float = 0.0,
) -> Label:
    """Simple forward return over the definition's horizon.

    ``future_bars`` must be bars *after* the decision; the caller is responsible
    for that separation and the evaluation pipeline enforces it.
    """
    moment = utc(decision_time)
    deadline = moment + definition.horizon
    within = [b for b in future_bars if moment < b.event_time <= deadline]
    if not within:
        return Label(definition.key, instrument, moment, deadline, None, False,
                     {"reason": "no bars within horizon"})

    exit_price = within[-1].value["close"]
    gross = direction * (exit_price / entry_price - 1.0)
    net = gross - cost_bps / 10_000.0
    return Label(
        definition.key, instrument, moment, within[-1].event_time, net, True,
        {"gross": gross, "net": net, "entry": entry_price, "exit": exit_price,
         "bars": len(within), "cost_bps": cost_bps},
    )


def compute_r_multiple(
    definition: LabelDefinition,
    instrument: str,
    decision_time: datetime,
    entry_price: float,
    stop_distance: float,
    future_bars: list[Observation],
    *,
    direction: int = 1,
    cost_bps: float = 0.0,
) -> Label:
    """Did price reach ``target_r`` before ``-stop_r``, within the horizon?

    Also returns MAE and MFE in R units, and time to target when reached.

    **Tie policy matters.** When a bar's range spans both the target and the
    stop, bar data cannot say which was touched first. Assuming the target
    inflates every result, so the default resolves ties to the stop.
    """
    if stop_distance <= 0:
        raise LabelError(f"stop_distance must be > 0, got {stop_distance}")

    moment = utc(decision_time)
    deadline = moment + definition.horizon
    within = [b for b in future_bars if moment < b.event_time <= deadline]
    if not within:
        return Label(definition.key, instrument, moment, deadline, None, False,
                     {"reason": "no bars within horizon"})

    target_r = definition.target_r or 1.0
    if direction > 0:
        stop_price = entry_price - stop_distance * definition.stop_r
        target_price = entry_price + stop_distance * target_r
    else:
        stop_price = entry_price + stop_distance * definition.stop_r
        target_price = entry_price - stop_distance * target_r

    mae_r = 0.0
    mfe_r = 0.0
    for bar in within:
        _, high, low, _ = _bar_values(bar)
        favourable = (high - entry_price) if direction > 0 else (entry_price - low)
        adverse = (entry_price - low) if direction > 0 else (high - entry_price)
        mfe_r = max(mfe_r, favourable / stop_distance)
        mae_r = max(mae_r, adverse / stop_distance)

        hit_target = high >= target_price if direction > 0 else low <= target_price
        hit_stop = low <= stop_price if direction > 0 else high >= stop_price

        if hit_target and hit_stop:
            won = definition.tie_policy == "target"
        elif hit_target:
            won = True
        elif hit_stop:
            won = False
        else:
            continue

        gross_r = target_r if won else -definition.stop_r
        cost_r = (cost_bps / 10_000.0) * entry_price / stop_distance
        return Label(
            definition.key, instrument, moment, bar.event_time,
            gross_r - cost_r, True,
            {"outcome": "target" if won else "stop", "gross_r": gross_r,
             "cost_r": cost_r, "mae_r": mae_r, "mfe_r": mfe_r,
             "time_to_resolve_s": (bar.event_time - moment).total_seconds(),
             "tie": hit_target and hit_stop},
        )

    # Neither level reached: mark to the final close.
    final_close = within[-1].value["close"]
    open_r = direction * (final_close - entry_price) / stop_distance
    cost_r = (cost_bps / 10_000.0) * entry_price / stop_distance
    return Label(
        definition.key, instrument, moment, within[-1].event_time,
        open_r - cost_r, True,
        {"outcome": "timeout", "gross_r": open_r, "cost_r": cost_r,
         "mae_r": mae_r, "mfe_r": mfe_r},
    )


#: Standard label family. Immutable -- extend with new versions, never edit.
FORWARD_RETURNS = {
    name: LabelDefinition(f"forward_return_{name}", LabelKind.FORWARD_RETURN, horizon)
    for name, horizon in [
        ("5m", timedelta(minutes=5)), ("15m", timedelta(minutes=15)),
        ("30m", timedelta(minutes=30)), ("1h", timedelta(hours=1)),
        ("4h", timedelta(hours=4)),
    ]
}

R_LABELS = {
    f"hit_{r}R": LabelDefinition(
        f"hit_{r}R_before_-1R", LabelKind.R_MULTIPLE, timedelta(hours=4),
        target_r=float(r), stop_r=1.0, stop_definition="1.0*atr_14",
    )
    for r in (1, 2, 3)
}
