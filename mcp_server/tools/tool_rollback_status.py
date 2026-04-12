"""Wave 1 rollback-status MCP tool."""

from __future__ import annotations

from typing import Any

from rollback.rollback_manager import RollbackManager
from ._rollback_helpers import build_system_state


def rollback_status(
    L_Phi: str = "0.0",
    consecutive_failures: str = "0",
    fail_rate_60s: str = "0.0",
    diff_score: str = "0.0",
    threshold: str = "1.0",
    operator_halt: str = "false",
) -> dict[str, Any]:
    """Expose rollback trigger evaluation through the MCP nervous system."""
    manager = RollbackManager()
    system_state = build_system_state(
        L_Phi=L_Phi,
        consecutive_failures=consecutive_failures,
        fail_rate_60s=fail_rate_60s,
        diff_score=diff_score,
        threshold=threshold,
        operator_halt=operator_halt,
    )
    report = manager.evaluate(system_state)
    report["triggers"] = [trigger.name for trigger in manager.list_triggers()]
    return report