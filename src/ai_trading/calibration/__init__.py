"""Phase 9.5: research calibration on data whose truth is known.

Every earlier phase asked whether the machinery *runs*. This one asks whether
it is *right*: five datasets with known generating processes, and a scorer that
checks the research system recovers what was put in and refuses what was not.

The five questions, one dataset each: reject a null, recover momentum, recover
mean reversion, separate regimes, and refuse a real-but-sub-cost edge on
economic grounds rather than statistical ones.
"""

from .generators import (
    ALL_GENERATORS,
    CalibrationDataset,
    generate_mean_reversion,
    generate_momentum,
    generate_null,
    generate_regime_dependent,
    generate_sub_cost,
)
from .harness import (
    MIN_DETECTION_SAMPLE,
    CalibrationRun,
    CalibrationScore,
    Detection,
    EconomicVerdict,
    FalseDiscoveryReport,
    RegimeBreakdown,
    StatisticalVerdict,
    detect_by_regime,
    detect_mean_reversion,
    detect_momentum,
    false_discovery_stress,
)
from .truth import EdgeKind, GroundTruth, RevealLog, SealedTruth, TruthRevealed

__all__ = [
    "ALL_GENERATORS", "MIN_DETECTION_SAMPLE", "CalibrationDataset",
    "CalibrationRun", "CalibrationScore", "Detection", "EconomicVerdict",
    "EdgeKind", "FalseDiscoveryReport", "GroundTruth", "RegimeBreakdown",
    "RevealLog", "SealedTruth", "StatisticalVerdict", "TruthRevealed",
    "detect_by_regime", "detect_mean_reversion", "detect_momentum",
    "false_discovery_stress", "generate_mean_reversion", "generate_momentum",
    "generate_null", "generate_regime_dependent", "generate_sub_cost",
]
