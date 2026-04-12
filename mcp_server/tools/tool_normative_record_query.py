"""Wave 1 normative-record query MCP tool."""

from __future__ import annotations

from typing import Any

from rollback.normative_record import NormativeRecordWriter


def normative_record_query(record_id: str | None = None, limit: str | None = None) -> dict[str, Any]:
    """Inspect the local normative record ledger through the MCP nervous system."""
    writer = NormativeRecordWriter()
    if record_id:
        record = writer.get_record(record_id)
        return {
            "record_id": record_id,
            "found": record is not None,
            "record": record,
        }

    parsed_limit = int(limit) if limit is not None else None
    return writer.describe(limit=parsed_limit)