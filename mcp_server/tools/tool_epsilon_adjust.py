"""Fast-path epsilon adjustment MCP tool surface (ADR-018)."""

from __future__ import annotations

from typing import Any

from daemon.watchdog import DaemonWatchdog


def epsilon_adjust(delta_epsilon: str, reason: str | None = None) -> dict[str, Any]:
    """Adjust runtime epsilon through the watchdog fast path only."""
    watchdog = DaemonWatchdog(persist_epsilon_state=True)
    return watchdog.epsilon_adjust(
        float(delta_epsilon),
        reason=reason or "watchdog_control",
    )
