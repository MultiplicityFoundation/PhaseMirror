"""Canonical daemon heartbeat MCP tool for Wave 3 PMD runtime wiring."""

from __future__ import annotations

from typing import Any

from daemon.watchdog import DaemonWatchdog


def daemon_heartbeat(
    label: str | None = None,
    L_Phi: str | None = None,
    consecutive_failures: str | None = None,
    fail_rate_60s: str | None = None,
    operator_halt: str | None = None,
) -> dict[str, Any]:
    """Execute one daemon heartbeat and optionally evaluate runtime rollback triggers."""
    watchdog = DaemonWatchdog(persist_epsilon_state=True)
    heartbeat_report = watchdog.heartbeat(label=label)

    metrics_provided = any(
        value is not None
        for value in (L_Phi, consecutive_failures, fail_rate_60s, operator_halt)
    )
    if not metrics_provided:
        return heartbeat_report

    runtime_report = watchdog.evaluate_runtime(
        L_Phi=float(L_Phi) if L_Phi is not None else 0.0,
        consecutive_failures=int(consecutive_failures) if consecutive_failures is not None else 0,
        fail_rate_60s=float(fail_rate_60s) if fail_rate_60s is not None else 0.0,
        operator_halt=(operator_halt or "false").lower() == "true",
        diff_score=float(heartbeat_report["diff_score"]),
    )
    return {
        "heartbeat": heartbeat_report,
        "runtime": runtime_report,
    }