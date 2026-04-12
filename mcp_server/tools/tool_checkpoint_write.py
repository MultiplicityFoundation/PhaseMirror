"""Wave 1 checkpoint-writing MCP tool."""

from __future__ import annotations

import json
from typing import Any

from rollback.checkpoint import CheckpointManager


def checkpoint_write(
    label: str | None = None,
    governance_version: str | None = None,
    ledger_reference: str | None = None,
    metadata_json: str | None = None,
) -> dict[str, Any]:
    """Create an explicit rollback checkpoint before self-modification."""
    metadata: dict[str, Any] | None = None
    if metadata_json:
        metadata = json.loads(metadata_json)

    manager = CheckpointManager()
    return manager.write_checkpoint(
        label=label,
        governance_version=governance_version,
        ledger_reference=ledger_reference,
        metadata=metadata,
    )