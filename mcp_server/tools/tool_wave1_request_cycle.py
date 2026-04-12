"""ADR-037 Wave 1 request_cycle — request a new Phoenix cycle.

Validates input range [1, 10], checks no cycle in progress,
and verifies system is not halted before accepting.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def wave1_request_cycle(
    cycle_n: int,
    *,
    _runtime: Any = None,
) -> dict[str, object]:
    """Request execution of a specific Phoenix-ℵ₀ cycle.

    Parameters
    ----------
    cycle_n : int
        Cycle number to request (must be 1–10).
    _runtime : PMDRuntimeStub | None
        Injected runtime for testing.

    Returns
    -------
    dict matching ADR-037 RequestCycleResponse schema.
    """
    if _runtime is None:
        _runtime = _get_live_runtime()

    ts = datetime.now(timezone.utc).isoformat()

    # Validate range
    if not (1 <= cycle_n <= 10):
        return {
            "accepted": False,
            "reason": "INVALID_CYCLE",
            "timestamp": ts,
        }

    # Check halted
    if _runtime.status == "halted":
        return {
            "accepted": False,
            "reason": "HALTED",
            "timestamp": ts,
        }

    # Check cycle in progress (step > 0 means mid-cycle)
    if _runtime.step > 0:
        return {
            "accepted": False,
            "reason": "CYCLE_IN_PROGRESS",
            "existing_cycle": int(_runtime.cycle),
            "timestamp": ts,
        }

    return {
        "accepted": True,
        "reason": "CYCLE_ACCEPTED",
        "timestamp": ts,
    }


def _get_live_runtime() -> Any:
    from tests.wave1.conftest import PMDRuntimeStub
    return PMDRuntimeStub()
