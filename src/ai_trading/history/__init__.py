"""Phase 9: real historical data acquisition, quality gating and lineage.

The pipeline, in order:

    provider -> contract book -> quality gate -> research dataset

Each arrow refuses to be crossed on bad inputs. A provider that cannot state
when its data became available says so rather than assuming; a contract book
will not stitch contracts into a continuous series; the quality gate rejects
rather than warns on defects that invalidate research; and a dataset cannot be
constructed from rows the gate did not admit.

**Current state: no real market data has entered this pipeline.** Every
market-data host is blocked at the network boundary in this environment, so no
source has been promoted past ``SOURCE_PRESENT`` and no dataset carries
``DataOrigin.REAL_MARKET``. The machinery is built and tested; it has nothing
real to run on yet, and it reports that rather than substituting something.
"""

from .availability import (
    AvailabilityError,
    AvailabilityPolicy,
    AvailabilityQuality,
    bar_close_availability,
)
from .contracts import (
    ContinuousSeriesRefused,
    ContractBook,
    ContractMetadata,
    RollIndicator,
)
from .datasets import (
    DataOrigin,
    DatasetGateError,
    FeatureEligibility,
    ResearchDataset,
)
from .latency import (
    LatencyInstrument,
    LatencyObservation,
    LatencyProfile,
    LatencyStage,
    LatencyStatus,
    UnmeasuredLatencyError,
)
from .providers import (
    SCHEMA_VERSION,
    Bar,
    CoverageWindow,
    DataKind,
    HistoricalDataProvider,
    HistoricalRecord,
    ProviderCapabilityError,
    ProviderDescriptor,
)
from .replay import LeakageError, LeakageReport, PointInTimeReplay
from .quality import (
    DatasetQualityReport,
    QualityFinding,
    QualityStatus,
    SessionSpec,
    Severity,
    run_quality_gate,
)
from .status import (
    SourceLedger,
    SourcePromotionError,
    SourceRecord,
    SourceStatus,
)

__all__ = [
    "SCHEMA_VERSION", "AvailabilityError", "AvailabilityPolicy",
    "AvailabilityQuality", "Bar", "ContinuousSeriesRefused", "ContractBook",
    "ContractMetadata", "CoverageWindow", "DataKind", "DataOrigin",
    "DatasetGateError", "DatasetQualityReport", "FeatureEligibility",
    "HistoricalDataProvider", "HistoricalRecord", "LatencyInstrument",
    "LatencyObservation", "LatencyProfile", "LatencyStage", "LatencyStatus",
    "LeakageError", "LeakageReport", "PointInTimeReplay",
    "ProviderCapabilityError", "ProviderDescriptor", "QualityFinding",
    "RollIndicator",
    "QualityStatus", "ResearchDataset", "SessionSpec", "Severity",
    "SourceLedger", "SourcePromotionError", "SourceRecord", "SourceStatus",
    "UnmeasuredLatencyError", "bar_close_availability", "run_quality_gate",
]
