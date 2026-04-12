"""Canonical daemon epsilon-adjust MCP tool for runtime control-plane actuation."""

from __future__ import annotations

from typing import Any

from daemon.watchdog import DaemonWatchdog


def daemon_epsilon_adjust(delta: str, reason: str | None = None) -> dict[str, Any]:
    """Apply a circuit-safe runtime epsilon change through the canonical watchdog surface."""
    watchdog = DaemonWatchdog(persist_epsilon_state=True)
    return watchdog.epsilon_adjust(
        float(delta),
        reason=reason or "mcp_runtime_adjustment",
    )