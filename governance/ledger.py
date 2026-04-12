"""Governance Ledger for Immutability Trust Anchor.

Per ADR-012, the governance ledger stores Merkle root commits that establish
the root of trust for all immutable files. This breaks the self-reference paradox
by storing the root externally in a ledger, rather than embedding hashes in files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Any
import json
import hashlib
import os
from enum import Enum
from uuid import uuid4


class LedgerEntryType(Enum):
    """Types of governance ledger entries."""
    GOVERNANCE_ROOT_COMMIT = "GOVERNANCE_ROOT_COMMIT"
    GOVERNANCE_ACTION = "GOVERNANCE_ACTION"
    AUDIT_LOG = "AUDIT_LOG"


@dataclass
class ImmutableFileRecord:
    """Record of a single immutable file's hash."""
    path: str
    hash: str
    raw_hash: str = ""
    root_input_hash: str = ""
    hash_semantics: str = "raw_bytes"
    size_bytes: int = 0


@dataclass
class GovernanceRootCommit:
    """Ledger entry for a Merkle root commit."""
    type: str = LedgerEntryType.GOVERNANCE_ROOT_COMMIT.value
    governance_version: str = "v0.0.0"
    merkle_root: str = ""  # Root hash of immutable files
    immutable_files: list[ImmutableFileRecord] = field(default_factory=list)
    signed_by: str = ""  # User or governance action key
    timestamp: str = ""  # ISO8601
    previous_root_tx_id: Optional[int] = None
    governance_notes: str = ""
    # ADR-AGI-002: Governance ledger binding contract
    quorum_threshold: int = 2  # Minimum quorum for governance actions
    rollback_ref: str = "main"  # Git reference for rollback
    review_cadence_days: int = 30  # Days between governance reviews
    audit_trigger: str = "governance_action"  # Event triggering audit

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "type": self.type,
            "governance_version": self.governance_version,
            "merkle_root": self.merkle_root,
            "immutable_files": [
                {
                    "path": f.path,
                    "hash": f.hash,
                    "raw_hash": f.raw_hash,
                    "root_input_hash": f.root_input_hash,
                    "hash_semantics": f.hash_semantics,
                    "size_bytes": f.size_bytes,
                }
                for f in self.immutable_files
            ],
            "signed_by": self.signed_by,
            "timestamp": self.timestamp,
            "previous_root_tx_id": self.previous_root_tx_id,
            "governance_notes": self.governance_notes,
            # ADR-AGI-002: Governance ledger binding contract
            "quorum_threshold": self.quorum_threshold,
            "rollback_ref": self.rollback_ref,
            "review_cadence_days": self.review_cadence_days,
            "audit_trigger": self.audit_trigger,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GovernanceRootCommit:
        """Reconstruct from dictionary."""
        files = [
            ImmutableFileRecord(
                path=f["path"],
                hash=f["hash"],
                raw_hash=f.get("raw_hash", f.get("hash", "")),
                root_input_hash=f.get("root_input_hash", f.get("hash", "")),
                hash_semantics=f.get("hash_semantics", "raw_bytes"),
                size_bytes=f.get("size_bytes", 0),
            )
            for f in data.get("immutable_files", [])
        ]
        return cls(
            type=data.get("type", LedgerEntryType.GOVERNANCE_ROOT_COMMIT.value),
            governance_version=data.get("governance_version", "v0.0.0"),
            merkle_root=data.get("merkle_root", ""),
            immutable_files=files,
            signed_by=data.get("signed_by", ""),
            timestamp=data.get("timestamp", ""),
            previous_root_tx_id=data.get("previous_root_tx_id"),
            governance_notes=data.get("governance_notes", ""),
            # ADR-AGI-002: Governance ledger binding contract
            quorum_threshold=data.get("quorum_threshold", 2),
            rollback_ref=data.get("rollback_ref", "main"),
            review_cadence_days=data.get("review_cadence_days", 30),
            audit_trigger=data.get("audit_trigger", "governance_action"),
        )


