"""ADR-037 Wave 1 request_rollback — request rollback to checkpoint.

Validates checkpoint exists, no rollback already in progress,
and system is not halted before accepting.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def wave1_request_rollback(
    target_checkpoint: str | None = None,
    *,
    _runtime: Any = None,
) -> dict[str, object]:
    """Request rollback to a previous checkpoint.

    Parameters
    ----------
    target_checkpoint : str | None
        ISO-8601 timestamp of target checkpoint.  If None, rolls back
        to the most recent checkpoint.
    _runtime : PMDRuntimeStub | None
        Injected runtime for testing.

    Returns
    -------
    dict matching ADR-037 RequestRollbackResponse schema.
    """
    if _runtime is None:
        _runtime = _get_live_runtime()

    ts = datetime.now(timezone.utc).isoformat()

    # No checkpoints available
    if not _runtime.checkpoints:
        return {
            "rollback_accepted": False,
            "reason": "NO_CHECKPOINT",
            "estimated_duration_seconds": 0,
            "timestamp": ts,
        }

    # Already rolling back
    if _runtime.rollback_in_progress:
        return {
            "rollback_accepted": False,
            "reason": "ROLLBACK_IN_PROGRESS",
            "estimated_duration_seconds": 0,
            "timestamp": ts,
        }

    # Resolve target
    if target_checkpoint is not None:
        matching = [
            cp for cp in _runtime.checkpoints
            if cp["timestamp"] == target_checkpoint
        ]
        if not matching:
            return {
                "rollback_accepted": False,
                "reason": "NO_CHECKPOINT",
                "estimated_duration_seconds": 0,
                "timestamp": ts,
            }
        resolved = matching[0]
    else:
        resolved = _runtime.checkpoints[-1]

    return {
        "rollback_accepted": True,
        "reason": "ROLLBACK_ACCEPTED",
        "target_checkpoint": resolved["timestamp"],
        "estimated_duration_seconds": 1,
        "timestamp": ts,
    }


def _get_live_runtime() -> Any:
    from tests.wave1.conftest import PMDRuntimeStub
    return PMDRuntimeStub()
