"""
A-02: MCP Tool Invocation Audit Logger

Implements Contract 4: Every tool invocation emits audit record with correlation ID.
"""

from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, List
from enum import Enum
from datetime import datetime
import hashlib
import json
import uuid
from pathlib import Path


class AuditEventType(Enum):
    """Type of audit event."""
    TOOL_INVOCATION_START = "tool_invocation_start"
    TOOL_INVOCATION_SUCCESS = "tool_invocation_success"
    TOOL_INVOCATION_FAILURE = "tool_invocation_failure"
    STARTUP_EVENT = "startup_event"
    SHUTDOWN_EVENT = "shutdown_event"


@dataclass(frozen=True)
class AuditRecord:
    """Audit record for a tool invocation."""
    timestamp: str  # ISO8601 UTC
    correlation_id: str
    event_type: str
    tool_name: str
    tool_version: str
    args_hash: str
    result: str  # success|failure
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    duration_ms: int = 0
    caller: Optional[str] = None
    policy_decision: Optional[str] = None
    checkpoint_id: Optional[str] = None
    trace_id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, excluding None values."""
        return {
            k: v for k, v in asdict(self).items()
            if v is not None
        }
    
    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())


class CorrelationIDGenerator:
    """Generates and manages correlation IDs."""
    
    @staticmethod
    def generate() -> str:
        """Generate a new correlation ID."""
        return f"corr-{uuid.uuid4().hex[:16]}"


def hash_args(args: Any) -> str:
    """Hash tool arguments for audit trail."""
    try:
        # Serialize args to JSON for consistent hashing
        serialized = json.dumps(args, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]
    except Exception:
        return "hash-error"


class ToolInvocationAuditor:
    """Audits tool invocations with correlation metadata."""
    
    def __init__(self, audit_log_path: Optional[str] = None):
        """Initialize auditor."""
        self.audit_log_path = audit_log_path
        self.records: List[AuditRecord] = []
        self._active_correlations: Dict[str, str] = {}
    
    def generate_correlation_id(self) -> str:
        """Generate correlation ID for request."""
        return CorrelationIDGenerator.generate()
    
    def record_tool_invocation(
        self,
        tool_name: str,
        tool_version: str,
        args: Dict[str, Any],
        correlation_id: str,
        caller: Optional[str] = None,
    ) -> str:
        """
        Record tool invocation start.
        
        Returns:
            correlation_id for this invocation
        """
        args_hash = hash_args(args)
        
        record = AuditRecord(
            timestamp=datetime.utcnow().isoformat() + "Z",
            correlation_id=correlation_id,
            event_type=AuditEventType.TOOL_INVOCATION_START.value,
            tool_name=tool_name,
            tool_version=tool_version,
            args_hash=args_hash,
            result="pending",
            caller=caller,
        )
        
        self.records.append(record)
        self._active_correlations[correlation_id] = tool_name
        
        return correlation_id
    
    def record_invocation_success(
        self,
        correlation_id: str,
        duration_ms: int,
        checkpoint_id: Optional[str] = None,
    ) -> AuditRecord:
        """Record successful tool invocation."""
        tool_name = self._active_correlations.get(correlation_id, "unknown")
        
        # Find the start record to get tool_version
        tool_version = "unknown"
        for record in reversed(self.records):
            if record.correlation_id == correlation_id:
                tool_version = record.tool_version
                break
        
        record = AuditRecord(
            timestamp=datetime.utcnow().isoformat() + "Z",
            correlation_id=correlation_id,
            event_type=AuditEventType.TOOL_INVOCATION_SUCCESS.value,
            tool_name=tool_name,
            tool_version=tool_version,
            args_hash="completed",
            result="success",
            duration_ms=duration_ms,
            checkpoint_id=checkpoint_id,
        )
        
        self.records.append(record)
        
        if correlation_id in self._active_correlations:
            del self._active_correlations[correlation_id]
        
        return record
    
    def record_invocation_failure(
        self,
        correlation_id: str,
        error_code: str,
        error_message: str,
        duration_ms: int = 0,
    ) -> AuditRecord:
        """Record failed tool invocation."""
        tool_name = self._active_correlations.get(correlation_id, "unknown")
        
        # Find the start record to get tool_version and args
        tool_version = "unknown"
        for record in reversed(self.records):
            if record.correlation_id == correlation_id:
                tool_version = record.tool_version
                break
        
        record = AuditRecord(
            timestamp=datetime.utcnow().isoformat() + "Z",
            correlation_id=correlation_id,
            event_type=AuditEventType.TOOL_INVOCATION_FAILURE.value,
            tool_name=tool_name,
            tool_version=tool_version,
            args_hash="error",
            result="failure",
            error_code=error_code,
            error_message=error_message,
            duration_ms=duration_ms,
        )
        
        self.records.append(record)
        
        if correlation_id in self._active_correlations:
            del self._active_correlations[correlation_id]
        
        return record
    
    def get_records_for_correlation(self, correlation_id: str) -> List[AuditRecord]:
        """Get all records for a correlation ID."""
        return [r for r in self.records if r.correlation_id == correlation_id]
    
    def get_records_for_tool(self, tool_name: str) -> List[AuditRecord]:
        """Get all records for a tool."""
        return [r for r in self.records if r.tool_name == tool_name]
    
    def get_all_records(self) -> List[AuditRecord]:
        """Get all audit records."""
        return list(self.records)
    
    def save_to_file(self, path: str) -> None:
        """Save audit log to file."""
        try:
            with open(path, 'w') as f:
                for record in self.records:
                    f.write(record.to_json() + "\n")
        except Exception as e:
            raise RuntimeError(f"Failed to save audit log: {e}")
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary statistics."""
        success_count = sum(
            1 for r in self.records
            if r.result == "success"
        )
        failure_count = sum(
            1 for r in self.records
            if r.result == "failure"
        )
        
        total_duration = sum(r.duration_ms for r in self.records)
        
        tool_counts = {}
        for record in self.records:
            tool_counts[record.tool_name] = tool_counts.get(record.tool_name, 0) + 1
        
        return {
            "total_records": len(self.records),
            "success_count": success_count,
            "failure_count": failure_count,
            "success_rate": (
                success_count / len(self.records) * 100
                if self.records else 0
            ),
            "total_duration_ms": total_duration,
            "tool_invocation_counts": tool_counts,
            "unique_correlation_ids": len(set(r.correlation_id for r in self.records)),
        }


# Test helper
def create_test_auditor() -> ToolInvocationAuditor:
    """Create a test auditor."""
    return ToolInvocationAuditor()