class LedgerStore:
    """In-memory governance ledger for Merkle root tracking.
    
    In production, this would be backed by a durable store (database, filesystem, etc).
    For testing and startup verification, an in-memory store is sufficient.
    """

    def __init__(self, storage_path: Optional[Path] = None):
        """Initialize ledger.
        
        Args:
            storage_path: Optional path for persistent storage.
                If None, uses in-memory store only.
        """
        self.storage_path = storage_path
        self._entries: dict[int, GovernanceRootCommit] = {}
        self._next_id = 1
        
        if storage_path and storage_path.exists():
            self._load_from_disk()

    def _load_from_disk(self) -> None:
        """Load entries from disk storage."""
        if not self.storage_path or not self.storage_path.exists():
            return
        
        try:
            with open(self.storage_path, 'r') as f:
                data = json.load(f)
                for tx_id_str, entry_data in data.items():
                    tx_id = int(tx_id_str)
                    self._entries[tx_id] = GovernanceRootCommit.from_dict(entry_data)
                    self._next_id = max(self._next_id, tx_id + 1)
        except Exception as e:
            raise RuntimeError(f"Failed to load ledger from {self.storage_path}: {e}")

    def _save_to_disk(self) -> None:
        """Save entries to disk storage."""
        if not self.storage_path:
            return
        
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                str(tx_id): entry.to_dict()
                for tx_id, entry in self._entries.items()
            }
            with open(self.storage_path, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            raise RuntimeError(f"Failed to save ledger to {self.storage_path}: {e}")

    def create_entry(self, entry: GovernanceRootCommit) -> int:
        """Create a new ledger entry.
        
        Args:
            entry: The entry to create
            
        Returns:
            Transaction ID assigned to the entry
        """
        tx_id = self._next_id
        self._next_id += 1
        
        # Set timestamp if not set
        if not entry.timestamp:
            entry.timestamp = datetime.utcnow().isoformat() + "Z"
        
        self._entries[tx_id] = entry
        self._save_to_disk()
        return tx_id

    def get_entry(self, tx_id: int) -> Optional[GovernanceRootCommit]:
        """Fetch entry by transaction ID.
        
        Args:
            tx_id: Transaction ID
            
        Returns:
            Entry if found, None otherwise
        """
        return self._entries.get(tx_id)

    def get_latest_root_commit(self) -> Optional[tuple[int, GovernanceRootCommit]]:
        """Get the latest (highest tx_id) root commit entry.
        
        Returns:
            (tx_id, entry) tuple if found, None otherwise
        """
        root_commits = [
            (tx_id, entry)
            for tx_id, entry in self._entries.items()
            if entry.type == LedgerEntryType.GOVERNANCE_ROOT_COMMIT.value
        ]
        
        if not root_commits:
            return None
        
        # Sort by tx_id, return the latest
        root_commits.sort(key=lambda x: x[0])
        return root_commits[-1]

    def list_entries(self) -> list[tuple[int, GovernanceRootCommit]]:
        """List all entries in order.
        
        Returns:
            List of (tx_id, entry) tuples sorted by tx_id
        """
        items = sorted(self._entries.items(), key=lambda x: x[0])
        return items


def create_governance_root_commit(
    merkle_root: str,
    immutable_files: list[Path],
    governance_critical_tools: Optional[list[Path]] = None,
    governance_version: str = "v0.1.0",
    signed_by: str = "test",
    notes: str = "",
    previous_root_tx_id: Optional[int] = None,
) -> GovernanceRootCommit:
    """Create a governance root commit entry.
    
    Utility function to create a proper GovernanceRootCommit with
    file hash records for audit trail.
    
    Args:
        merkle_root: The computed Merkle root hash
        immutable_files: List of paths to immutable files
        governance_critical_tools: Optional governance-critical tool files included
            in the same root of trust
        governance_version: Version string (e.g., "v0.1.0")
        signed_by: User or governance key identifier
        notes: Human-readable governance notes
        previous_root_tx_id: If this is an update, the previous tx_id
        
    Returns:
        GovernanceRootCommit entry ready for ledger
    """
    from contracts.shared.merkle_root import governance_root_hash_details
    
    file_records = []
    seen_paths: set[str] = set()
    all_files = list(immutable_files)
    if governance_critical_tools:
        all_files.extend(governance_critical_tools)

    for file_path in all_files:
        path_key = str(file_path)
        if path_key in seen_paths:
            continue
        seen_paths.add(path_key)
        try:
            hash_details = governance_root_hash_details(file_path)
            file_hash = str(hash_details["root_input_hash"])
            size_bytes = file_path.stat().st_size if file_path.exists() else 0
            file_records.append(
                ImmutableFileRecord(
                    path=str(file_path),
                    hash=file_hash,
                    raw_hash=str(hash_details["raw_hash"]),
                    root_input_hash=str(hash_details["root_input_hash"]),
                    hash_semantics=str(hash_details["hash_semantics"]),
                    size_bytes=size_bytes,
                )
            )
        except Exception as e:
            # Include failed hashes in record for audit
            file_records.append(
                ImmutableFileRecord(
                    path=str(file_path),
                    hash=f"ERROR: {e}",
                    raw_hash=f"ERROR: {e}",
                    root_input_hash=f"ERROR: {e}",
                    hash_semantics="error",
                )
            )
    
    return GovernanceRootCommit(
        governance_version=governance_version,
        merkle_root=merkle_root,
        immutable_files=file_records,
        signed_by=signed_by,
        timestamp=datetime.utcnow().isoformat() + "Z",
        previous_root_tx_id=previous_root_tx_id,
        governance_notes=notes,
    )


@dataclass
class AuditLedgerEntry:
    """In-memory audit log entry for Phase Mirror evaluation reports."""

    sequence_num: int
    evaluation_id: str
    timestamp: str
    report: Any
    prev_hash: str = ""
    payload_hash: str = ""
    entry_hash: str = ""
    type: str = LedgerEntryType.AUDIT_LOG.value

    def to_dict(self) -> dict[str, Any]:
        report_payload = self.report.to_dict() if hasattr(self.report, "to_dict") else self.report
        all_tensions = self.report.get_all_tensions() if hasattr(self.report, "get_all_tensions") else ()
        return {
            "type": self.type,
            "sequence_num": self.sequence_num,
            "evaluation_id": self.evaluation_id,
            "timestamp": self.timestamp,
            "report": report_payload,
            "payload_hash": self.payload_hash,
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
            "tension_audit": {
                "total_tensions": len(all_tensions),
                "decision_tensions": len(getattr(self.report, "tensions", ())),
                "audit_tensions": len(getattr(self.report, "suppressed_tensions", ())),
                "all_tension_ids": [signal.signal_id for signal in all_tensions],
            },
        }


class AuditLedger:
    """Minimal in-memory audit ledger for complete tension reports."""

    def __init__(self, location: Optional[Path] = None, auto_load: bool = True) -> None:
        self.location = location
        self.entries: list[AuditLedgerEntry] = []
        if self.location and auto_load and self.location.exists():
            loaded = AuditLedger.load(self.location)
            self.entries = loaded.entries

    def append(
        self,
        report: Any,
        *,
        timestamp: str | None = None,
        evaluation_id: str | None = None,
        persist: bool | None = None,
    ) -> AuditLedgerEntry:
        sequence_num = len(self.entries) + 1
        resolved_timestamp = timestamp or (datetime.utcnow().isoformat() + "Z")
        report_payload = report.to_dict() if hasattr(report, "to_dict") else report
        metadata = report_payload.get("metadata", {}) if isinstance(report_payload, dict) else {}
        resolved_evaluation_id = (
            evaluation_id
            or (metadata.get("evaluation_id") if isinstance(metadata, dict) else None)
            or f"eval-{uuid4().hex}"
        )
        payload_json = json.dumps(report_payload, sort_keys=True)
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        prev_hash = self.entries[-1].entry_hash if self.entries else ""
        entry_hash = hashlib.sha256(
            f"{sequence_num}:{resolved_evaluation_id}:{prev_hash}:{payload_hash}".encode("utf-8")
        ).hexdigest()

        entry = AuditLedgerEntry(
            sequence_num=sequence_num,
            evaluation_id=resolved_evaluation_id,
            timestamp=resolved_timestamp,
            report=report,
            prev_hash=prev_hash,
            payload_hash=payload_hash,
            entry_hash=entry_hash,
        )
        self.entries.append(entry)
        should_persist = persist if persist is not None else (self.location is not None)
        if should_persist and self.location is not None:
            self.save()
        return entry

    def query_by_id(self, evaluation_id: str) -> Optional[AuditLedgerEntry]:
        for entry in self.entries:
            if entry.evaluation_id == evaluation_id:
                return entry
        return None

    def query_by_timestamp(self, start_iso: str, end_iso: str) -> list[AuditLedgerEntry]:
        start = _parse_iso8601(start_iso)
        end = _parse_iso8601(end_iso)
        results: list[AuditLedgerEntry] = []
        for entry in self.entries:
            current = _parse_iso8601(entry.timestamp)
            if start <= current <= end:
                results.append(entry)
        return results

    def validate(self) -> bool:
        prev_hash = ""
        for entry in self.entries:
            report_payload = entry.report.to_dict() if hasattr(entry.report, "to_dict") else entry.report
            payload_json = json.dumps(report_payload, sort_keys=True)
            expected_payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
            if expected_payload_hash != entry.payload_hash:
                raise ValueError(
                    f"Payload hash mismatch at sequence {entry.sequence_num}: "
                    f"expected {expected_payload_hash}, got {entry.payload_hash}"
                )

            expected_entry_hash = hashlib.sha256(
                f"{entry.sequence_num}:{entry.evaluation_id}:{prev_hash}:{entry.payload_hash}".encode("utf-8")
            ).hexdigest()
            if expected_entry_hash != entry.entry_hash:
                raise ValueError(
                    f"Entry hash mismatch at sequence {entry.sequence_num}: "
                    f"expected {expected_entry_hash}, got {entry.entry_hash}"
                )

            if entry.prev_hash != prev_hash:
                raise ValueError(
                    f"Prev hash mismatch at sequence {entry.sequence_num}: "
                    f"expected {prev_hash}, got {entry.prev_hash}"
                )

            prev_hash = entry.entry_hash
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": {
                "entry_count": len(self.entries),
                "last_hash": self.entries[-1].entry_hash if self.entries else "",
                "exported_at": datetime.utcnow().isoformat() + "Z",
            },
            "entries": [entry.to_dict() for entry in self.entries],
        }

    def save(self, path: Optional[Path] = None) -> None:
        target = path or self.location
        if target is None:
            raise ValueError("AuditLedger.save requires a path or configured location")
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w") as f:
            json.dump(self.to_dict(), f, indent=2, sort_keys=True)

    @classmethod
    def load(cls, path: Path) -> "AuditLedger":
        with open(path, "r") as f:
            payload = json.load(f)
        entries_payload = payload.get("entries", []) if isinstance(payload, dict) else []

        ledger = cls(location=path, auto_load=False)
        for row in entries_payload:
            report_payload = row.get("report", {}) if isinstance(row, dict) else {}
            entry = AuditLedgerEntry(
                sequence_num=int(row.get("sequence_num", 0)),
                evaluation_id=str(row.get("evaluation_id", f"eval-{uuid4().hex}")),
                timestamp=str(row.get("timestamp", "")),
                report=report_payload,
                payload_hash=str(row.get("payload_hash", "")),
                prev_hash=str(row.get("prev_hash", "")),
                entry_hash=str(row.get("entry_hash", "")),
                type=str(row.get("type", LedgerEntryType.AUDIT_LOG.value)),
            )
            ledger.entries.append(entry)

        ledger.validate()
        return ledger

    def append_epsilon_adjustment(
        self,
        *,
        delta: float,
        new_epsilon: float,
        reason: str,
        epsilon_min_safe: float,
        max_circuit_steps: int,
        within_bounds: bool,
        timestamp: str | None = None,
    ) -> AuditLedgerEntry:
        report = {
            "kind": "EPSILON_ADJUST",
            "delta": delta,
            "new_epsilon": new_epsilon,
            "reason": reason,
            "metadata": {
                "epsilon_min_safe": epsilon_min_safe,
                "circuit_steps": max_circuit_steps,
                "within_bounds": within_bounds,
            },
        }
        return self.append(report, timestamp=timestamp)


