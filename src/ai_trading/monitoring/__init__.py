"""Monitoring, drift detection, and divergence tracking (design-doc section 10).

The monitor observes and emits events; it never acts on the trading path.
Responding to a CRITICAL event stays an explicit decision in the execution
layer, so monitoring can never surprise a running system.
"""

from .dashboard import Monitor, MonitorThresholds
from .divergence import DivergenceReport, compare_to_backtest
from .drift import (
    PSI_SHIFTED,
    PSI_STABLE,
    KSResult,
    drift_report,
    ks_two_sample,
    population_stability_index,
)
from .events import Event, EventLog, Severity
from .performance import PerformanceTracker

__all__ = [
    "PSI_SHIFTED",
    "PSI_STABLE",
    "DivergenceReport",
    "Event",
    "EventLog",
    "KSResult",
    "Monitor",
    "MonitorThresholds",
    "PerformanceTracker",
    "Severity",
    "compare_to_backtest",
    "drift_report",
    "ks_two_sample",
    "population_stability_index",
]
