"""Adapter lifecycle state.

Source code existing is not the same as an adapter working. The audit found six
of Pumpi's eight adapters present but never started, referenced elsewhere only
as platform name strings -- which would have produced a three-platform dataset
labelled as eight.

This models the distance between "the file exists" and "we trust its output",
so an adapter can never be described as production-ready merely because it
compiles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum

from ..storage.records import utc

__all__ = ["AdapterState", "AdapterHealth", "ADAPTER_REGISTRY"]


class AdapterState(IntEnum):
    """Ordered: each level presupposes the ones below it."""

    DISABLED = 0            # explicitly turned off
    PRESENT = 1             # source exists, nothing verified
    UNIT_TESTED = 2         # decode logic has tests
    RUNTIME_VERIFIED = 3    # observed decoding live data correctly
    HISTORICALLY_VALIDATED = 4  # output checked against an independent source
    PRODUCTION_ENABLED = 5  # cleared for research and trading use

    def __str__(self) -> str:
        return self.name

    @property
    def usable_for_research(self) -> bool:
        """Below HISTORICALLY_VALIDATED, output is not trustworthy as data."""
        return self >= AdapterState.HISTORICALLY_VALIDATED


@dataclass
class AdapterHealth:
    """Health record for one ingestion adapter."""

    name: str
    platform: str
    state: AdapterState = AdapterState.PRESENT
    last_event_at: datetime | None = None
    events_seen: int = 0
    decode_failures: int = 0
    reconnects: int = 0
    notes: str = ""
    evidence: list[str] = field(default_factory=list)

    def promote(self, state: AdapterState, evidence: str) -> None:
        """Advance the lifecycle. Promotion requires stated evidence.

        Skipping levels is refused: an adapter cannot be historically validated
        without first having been seen to run.
        """
        if state <= self.state:
            raise ValueError(
                f"{self.name}: cannot promote to {state} from {self.state} -- "
                "lifecycle moves forward only (use demote to disable)"
            )
        if state - self.state > 1:
            raise ValueError(
                f"{self.name}: cannot skip from {self.state} to {state} -- "
                "each level presupposes the one below it"
            )
        if not evidence.strip():
            raise ValueError(f"{self.name}: promotion requires evidence")
        self.state = state
        self.evidence.append(f"{state}: {evidence}")

    def demote(self, reason: str) -> None:
        self.state = AdapterState.DISABLED
        self.evidence.append(f"DISABLED: {reason}")

    def record_event(self, at: datetime) -> None:
        self.last_event_at = utc(at)
        self.events_seen += 1

    @property
    def usable_for_research(self) -> bool:
        return self.state.usable_for_research


#: Ground truth as established by the Phase 1 audit. Only the three adapters
#: actually started by Pumpi's registry are above PRESENT, and none has been
#: runtime-verified here because the network is unavailable.
ADAPTER_REGISTRY: dict[str, AdapterHealth] = {
    "pumpfun": AdapterHealth(
        "pumpfun", "pump.fun", AdapterState.PRESENT,
        notes="started by pumpApiManager; decode unverified here (no network)",
    ),
    "pumpswap": AdapterHealth(
        "pumpswap", "PumpSwap", AdapterState.PRESENT,
        notes="started by pumpApiManager; decode unverified here (no network)",
    ),
    "raydium_launchlab": AdapterHealth(
        "raydium_launchlab", "Raydium LaunchLab", AdapterState.UNIT_TESTED,
        notes="started by pumpApiManager; launchlabDecode has unit tests",
        evidence=["UNIT_TESTED: launchlabDecode.test.ts present in Pumpi"],
    ),
    "raydium_amm": AdapterHealth(
        "raydium_amm", "Raydium AMM", AdapterState.PRESENT,
        notes="NOT started by Pumpi's registry; referenced only as a label",
    ),
    "meteora": AdapterHealth(
        "meteora", "Meteora", AdapterState.PRESENT,
        notes="NOT started; referenced only as a label in enrichment/routes",
    ),
    "orca": AdapterHealth(
        "orca", "Orca", AdapterState.PRESENT,
        notes="NOT started; referenced only as a label in enrichment/routes",
    ),
    "moonshot": AdapterHealth(
        "moonshot", "Moonshot", AdapterState.PRESENT,
        notes="NOT started; referenced only as a label in enrichment",
    ),
    "letsbonk": AdapterHealth(
        "letsbonk", "LetsBonk", AdapterState.PRESENT,
        notes="NOT started; referenced only as a label in enrichment",
    ),
}
