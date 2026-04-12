"""Gate F — F-07: Audit Logging and Monitoring Tests.

Tests for:
- AuditEntry.compute_hash() hash chain formula
- AuditEntry.finalise()
- AuditLedger.append() persistence
- AuditLedger.validate_chain() detects tampering
- AuditLedger.query() filtering
- AuditLedger.get_stats() → CallMetrics
- AuditMiddleware wrapping
- AuditExporter JSON, CSV, compliance report
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp_server.audit import (
    AuditEntry,
    AuditExporter,
    AuditLedger,
    AuditMiddleware,
    CallMetrics,
    _digest,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ledger(tmp_path: Path) -> AuditLedger:
    return AuditLedger(audit_path=tmp_path / "test_ledger.jsonl")


def _append_entry(ledger: AuditLedger, *, tool: str = "health_check") -> AuditEntry:
    return ledger.append(
        tool_name=tool,
        caller_id="subject:test@example.com",
        parameters={},
        result={"status": "pass"},
        duration_ms=1.5,
    )


# ---------------------------------------------------------------------------
# AuditEntry — hash chain formula
# ---------------------------------------------------------------------------


def test_audit_entry_compute_hash_returns_hex():
    entry = AuditEntry(
        seq=0,
        timestamp="2026-01-01T00:00:00+00:00",
        tool_name="health_check",
        caller_id="subject:ops",
        param_digest="abc",
        result_hash="def",
        duration_ms=1.0,
        previous_hash="0",
    )
    h = entry.compute_hash()
    assert isinstance(h, str)
    assert len(h) == 64
    int(h, 16)  # valid hex


def test_audit_entry_hash_changes_with_tool_name():
    base = AuditEntry(
        seq=0, timestamp="T", tool_name="tool_a",
        caller_id="c", param_digest="p", result_hash="r",
        duration_ms=1.0, previous_hash="0",
    )
    other = AuditEntry(
        seq=0, timestamp="T", tool_name="tool_b",
        caller_id="c", param_digest="p", result_hash="r",
        duration_ms=1.0, previous_hash="0",
    )
    assert base.compute_hash() != other.compute_hash()


def test_audit_entry_hash_chains_previous():
    """entry.previous_hash must influence entry.entry_hash."""
    entry_a = AuditEntry(
        seq=1, timestamp="T", tool_name="t",
        caller_id="c", param_digest="p", result_hash="r",
        duration_ms=1.0, previous_hash="prev-hash-a",
    )
    entry_b = AuditEntry(
        seq=1, timestamp="T", tool_name="t",
        caller_id="c", param_digest="p", result_hash="r",
        duration_ms=1.0, previous_hash="prev-hash-b",
    )
    assert entry_a.compute_hash() != entry_b.compute_hash()


def test_audit_entry_finalise_sets_entry_hash():
    entry = AuditEntry(
        seq=0, timestamp="T", tool_name="t",
        caller_id="c", param_digest="p", result_hash="r",
        duration_ms=1.0, previous_hash="0",
    )
    assert entry.entry_hash == ""
    finalised = entry.finalise()
    assert finalised.entry_hash != ""
    assert finalised.entry_hash == finalised.compute_hash()


# ---------------------------------------------------------------------------
# AuditLedger — append and validate_chain
# ---------------------------------------------------------------------------


def test_ledger_append_returns_entry(tmp_path):
    ledger = _make_ledger(tmp_path)
    entry = _append_entry(ledger)
    assert isinstance(entry, AuditEntry)
    assert entry.seq == 0
    assert entry.tool_name == "health_check"


def test_ledger_append_increments_seq(tmp_path):
    ledger = _make_ledger(tmp_path)
    e1 = _append_entry(ledger)
    e2 = _append_entry(ledger)
    assert e1.seq == 0
    assert e2.seq == 1


def test_ledger_first_entry_previous_hash_is_zero(tmp_path):
    ledger = _make_ledger(tmp_path)
    entry = _append_entry(ledger)
    assert entry.previous_hash == "0"


def test_ledger_second_entry_previous_hash_links_first(tmp_path):
    ledger = _make_ledger(tmp_path)
    e1 = _append_entry(ledger)
    e2 = _append_entry(ledger)
    assert e2.previous_hash == e1.entry_hash


def test_ledger_validate_chain_empty_is_valid(tmp_path):
    ledger = _make_ledger(tmp_path)
    ok, msg = ledger.validate_chain()
    assert ok is True


def test_ledger_validate_chain_single_entry_valid(tmp_path):
    ledger = _make_ledger(tmp_path)
    _append_entry(ledger)
    ok, msg = ledger.validate_chain()
    assert ok is True


def test_ledger_validate_chain_multiple_entries_valid(tmp_path):
    ledger = _make_ledger(tmp_path)
    for _ in range(10):
        _append_entry(ledger)
    ok, msg = ledger.validate_chain()
    assert ok is True


def test_ledger_validate_chain_detects_hash_tampering(tmp_path):
    """Modifying an entry's stored hash must fail chain validation."""
    ledger = _make_ledger(tmp_path)
    _append_entry(ledger)
    _append_entry(ledger)
    # Tamper the first entry's stored hash
    ledger._entries[0] = AuditEntry(
        seq=ledger._entries[0].seq,
        timestamp=ledger._entries[0].timestamp,
        tool_name=ledger._entries[0].tool_name,
        caller_id=ledger._entries[0].caller_id,
        param_digest=ledger._entries[0].param_digest,
        result_hash=ledger._entries[0].result_hash,
        duration_ms=ledger._entries[0].duration_ms,
        previous_hash=ledger._entries[0].previous_hash,
        entry_hash="tampered-hash-000",
    )
    ok, msg = ledger.validate_chain()
    assert ok is False
    assert "0" in msg or "tampered" in msg.lower() or "hash mismatch" in msg.lower()


