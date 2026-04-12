"""Wave 1 Phase Mirror MCP tool."""

from __future__ import annotations

import json
from typing import Any

from contracts.shared.types import StateSnapshot
from governance.ledger import get_phase_mirror_audit_ledger
from policy.phase_mirror import evaluate


def phase_mirror(
    input_text: str,
    output_expr: str,
    context_json: str | None = None,
) -> dict[str, Any]:
    """Evaluate a request against the Tooling-owned Phase Mirror surface.
    
    Per ADR-013: Type-safe mode inference. output_expr is parsed as JSON
    and wrapped in StateSnapshot for state transition evaluation.
    """
    # Parse output_expr as JSON and wrap in StateSnapshot.
    # If the payload is not JSON, encapsulate it in a minimal snapshot envelope
    # so the type-safe evaluator still receives a valid StateSnapshot.
    try:
        expr_data = json.loads(output_expr)
    except json.JSONDecodeError:
        expr_data = {
            "snapshot_id": "mcp-eval",
            "state": {"raw_output_expr": output_expr},
        }

    if isinstance(expr_data, dict) and "snapshot_id" not in expr_data:
        expr_data = {
            "snapshot_id": "mcp-eval",
            "state": expr_data,
        }

    expr_bytes = json.dumps(expr_data).encode("utf-8")
    snapshot_id = str(expr_data.get("snapshot_id", "mcp-eval")) if isinstance(expr_data, dict) else "mcp-eval"
    snapshot = StateSnapshot(expr_bytes, snapshot_id=snapshot_id)

    # context_json parameter is deprecated but kept for backward compat
    # Type-safe signature doesn't use context parameter

    report = evaluate(
        input_text=input_text,
        output_expr=snapshot,
        audit_ledger=get_phase_mirror_audit_ledger(),
    )
    return report.to_dict()
