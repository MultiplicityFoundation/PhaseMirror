"""A-07: Health signal contract for watchdog trigger integration.

Defines HealthReport — the structured result of the L(Phi) health monitor.
Consumed by WatchdogTriggerEvaluator.evaluate_l_phi_breach_from_health_report().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict

# Maximum age (seconds) before a report is considered stale.
_STALE_THRESHOLD_SECONDS: float = 60.0


@dataclass(frozen=True)
class HealthReport:
    """Immutable health signal snapshot from the A-07 health monitor.

    Attributes:
        report_id:   Unique identifier for this report snapshot.
        l_phi:       Computed L(Phi) value.  Values above the watchdog
                     threshold (default 0.9) trigger a breach.
        confidence:  Confidence score in [0.0, 1.0] for this reading.
                     Values below 0.5 are treated as insufficient for
                     reliable decision-making.
        timestamp:   UTC timestamp at which this report was produced.
        metadata:    Arbitrary key-value annotations (not for policy use).
    """

    report_id: str
    l_phi: float
    confidence: float
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"HealthReport.confidence must be in [0.0, 1.0], got {self.confidence}"
            )

    def is_stale(self, current_time: datetime | None = None) -> bool:
        """Return True if this report is older than the staleness threshold."""
        if current_time is None:
            current_time = datetime.now(tz=timezone.utc)
        age_seconds = (current_time - self.timestamp).total_seconds()
        return age_seconds > _STALE_THRESHOLD_SECONDS