def test_ledger_validate_chain_detects_chain_break(tmp_path):
    """Breaking the previous_hash linkage must fail chain validation."""
    ledger = _make_ledger(tmp_path)
    e1 = _append_entry(ledger)
    _append_entry(ledger)
    # Tamper entry 1's previous_hash to break the link
    ledger._entries[1] = AuditEntry(
        seq=ledger._entries[1].seq,
        timestamp=ledger._entries[1].timestamp,
        tool_name=ledger._entries[1].tool_name,
        caller_id=ledger._entries[1].caller_id,
        param_digest=ledger._entries[1].param_digest,
        result_hash=ledger._entries[1].result_hash,
        duration_ms=ledger._entries[1].duration_ms,
        previous_hash="broken-link",  # must not match e1.entry_hash
        entry_hash=ledger._entries[1].entry_hash,
    )
    ok, msg = ledger.validate_chain()
    assert ok is False


# ---------------------------------------------------------------------------
# AuditLedger — persistence
# ---------------------------------------------------------------------------


def test_ledger_persists_to_file(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = AuditLedger(audit_path=path)
    _append_entry(ledger)
    assert path.exists()
    content = path.read_text()
    assert "health_check" in content


def test_ledger_loads_existing_on_construction(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger1 = AuditLedger(audit_path=path)
    _append_entry(ledger1)
    _append_entry(ledger1)

    ledger2 = AuditLedger(audit_path=path)
    assert ledger2.entry_count == 2


# ---------------------------------------------------------------------------
# AuditLedger — query
# ---------------------------------------------------------------------------


def test_ledger_query_by_tool_name(tmp_path):
    ledger = _make_ledger(tmp_path)
    _append_entry(ledger, tool="health_check")
    _append_entry(ledger, tool="phase_mirror")
    _append_entry(ledger, tool="health_check")

    results = ledger.query(tool_name="health_check")
    assert all(e.tool_name == "health_check" for e in results)
    assert len(results) == 2


def test_ledger_query_returns_most_recent_first(tmp_path):
    ledger = _make_ledger(tmp_path)
    for _ in range(5):
        _append_entry(ledger)
    results = ledger.query()
    seqs = [e.seq for e in results]
    assert seqs == sorted(seqs, reverse=True)


def test_ledger_query_limit(tmp_path):
    ledger = _make_ledger(tmp_path)
    for _ in range(10):
        _append_entry(ledger)
    results = ledger.query(limit=3)
    assert len(results) == 3


# ---------------------------------------------------------------------------
# CallMetrics
# ---------------------------------------------------------------------------


def test_call_metrics_from_empty_ledger(tmp_path):
    ledger = _make_ledger(tmp_path)
    metrics = ledger.get_stats()
    assert metrics.total_calls == 0


def test_call_metrics_counts_entries(tmp_path):
    ledger = _make_ledger(tmp_path)
    for _ in range(5):
        _append_entry(ledger)
    metrics = ledger.get_stats()
    assert metrics.total_calls == 5


def test_call_metrics_tools_called_map(tmp_path):
    ledger = _make_ledger(tmp_path)
    _append_entry(ledger, tool="health_check")
    _append_entry(ledger, tool="phase_mirror")
    _append_entry(ledger, tool="health_check")
    metrics = ledger.get_stats()
    assert metrics.tools_called["health_check"] == 2
    assert metrics.tools_called["phase_mirror"] == 1


# ---------------------------------------------------------------------------
# AuditMiddleware
# ---------------------------------------------------------------------------


def test_audit_middleware_logs_call(tmp_path):
    ledger = _make_ledger(tmp_path)

    def dispatch(tool_name, **kwargs):
        return {"status": "pass"}

    middleware = AuditMiddleware(ledger, dispatch)
    result = middleware.call_tool("health_check", caller_id="subject:ops")
    assert result == {"status": "pass"}
    assert ledger.entry_count == 1
    assert ledger._entries[0].tool_name == "health_check"


def test_audit_middleware_does_not_suppress_tool_errors(tmp_path):
    ledger = _make_ledger(tmp_path)

    def dispatch(tool_name, **kwargs):
        raise ValueError("tool failed")

    middleware = AuditMiddleware(ledger, dispatch)
    with pytest.raises(ValueError):
        middleware.call_tool("health_check", caller_id="subject:ops")


# ---------------------------------------------------------------------------
# AuditExporter
# ---------------------------------------------------------------------------


def test_exporter_to_json(tmp_path):
    import json
    ledger = _make_ledger(tmp_path)
    _append_entry(ledger)
    exporter = AuditExporter(ledger)
    output = exporter.to_json()
    entries = json.loads(output)
    assert isinstance(entries, list)
    assert len(entries) == 1


def test_exporter_to_csv(tmp_path):
    ledger = _make_ledger(tmp_path)
    _append_entry(ledger)
    exporter = AuditExporter(ledger)
    csv_output = exporter.to_csv()
    assert "health_check" in csv_output
    assert "seq" in csv_output


def test_exporter_compliance_report(tmp_path):
    ledger = _make_ledger(tmp_path)
    _append_entry(ledger)
    exporter = AuditExporter(ledger)
    report = exporter.compliance_report()
    assert report["chain_integrity"]["valid"] is True
    assert report["total_entries"] == 1
    assert "generated_at" in report


def test_exporter_compliance_report_detects_tampering(tmp_path):
    ledger = _make_ledger(tmp_path)
    _append_entry(ledger)
    # Tamper an entry
    ledger._entries[0] = AuditEntry(
        seq=0, timestamp="T", tool_name="t",
        caller_id="c", param_digest="p", result_hash="r",
        duration_ms=1.0, previous_hash="0",
        entry_hash="bad-hash",
    )
    exporter = AuditExporter(ledger)
    report = exporter.compliance_report()
    assert report["chain_integrity"]["valid"] is False
