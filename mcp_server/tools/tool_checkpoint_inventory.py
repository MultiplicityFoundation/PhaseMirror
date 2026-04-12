"""Wave 1 checkpoint inventory MCP tool."""

from __future__ import annotations

from typing import Any

from rollback.checkpoint import CheckpointManager


def checkpoint_inventory(limit: str | None = None) -> dict[str, Any]:
    """List checkpoint inventory and retention tiers through the MCP nervous system."""
    manager = CheckpointManager()
    parsed_limit = int(limit) if limit is not None else None
    checkpoints = manager.list_checkpoints(limit=parsed_limit)
    return {
        "checkpoint_count": len(checkpoints),
        "checkpoints": checkpoints,
    }