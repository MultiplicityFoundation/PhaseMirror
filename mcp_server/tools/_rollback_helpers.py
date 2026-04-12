"""Shared rollback MCP parsing helpers."""

from __future__ import annotations

from rollback.rollback_manager import SystemStateSnapshot


def build_system_state(
    L_Phi: str = "0.0",
    consecutive_failures: str = "0",
    fail_rate_60s: str = "0.0",
    diff_score: str = "0.0",
    threshold: str = "1.0",
    operator_halt: str = "false",
) -> SystemStateSnapshot:
    return SystemStateSnapshot(
        L_Phi=float(L_Phi),
        consecutive_failures=int(consecutive_failures),
        fail_rate_60s=float(fail_rate_60s),
        diff_score=float(diff_score),
        threshold=float(threshold),
        operator_halt=operator_halt.lower() == "true",
    )