"""Temporal audit of the OpenMobius structural indicator.

Findings recorded as data rather than prose, so the gate can be enforced by
code: :func:`assert_importable` refuses anything not ``POINT_IN_TIME_SAFE``.

The headline result is that **no structural output of the audited indicator is
importable as historical truth**. That is not a criticism of the tool -- it is
built to annotate a chart that a human is looking at *now*, where "the last two
bars exist" is trivially true. It becomes a defect only when the same output is
replayed as though it had been available at the bar it is labelled with.

Three distinct mechanisms, worth separating because they need different fixes:

1. **Centred confirmation.** A fractal swing with ``right=2`` cannot be known
   until two bars later. The function emits ``index: i`` and no confirmation
   index, so a consumer has no way to recover the delay. Fixable by recording
   ``formed_at`` and ``confirmed_at`` separately.

2. **Whole-array normalisation.** ``calc_atr`` returns the ATR of the *last*
   14 bars of whatever array it is given, and that single value is then used to
   threshold events at every index. An event at bar 50 is classified using
   volatility from bar 3,000. Fixable by computing a rolling ATR as of each bar.

3. **Forward scanning.** ``mitigation_pct`` walks every bar after formation to
   the end of the array. There is no delay to model here -- the quantity is a
   summary of the future by construction, and the fix is not to import it.

A fourth, quieter one: every record carries ``age_bars = n - 1 - i``, relative
to the end of the supplied array. The number is meaningful only for the exact
call that produced it, so a stored record silently misstates age.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "TemporalClass", "TemporalFinding", "OPENMOBIUS_TEMPORAL_AUDIT",
    "assert_importable", "TemporalImportError", "audit_summary",
]


class TemporalImportError(RuntimeError):
    """A future-aware or unaudited signal was offered to the research engine."""


class TemporalClass(str, Enum):
    """When an output is genuinely knowable, relative to the bar it labels."""

    #: Knowable at the close of the bar it is attributed to.
    POINT_IN_TIME_SAFE = "point_in_time_safe"
    #: Real, but only knowable N bars later. Usable once the delay is modelled
    #: explicitly as available_at = confirmed_at.
    DELAYED_CONFIRMATION = "delayed_confirmation"
    #: Depends on data after the decision point in a way no delay can repair.
    RETROSPECTIVE = "retrospective"
    #: Not inspectable. Treated exactly as RETROSPECTIVE.
    UNKNOWN = "unknown"

    @property
    def importable_as_is(self) -> bool:
        return self is TemporalClass.POINT_IN_TIME_SAFE

    @property
    def usable_with_explicit_delay(self) -> bool:
        return self is TemporalClass.DELAYED_CONFIRMATION

    @property
    def barred_from_research(self) -> bool:
        """UNKNOWN is barred with RETROSPECTIVE, not held pending review.

        An opaque server-side computation is not innocent until proven guilty:
        the cost of being wrong is a silently inflated backtest.
        """
        return self in (TemporalClass.RETROSPECTIVE, TemporalClass.UNKNOWN)


@dataclass(frozen=True)
class TemporalFinding:
    """One audited output."""

    output: str
    source_function: str
    temporal_class: TemporalClass
    confirmation_lag_bars: int | None
    evidence: str
    remediation: str

    def to_dict(self) -> dict:
        return {
            "output": self.output, "source_function": self.source_function,
            "temporal_class": self.temporal_class.value,
            "confirmation_lag_bars": self.confirmation_lag_bars,
            "importable_as_is": self.temporal_class.importable_as_is,
            "barred_from_research": self.temporal_class.barred_from_research,
            "evidence": self.evidence, "remediation": self.remediation,
        }


#: Audit of OpenMobius-skill ``scripts/kb_klines.py`` as inspected 2026-08-17.
OPENMOBIUS_TEMPORAL_AUDIT: tuple[TemporalFinding, ...] = (
    TemporalFinding(
        output="swing_pivot",
        source_function="find_swings(left=2, right=2)",
        temporal_class=TemporalClass.RETROSPECTIVE,
        confirmation_lag_bars=2,
        evidence="centred fractal: requires candles[i+1..i+right]; emits "
                 "{'index': i} with no confirmation index, so the two-bar delay "
                 "is unrecoverable by a consumer",
        remediation="recompute internally with formed_at=i and "
                    "confirmed_at=i+right; available_at = confirmed_at",
    ),
    TemporalFinding(
        output="fair_value_gap",
        source_function="find_fvgs",
        temporal_class=TemporalClass.DELAYED_CONFIRMATION,
        confirmation_lag_bars=1,
        evidence="three-candle pattern reported as formed_at_index=i+1, but the "
                 "test reads candles[i+2]; the gap is knowable one bar after the "
                 "index it is labelled with",
        remediation="own implementation labelling formed_at=i+1 and "
                    "available_at=close of bar i+2",
    ),
    TemporalFinding(
        output="fvg_mitigation_pct",
        source_function="_fvg_mitigation_pct",
        temporal_class=TemporalClass.RETROSPECTIVE,
        confirmation_lag_bars=None,
        evidence="scans every bar from formation to the end of the array; it is "
                 "a summary of the future by construction",
        remediation="not importable; compute mitigation as-of a decision time or "
                    "not at all",
    ),
    TemporalFinding(
        output="order_block",
        source_function="find_order_blocks",
        temporal_class=TemporalClass.RETROSPECTIVE,
        confirmation_lag_bars=3,
        evidence="labels formed_at_index=i while requiring candles[i+1:i+4] for "
                 "the displacement test; three bars of lookahead presented as a "
                 "property of bar i",
        remediation="own implementation with confirmed_at=i+3; the block's "
                    "existence is a claim about bar i+3, not bar i",
    ),
    TemporalFinding(
        output="liquidity_sweep",
        source_function="find_sweeps",
        temporal_class=TemporalClass.RETROSPECTIVE,
        confirmation_lag_bars=2,
        evidence="consumes the swing list, inheriting its centred-fractal "
                 "lookahead; the sweep itself is a single-bar event and would be "
                 "safe against a point-in-time swing reference",
        remediation="rebuild on confirmed swings; the sweep bar is then safe at "
                    "its own close",
    ),
    TemporalFinding(
        output="displacement",
        source_function="find_displacements",
        temporal_class=TemporalClass.RETROSPECTIVE,
        confirmation_lag_bars=None,
        evidence="the event is a single-bar body test and would be safe, but the "
                 "threshold is calc_atr(candles) -- the ATR of the last 14 bars "
                 "of the whole array, applied to classify every earlier bar",
        remediation="rolling ATR as of each bar; the event is then safe at its "
                    "own close",
    ),
    TemporalFinding(
        output="bos_choch",
        source_function="analyze_structure",
        temporal_class=TemporalClass.RETROSPECTIVE,
        confirmation_lag_bars=2,
        evidence="derived entirely from the swing sequence; inherits the "
                 "centred-fractal lookahead and adds no confirmation record",
        remediation="rebuild on confirmed swings; a break is knowable at the "
                    "close of the bar that breaks a confirmed level",
    ),
    TemporalFinding(
        output="trailing_extremes",
        source_function="remote API (api.mobiusquant.ai)",
        temporal_class=TemporalClass.UNKNOWN,
        confirmation_lag_bars=None,
        evidence="computed server-side and returned as opaque objects; the "
                 "algorithm is not inspectable from this repository",
        remediation="not importable at any delay; define our own or omit",
    ),
    TemporalFinding(
        output="premium_discount_equilibrium",
        source_function="remote API (api.mobiusquant.ai)",
        temporal_class=TemporalClass.UNKNOWN,
        confirmation_lag_bars=None,
        evidence="zone bands returned by the remote service; the reference range "
                 "and whether it updates on future extremes is not visible",
        remediation="define our own against an explicitly bounded lookback range",
    ),
    TemporalFinding(
        output="equal_highs_lows",
        source_function="remote API (api.mobiusquant.ai)",
        temporal_class=TemporalClass.UNKNOWN,
        confirmation_lag_bars=None,
        evidence="returned by the remote service; tolerance and whether later "
                 "bars can create or dissolve a pair is not visible",
        remediation="define our own with an explicit tolerance and a stated "
                    "as-of rule",
    ),
    TemporalFinding(
        output="volume_anomaly",
        source_function="find_volume_anomalies",
        temporal_class=TemporalClass.POINT_IN_TIME_SAFE,
        confirmation_lag_bars=0,
        evidence="rolling mean over candles[i-lookback:i], strictly backward; "
                 "the one audited output with no forward dependency",
        remediation="none required; still reimplemented internally for "
                    "provenance and versioning",
    ),
)


def assert_importable(finding: TemporalFinding) -> TemporalFinding:
    """Refuse anything that is not point-in-time safe."""
    if finding.temporal_class.importable_as_is:
        return finding
    raise TemporalImportError(
        f"{finding.output} ({finding.source_function}) is "
        f"{finding.temporal_class.value} and may not enter the research engine. "
        f"{finding.evidence}. Remediation: {finding.remediation}."
    )


def audit_summary() -> dict:
    counts: dict[str, int] = {c.value: 0 for c in TemporalClass}
    for finding in OPENMOBIUS_TEMPORAL_AUDIT:
        counts[finding.temporal_class.value] += 1
    barred = [f.output for f in OPENMOBIUS_TEMPORAL_AUDIT
              if f.temporal_class.barred_from_research]
    return {
        "audited_outputs": len(OPENMOBIUS_TEMPORAL_AUDIT),
        "by_class": counts,
        "importable_as_is": [f.output for f in OPENMOBIUS_TEMPORAL_AUDIT
                             if f.temporal_class.importable_as_is],
        "barred_from_research": barred,
        "findings": [f.to_dict() for f in OPENMOBIUS_TEMPORAL_AUDIT],
    }