def record_epsilon_adjustment(
    ledger: AuditLedger,
    *,
    delta: float,
    new_epsilon: float,
    reason: str,
    epsilon_min_safe: float,
    max_circuit_steps: int,
    within_bounds: bool,
    timestamp: str | None = None,
) -> AuditLedgerEntry:
    """Record an epsilon adjustment as an immutable audit event."""

    return ledger.append_epsilon_adjustment(
        delta=delta,
        new_epsilon=new_epsilon,
        reason=reason,
        epsilon_min_safe=epsilon_min_safe,
        max_circuit_steps=max_circuit_steps,
        within_bounds=within_bounds,
        timestamp=timestamp,
    )


DEFAULT_PHASE_MIRROR_LEDGER_PATH = Path("state/phase_mirror_audit_ledger.json")
_PHASE_MIRROR_AUDIT_LEDGER: AuditLedger | None = None


def get_phase_mirror_audit_ledger(path: Optional[Path] = None) -> AuditLedger:
    """Return a singleton audit ledger used for Phase Mirror outcomes."""

    global _PHASE_MIRROR_AUDIT_LEDGER
    env_path = os.getenv("PHASE_MIRROR_AUDIT_LEDGER_PATH")
    resolved_path = path or (Path(env_path) if env_path else DEFAULT_PHASE_MIRROR_LEDGER_PATH)
    if _PHASE_MIRROR_AUDIT_LEDGER is None:
        _PHASE_MIRROR_AUDIT_LEDGER = AuditLedger(location=resolved_path)
    return _PHASE_MIRROR_AUDIT_LEDGER


