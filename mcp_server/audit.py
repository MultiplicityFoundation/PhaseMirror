"""F-07: Audit Logging and Monitoring.

Implements an immutable, hash-chained audit ledger for every MCP tool call.
The hash chain formula follows ADR-029's Merkle construction adapted for
sequential audit entries:

    h_i = SHA256(seq ‖ ts ‖ tool ‖ caller ‖ param_digest ‖ result_hash ‖ h_{i-1})

where ‖ denotes field-separator-delimited concatenation.

The ``AuditLedger`` persists each entry to a JSONL file before returning
from ``append()``, ensuring no call is lost on crash.  ``validate_chain()``
is called on server startup to detect any tampering.

``AuditMiddleware`` wraps ``RegistryBackedServer.call_tool()`` and logs every
dispatch non-blocking (the write is done in the calling thread but isolated
from the tool's return value path via a try/finally so audit failures never
suppress tool errors).

``CallMetrics`` and ``AuditExporter`` provide per-tool statistics and
compliance export (JSON, CSV).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

DEFAULT_AUDIT_PATH = (
    Path(__file__).resolve().parent.parent / "state" / "mcp_audit_ledger.jsonl"
)


# ---------------------------------------------------------------------------
# AuditEntry
# ---------------------------------------------------------------------------


@dataclass
class AuditEntry:
    """A single immutable audit record.

    The ``entry_hash`` field is computed from all other fields plus the
    ``previous_hash``.  It must not be set by callers; use
    :meth:`compute_hash` after constructing the entry.
    """

    seq: int
    timestamp: str          # ISO 8601 UTC
    tool_name: str
    caller_id: str
    param_digest: str       # SHA-256 hex of JSON-serialised parameters
    result_hash: str        # SHA-256 hex of JSON-serialised result
    duration_ms: float
    previous_hash: str      # Entry hash of the previous entry ("0" for genesis)
    entry_hash: str = ""    # Computed; empty until finalised

    _SEPARATOR = "|"

    def compute_hash(self) -> str:
        """Return the canonical SHA-256 hash for this entry.

        Hash input (UTF-8 encoded):
            seq|timestamp|tool_name|caller_id|param_digest|result_hash|duration_ms|previous_hash
        """
        parts = [
            str(self.seq),
            self.timestamp,
            self.tool_name,
            self.caller_id,
            self.param_digest,
            self.result_hash,
            f"{self.duration_ms:.3f}",
            self.previous_hash,
        ]
        raw = self._SEPARATOR.join(parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def finalise(self) -> "AuditEntry":
        """Return a new entry with ``entry_hash`` set from :meth:`compute_hash`."""
        return AuditEntry(
            seq=self.seq,
            timestamp=self.timestamp,
            tool_name=self.tool_name,
            caller_id=self.caller_id,
            param_digest=self.param_digest,
            result_hash=self.result_hash,
            duration_ms=self.duration_ms,
            previous_hash=self.previous_hash,
            entry_hash=self.compute_hash(),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json_line(self) -> str:
        return json.dumps(self.to_dict()) + "\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _digest(obj: Any) -> str:
    """Return a short SHA-256 hex digest of the JSON representation of *obj*."""
    try:
        serialised = json.dumps(obj, sort_keys=True, default=str)
    except Exception:
        serialised = str(obj)
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# AuditLedger
# ---------------------------------------------------------------------------


class AuditLedger:
    """Append-only hash-chained audit ledger.

    Entries are persisted to a JSONL file (one JSON object per line) before
    ``append()`` returns, guaranteeing durability.  The in-memory list mirrors
    the file for fast query and validation.

    Usage::

        ledger = AuditLedger()
        entry = ledger.append(
            tool_name="health_check",
            caller_id="subject:ops@example.com",
            parameters={},
            result={"status": "pass"},
            duration_ms=4.2,
        )
        ok, msg = ledger.validate_chain()
    """

    def __init__(self, audit_path: Path | None = None) -> None:
        self.audit_path = audit_path or DEFAULT_AUDIT_PATH
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: List[AuditEntry] = []
        self._load_existing()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(
        self,
        *,
        tool_name: str,
        caller_id: str,
        parameters: Any,
        result: Any,
        duration_ms: float,
    ) -> AuditEntry:
        """Append and persist a new entry; return the finalised ``AuditEntry``.

        The entry hash is computed before persistence so the on-disk record
        is already the finalised form.
        """
        previous_hash = self._entries[-1].entry_hash if self._entries else "0"
        seq = len(self._entries)

        entry = AuditEntry(
            seq=seq,
            timestamp=_utc_now(),
            tool_name=tool_name,
            caller_id=caller_id,
            param_digest=_digest(parameters),
            result_hash=_digest(result),
            duration_ms=duration_ms,
            previous_hash=previous_hash,
        ).finalise()

        # Persist before appending to the in-memory list so a crash between
        # write and list-append doesn't leave the ledger inconsistent.
        with open(self.audit_path, "a", encoding="utf-8") as fh:
            fh.write(entry.to_json_line())

        self._entries.append(entry)
        return entry

    def validate_chain(self) -> Tuple[bool, str]:
        """Verify the hash chain for all entries.

        Returns:
            ``(True, "Chain valid")`` if the chain is intact, or
            ``(False, "<description of first violation>")`` otherwise.
        """
        for i, entry in enumerate(self._entries):
            # Recompute and compare
            expected = entry.compute_hash()
            if expected != entry.entry_hash:
                return False, f"Entry {i} (seq={entry.seq}, tool={entry.tool_name}): hash mismatch"

            # Verify linkage
            expected_prev = self._entries[i - 1].entry_hash if i > 0 else "0"
            if entry.previous_hash != expected_prev:
                return (
                    False,
                    f"Entry {i} (seq={entry.seq}): chain break — "
                    f"previous_hash={entry.previous_hash!r} "
                    f"expected={expected_prev!r}",
                )

        return True, "Chain valid"

    def query(
        self,
        *,
        tool_name: Optional[str] = None,
        caller_id: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[AuditEntry]:
        """Return entries matching the given filters (most recent first by default)."""
        results = list(self._entries)
        if tool_name is not None:
            results = [e for e in results if e.tool_name == tool_name]
        if caller_id is not None:
            results = [e for e in results if e.caller_id == caller_id]
        if since is not None:
            results = [e for e in results if e.timestamp >= since]
        if until is not None:
            results = [e for e in results if e.timestamp <= until]
        results.reverse()  # most recent first
        if limit is not None:
            results = results[:limit]
        return results

    def get_stats(self) -> "CallMetrics":
        """Return aggregate statistics for all recorded calls."""
        return CallMetrics.from_entries(self._entries)

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_existing(self) -> None:
        """Load existing entries from the JSONL file on disk."""
        if not self.audit_path.exists():
            return
        with open(self.audit_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    self._entries.append(AuditEntry(**d))
                except Exception:
                    pass  # corrupt line — skip gracefully


# ---------------------------------------------------------------------------
# CallMetrics
# ---------------------------------------------------------------------------


@dataclass
class CallMetrics:
    """Per-ledger call statistics (p50, p95, p99 latencies in ms)."""

    total_calls: int
    error_count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    tools_called: Dict[str, int]  # tool_name -> count

    @classmethod
    def from_entries(cls, entries: List[AuditEntry]) -> "CallMetrics":
        if not entries:
            return cls(
                total_calls=0,
                error_count=0,
                p50_ms=0.0,
                p95_ms=0.0,
                p99_ms=0.0,
                tools_called={},
            )
        durations = sorted(e.duration_ms for e in entries)
        n = len(durations)

        def percentile(p: float) -> float:
            idx = max(0, int(n * p / 100) - 1)
            return durations[idx]

        tools_called: Dict[str, int] = {}
        for e in entries:
            tools_called[e.tool_name] = tools_called.get(e.tool_name, 0) + 1

        return cls(
            total_calls=n,
            error_count=0,  # errors tracked separately when AuditMiddleware is used
            p50_ms=percentile(50),
            p95_ms=percentile(95),
            p99_ms=percentile(99),
            tools_called=tools_called,
        )


# ---------------------------------------------------------------------------
# AuditMiddleware
# ---------------------------------------------------------------------------


class AuditMiddleware:
    """Wraps a ``call_tool`` callable and logs every invocation to *ledger*.

    Non-blocking design: the audit write happens synchronously in the calling
    thread, but in a try/finally block so audit failures never suppress the
    original tool error.

    Usage::

        middleware = AuditMiddleware(ledger, underlying_call_tool)
        result = middleware.call_tool("health_check", caller_id="subject:ops", **kwargs)
    """

    def __init__(
        self,
        ledger: AuditLedger,
        dispatch: Callable[..., Any],
    ) -> None:
        self.ledger = ledger
        self._dispatch = dispatch

    def call_tool(
        self,
        tool_name: str,
        *,
        caller_id: str = "anonymous",
        **kwargs: Any,
    ) -> Any:
        """Dispatch *tool_name* and log the invocation to the ledger."""
        start = time.monotonic()
        result = None
        try:
            result = self._dispatch(tool_name, **kwargs)
            return result
        finally:
            duration_ms = (time.monotonic() - start) * 1000
            try:
                self.ledger.append(
                    tool_name=tool_name,
                    caller_id=caller_id,
                    parameters=kwargs,
                    result=result,
                    duration_ms=duration_ms,
                )
            except Exception:
                pass  # audit write must never propagate and mask tool errors


# ---------------------------------------------------------------------------
# AuditExporter
# ---------------------------------------------------------------------------


class AuditExporter:
    """Export audit entries in JSON, CSV, and compliance report formats."""

    def __init__(self, ledger: AuditLedger) -> None:
        self.ledger = ledger

    def to_json(self, entries: Optional[List[AuditEntry]] = None) -> str:
        """Return a JSON array of all entries (or a provided subset)."""
        target = entries if entries is not None else self.ledger.query()
        return json.dumps([e.to_dict() for e in target], indent=2)

    def to_csv(self, entries: Optional[List[AuditEntry]] = None) -> str:
        """Return a CSV string of all entries (or a provided subset)."""
        target = entries if entries is not None else self.ledger.query()
        buf = io.StringIO()
        if not target:
            return ""
        fieldnames = list(target[0].to_dict().keys())
        writer = csv.DictWriter(buf, fieldnames=fieldnames)
        writer.writeheader()
        for e in target:
            writer.writerow(e.to_dict())
        return buf.getvalue()

    def compliance_report(self) -> Dict[str, Any]:
        """Return a compliance summary dict with chain integrity and metrics."""
        chain_valid, chain_message = self.ledger.validate_chain()
        metrics = self.ledger.get_stats()
        return {
            "generated_at": _utc_now(),
            "total_entries": self.ledger.entry_count,
            "chain_integrity": {
                "valid": chain_valid,
                "message": chain_message,
            },
            "metrics": {
                "total_calls": metrics.total_calls,
                "p50_ms": metrics.p50_ms,
                "p95_ms": metrics.p95_ms,
                "p99_ms": metrics.p99_ms,
                "tools_called": metrics.tools_called,
            },
        }
