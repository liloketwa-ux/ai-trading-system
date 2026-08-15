"""Phase 7: walk-forward validation and robustness testing.

Research validation only. No parameter optimization, no winner selection, no
funded-account sizing, no live execution.
"""

from .candidates import Candidate, CandidateLockError, CandidateRegistry
from .funding import (
    ComponentStatus,
    EconomicConfidenceError,
    FundingAccrual,
    PnLBreakdown,
)
from .report import (
    DEFAULT_CRITERIA,
    CandidateReport,
    InstrumentReport,
    RobustnessCriteria,
    Verdict,
    WindowResult,
    grade,
)
from .robustness import (
    COST_MULTIPLIERS,
    DELAY_BARS,
    SLIPPAGE_MULTIPLIERS,
    PerturbationAxis,
    PerturbationPoint,
    RobustnessMatrix,
    SensitivityCurve,
    TradeRemovalResult,
    breakeven_multiplier,
    run_trade_removal,
)
from .rolls import (
    AdjustmentMethod,
    ContinuityError,
    ContractSeries,
    RollEvent,
    RollMethod,
    RollPolicy,
)
from .windows import (
    PurgeReport,
    WalkForwardConfig,
    Window,
    generate_windows,
    is_contaminated,
    purge_and_embargo,
)

__all__ = [
    "COST_MULTIPLIERS", "DEFAULT_CRITERIA", "DELAY_BARS", "SLIPPAGE_MULTIPLIERS",
    "AdjustmentMethod", "Candidate", "CandidateLockError", "CandidateRegistry",
    "CandidateReport", "ComponentStatus", "ContinuityError", "ContractSeries",
    "EconomicConfidenceError", "FundingAccrual", "InstrumentReport",
    "PerturbationAxis", "PerturbationPoint", "PnLBreakdown", "PurgeReport",
    "RobustnessCriteria", "RobustnessMatrix", "RollEvent", "RollMethod",
    "RollPolicy", "SensitivityCurve", "TradeRemovalResult", "Verdict",
    "WalkForwardConfig", "Window", "WindowResult", "breakeven_multiplier",
    "generate_windows", "grade", "is_contaminated", "purge_and_embargo",
    "run_trade_removal",
]