def _parse_iso8601(value: str) -> datetime:
    normalized = value
    if value.endswith("Z"):
        normalized = value[:-1] + "+00:00"
    return datetime.fromisoformat(normalized)


# ---------------------------------------------------------------------------
# GitLedger — git-backed deploy-branch audit trail (ADR-MVP-003 Lever 2)
# ---------------------------------------------------------------------------

import subprocess  # noqa: E402 — stdlib, no new dep


class GitLedger:
    """Thin git wrapper used by the MCP governance daemon as an immutable audit trail.

    Methods correspond 1-to-1 with calls in mcp_server/app.py:
      - head_sha()              → current HEAD of the deploy branch
      - get_ledger_history()    → recent commits as structured dicts
      - commit_proposal()       → write a JSON blob and commit it
      - rollback()              → git reset --hard <sha>
    """

    def __init__(self, repo_path: str = ".") -> None:
        self.repo_path = Path(repo_path).resolve()

    def _git(self, *args: str, check: bool = True) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.repo_path), *args],
            capture_output=True,
            text=True,
            check=check,
        )
        return result.stdout.strip()

    def head_sha(self) -> str:
        """Return the current HEAD commit SHA (short)."""
        try:
            return self._git("rev-parse", "--short", "HEAD")
        except subprocess.CalledProcessError:
            return "no-commits"

    def get_ledger_history(self, limit: int = 20) -> list[dict]:
        """Return the last *limit* commits as structured dicts."""
        try:
            log = self._git(
                "log", f"-{limit}",
                "--pretty=format:%H\t%ai\t%s",
            )
        except subprocess.CalledProcessError:
            return []
        entries = []
        for line in log.splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3:
                entries.append({"sha": parts[0], "timestamp": parts[1], "message": parts[2]})
        return entries

    def commit_proposal(
        self,
        proposal_id: str,
        delta: dict,
        rationale: str = "",
    ) -> str:
        """Write proposal delta to a JSON file and commit it. Returns commit SHA."""
        ledger_dir = self.repo_path / "state" / "proposals"
        ledger_dir.mkdir(parents=True, exist_ok=True)
        proposal_file = ledger_dir / f"{proposal_id}.json"
        entry = {
            "proposal_id": proposal_id,
            "rationale": rationale,
            "delta": delta,
            "committed_at": datetime.utcnow().isoformat() + "Z",
        }
        with open(proposal_file, "w") as f:
            json.dump(entry, f, indent=2, sort_keys=True)
        try:
            self._git("add", str(proposal_file))
            self._git(
                "commit",
                "--no-verify",
                "-m",
                f"proposal({proposal_id}): {rationale[:72]}",
            )
            return self._git("rev-parse", "--short", "HEAD")
        except subprocess.CalledProcessError:
            # If git commit fails (e.g. nothing to commit), return current HEAD
            return self.head_sha()

    def rollback(self, target_sha: str) -> None:
        """Hard-reset the deploy branch to *target_sha*."""
        # Validate sha looks like a hex string before passing to git (OWASP A03)
        if not all(c in "0123456789abcdefABCDEF" for c in target_sha):
            raise ValueError(f"Invalid SHA: {target_sha!r}")
        self._git("reset", "--hard", target_sha)
