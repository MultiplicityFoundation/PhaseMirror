"""Wave 1 checkpoint prune MCP tool."""

from __future__ import annotations

from typing import Any

from rollback.checkpoint import CheckpointManager


def checkpoint_prune(dry_run: str = "false") -> dict[str, Any]:
    """Prune checkpoint inventory according to rollback retention policy."""
    manager = CheckpointManager()
    return manager.prune_checkpoints(dry_run=dry_run.lower() == "true")