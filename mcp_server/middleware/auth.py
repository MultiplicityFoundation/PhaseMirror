"""MCP Server Authentication and tool-integrity middleware.

Per ADR-012 and ADR-017, this file is part of the governance trust boundary.
Governance-critical tools must pass a ledger-backed integrity check before dispatch.

F-02 (Gate F): OAuth 2.1 token validation, scope enforcement, PKCE verification,
and JTI replay detection are layered on top of the existing integrity verifier.
"""

from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, FrozenSet, Optional, Set, Tuple

from contracts.shared.constants import GOVERNANCE_MERKLE_ROOT_TX_ID
from contracts.shared.merkle_root import sha256_file
from governance.ledger import AuditLedger, LedgerStore
from mcp_server._yaml import load_yaml_file
from rollback.kill_switch import KillSwitch


# ─── F-02: OAuth 2.1 Types ────────────────────────────────────────────────────


class AuthorizationError(RuntimeError):
    """Raised when an OAuth token is invalid, expired, or lacks required scopes."""


@dataclass(frozen=True)
class OAuthToken:
    """OAuth 2.1 bearer token with scope-based authorization.

    Fields match the RFC 9068 JWT access token claim set.
    """

    subject: str
    scopes: FrozenSet[str]
    issued_at: int   # Unix timestamp (iat)
    expires_at: int  # Unix timestamp (exp)
    jti: str         # JWT ID — used for replay prevention

    def is_expired(self) -> bool:
        """Return True if the token's expiry time has passed."""
        return time.time() > self.expires_at

    def has_scope(self, required: FrozenSet[str]) -> bool:
        """Return True if the token's scopes are a superset of *required*."""
        return required.issubset(self.scopes)


# Scope-to-tool mapping.  Every tool registered in tool_registry.yaml must have
# exactly one entry here.  pmd:admin is a superset sentinel that bypasses
# individual checks; all other entries require a specific non-empty scope set.
REQUIRED_SCOPES_BY_TOOL: Dict[str, FrozenSet[str]] = {
    # Read-only tools
    "health_check":            frozenset({"pmd:read"}),
    "manifest_status":         frozenset({"pmd:read"}),
    "ledger_query":            frozenset({"pmd:read"}),
    "normative_record_query":  frozenset({"pmd:read"}),
    "rollback_status":         frozenset({"pmd:read"}),
    "checkpoint_inventory":    frozenset({"pmd:read"}),
    # Epsilon adjustment tools (watchdog-initiated, require write)
    "epsilon_adjust":          frozenset({"pmd:write"}),
    "daemon_epsilon_adjust":   frozenset({"pmd:write"}),
    # Watchdog / heartbeat
    "daemon_heartbeat":        frozenset({"pmd:read"}),
    # Phase Mirror evaluation
    "phase_mirror":            frozenset({"pmd:phase_mirror"}),
    # Agent dispatch
    "agent_dispatch":          frozenset({"pmd:dispatch"}),
    # Write / mutation tools
    "checkpoint_write":        frozenset({"pmd:write"}),
    "checkpoint_prune":        frozenset({"pmd:write"}),
    "rollback_execute":        frozenset({"pmd:write"}),
    # ADR-037 Wave 1 tools
    "wave1_health_check":      frozenset({"pmd:read"}),
    "wave1_manifest_status":   frozenset({"pmd:read"}),
    "wave1_request_cycle":     frozenset({"pmd:write"}),
    "wave1_request_rollback":  frozenset({"pmd:write"}),
}

# pmd:admin is a catch-all scope; a token bearing it may call any tool.
_ADMIN_SCOPE = frozenset({"pmd:admin"})


def require_auth(tool_name: str, token: OAuthToken) -> None:
    """Enforce scope-based authorization before tool dispatch.

    Raises:
        AuthorizationError: if the token is expired, the tool is unknown,
            or the token lacks the required scopes.
    """
    if token.is_expired():
        raise AuthorizationError(
            f"OAuth token for subject '{token.subject}' expired at {token.expires_at}"
        )

    # Admin scope bypasses per-tool scope checks.
    if token.has_scope(_ADMIN_SCOPE):
        return

    required = REQUIRED_SCOPES_BY_TOOL.get(tool_name)
    if required is None:
        raise AuthorizationError(
            f"Tool '{tool_name}' is not registered in REQUIRED_SCOPES_BY_TOOL; "
            f"cannot authorize unknown tool."
        )

    if not token.has_scope(required):
        raise AuthorizationError(
            f"Tool '{tool_name}' requires scopes {set(required)}, "
            f"but token for '{token.subject}' only has {set(token.scopes)}."
        )


def verify_pkce_challenge(verifier: str, challenge: str) -> bool:
    """Verify an OAuth 2.1 PKCE code_challenge against code_verifier.

    Per RFC 7636 §4.6, the only permitted method is S256:
        code_challenge = BASE64URL(SHA256(ASCII(code_verifier)))

    Args:
        verifier: The raw code_verifier string from the client.
        challenge: The code_challenge that was stored at authorization time.

    Returns:
        True if the verifier hashes to the challenge; False otherwise.
    """
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return computed == challenge


class ReplayDetector:
    """Detect OAuth token replay attacks via JTI (JWT ID) tracking.

    A JTI that has been seen within the token's validity window is a replay.
    Expired JTIs are pruned lazily to bound memory use.
    """

    def __init__(self) -> None:
        # Maps jti -> expiry (Unix timestamp)
        self._seen: Dict[str, int] = {}

    def is_replay(self, jti: str, expires_at: int) -> bool:
        """Return True if *jti* has been seen before and is still within its window.

        Side-effect: records *jti* as seen and prunes expired entries.
        """
        self._prune_expired()
        if jti in self._seen:
            return True
        self._seen[jti] = expires_at
        return False

    def _prune_expired(self) -> None:
        now = time.time()
        expired = [jti for jti, exp in self._seen.items() if exp <= now]
        for jti in expired:
            del self._seen[jti]


# ─── Legacy AuthToken (kept for backward compatibility) ───────────────────────


@dataclass
class AuthToken:
    """Authentication token for MCP operations (legacy stub)."""
    token_id: str
    origin: str
    scopes: list[str]


def authenticate_mcp_request(token: Optional[str]) -> Optional[AuthToken]:
    """Authenticate an MCP request.
    
    Args:
        token: Bearer token from request header
        
    Returns:
        AuthToken if valid, None otherwise
    """
    # Placeholder for authentication logic
    # Full implementation in production system
    if token is None:
        return None
    
    return AuthToken(
        token_id=token,
        origin="mcp-client",
        scopes=["default"],
    )


def authorize_operation(auth: AuthToken, operation: str) -> bool:
    """Check if authenticated user can perform operation.
    
    Args:
        auth: Authenticated token
        operation: Operation identifier
        
    Returns:
        True if authorized, False otherwise
    """
    # Placeholder for authorization logic
    return "default" in auth.scopes


@dataclass(frozen=True)
class ToolIntegrityReport:
    """Outcome of a single tool integrity verification."""

    tool_name: str
    status: str
    verify_integrity: bool
    tool_path: str
    expected_hash: str | None = None
    actual_hash: str | None = None
    reason: str = ""
    verified_at: str = ""


class ToolIntegrityViolation(RuntimeError):
    """Raised when a governance-critical tool fails integrity verification."""

    def __init__(self, report: ToolIntegrityReport, kill_switch_result: dict[str, object]):
        super().__init__(f"Tool integrity verification failed for '{report.tool_name}': {report.reason}")
        self.report = report
        self.kill_switch_result = kill_switch_result


class ToolIntegrityVerifier:
    """Verify governance-critical tool hashes against the governance ledger."""

    def __init__(
        self,
        *,
        ledger: LedgerStore | None = None,
        audit_ledger: AuditLedger | None = None,
        invariants_path: Path | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        self.workspace_root = (workspace_root or Path(__file__).resolve().parents[2]).resolve()
        self.invariants_path = invariants_path or (self.workspace_root / "contracts" / "system_invariants.yaml")
        self.ledger = ledger or LedgerStore(storage_path=self.workspace_root / "governance" / "ledger.json")
        self.audit_ledger = audit_ledger or AuditLedger()
        self.kill_switch = KillSwitch()
        self._governance_critical_tools = self._load_governance_critical_tools()

    def _load_governance_critical_tools(self) -> set[str]:
        data = load_yaml_file(self.invariants_path)
        raw_paths = data.get("governance_critical_tools", []) if isinstance(data, dict) else []
        return {self._normalize_path(path) for path in raw_paths}

    def _normalize_path(self, path_value: str | Path) -> str:
        path = Path(path_value)
        if path.is_absolute():
            try:
                return path.resolve().relative_to(self.workspace_root).as_posix()
            except ValueError:
                return path.resolve().as_posix()
        return path.as_posix()

    def _resolve_tool_path(self, tool_file: str) -> Path:
        path = Path(tool_file)
        if path.is_absolute():
            return path
        return self.workspace_root / path

    def _load_expected_hashes(self) -> dict[str, str]:
        root_entry = self.ledger.get_entry(GOVERNANCE_MERKLE_ROOT_TX_ID)
        if root_entry is None:
            latest_root = self.ledger.get_latest_root_commit()
            if latest_root is None:
                raise RuntimeError(
                    f"Governance root entry not found in ledger (tx_id={GOVERNANCE_MERKLE_ROOT_TX_ID})"
                )
            _, root_entry = latest_root
        return {
            self._normalize_path(record.path): record.hash
            for record in root_entry.immutable_files
            if isinstance(record.hash, str) and not record.hash.startswith("ERROR:")
        }

    def verify(
        self,
        *,
        tool_name: str,
        tool_file: str,
        verify_integrity: bool,
    ) -> ToolIntegrityReport:
        normalized_path = self._normalize_path(tool_file)
        resolved_path = self._resolve_tool_path(tool_file)
        verified_at = datetime.now(timezone.utc).isoformat()

        if not verify_integrity:
            return ToolIntegrityReport(
                tool_name=tool_name,
                status="skipped",
                verify_integrity=False,
                tool_path=normalized_path,
                reason="registry_bypass",
                verified_at=verified_at,
            )

        if normalized_path not in self._governance_critical_tools:
            return self._raise_violation(
                ToolIntegrityReport(
                    tool_name=tool_name,
                    status="failed",
                    verify_integrity=True,
                    tool_path=normalized_path,
                    reason="tool_not_governance_critical",
                    verified_at=verified_at,
                ),
                trigger=f"tool_integrity_scope_error:{tool_name}",
            )

        if not resolved_path.exists():
            return self._raise_violation(
                ToolIntegrityReport(
                    tool_name=tool_name,
                    status="failed",
                    verify_integrity=True,
                    tool_path=normalized_path,
                    reason="tool_file_not_found",
                    verified_at=verified_at,
                ),
                trigger=f"tool_file_not_found:{tool_name}",
            )

        try:
            expected_hashes = self._load_expected_hashes()
        except Exception as exc:
            return self._raise_violation(
                ToolIntegrityReport(
                    tool_name=tool_name,
                    status="failed",
                    verify_integrity=True,
                    tool_path=normalized_path,
                    reason=str(exc),
                    verified_at=verified_at,
                ),
                trigger=f"tool_hash_fetch_failed:{tool_name}",
            )

        expected_hash = expected_hashes.get(normalized_path)
        if expected_hash is None:
            return self._raise_violation(
                ToolIntegrityReport(
                    tool_name=tool_name,
                    status="failed",
                    verify_integrity=True,
                    tool_path=normalized_path,
                    reason="expected_hash_missing",
                    verified_at=verified_at,
                ),
                trigger=f"tool_hash_unknown:{tool_name}",
            )

        try:
            actual_hash = sha256_file(resolved_path)
        except Exception as exc:
            return self._raise_violation(
                ToolIntegrityReport(
                    tool_name=tool_name,
                    status="failed",
                    verify_integrity=True,
                    tool_path=normalized_path,
                    expected_hash=expected_hash,
                    reason=f"hash_compute_error: {exc}",
                    verified_at=verified_at,
                ),
                trigger=f"tool_hash_compute_error:{tool_name}",
            )

        if actual_hash != expected_hash:
            return self._raise_violation(
                ToolIntegrityReport(
                    tool_name=tool_name,
                    status="failed",
                    verify_integrity=True,
                    tool_path=normalized_path,
                    expected_hash=expected_hash,
                    actual_hash=actual_hash,
                    reason="hash_mismatch",
                    verified_at=verified_at,
                ),
                trigger=f"tool_integrity_failure:{tool_name}",
            )

        report = ToolIntegrityReport(
            tool_name=tool_name,
            status="verified",
            verify_integrity=True,
            tool_path=normalized_path,
            expected_hash=expected_hash,
            actual_hash=actual_hash,
            verified_at=verified_at,
        )
        self.audit_ledger.append(
            {
                "type": "TOOL_INTEGRITY_VERIFIED",
                "tool_name": tool_name,
                "tool_path": normalized_path,
                "tool_hash": actual_hash,
                "verified_at": verified_at,
            },
            timestamp=verified_at,
        )
        return report

    def _raise_violation(self, report: ToolIntegrityReport, *, trigger: str) -> ToolIntegrityReport:
        kill_switch_result = self.kill_switch.engage(trigger=trigger, reason=report.reason)
        self.audit_ledger.append(
            {
                "type": "TOOL_INTEGRITY_FAILED",
                "tool_name": report.tool_name,
                "tool_path": report.tool_path,
                "expected_hash": report.expected_hash,
                "actual_hash": report.actual_hash,
                "reason": report.reason,
                "kill_switch": kill_switch_result,
                "verified_at": report.verified_at,
            },
            timestamp=report.verified_at or datetime.now(timezone.utc).isoformat(),
        )
        raise ToolIntegrityViolation(report, kill_switch_result)


def verify_tool_before_dispatch(
    *,
    verifier: ToolIntegrityVerifier,
    tool_name: str,
    tool_file: str,
    verify_integrity: bool,
) -> ToolIntegrityReport:
    """Dispatch-time integrity gate for governance-critical tools."""
    return verifier.verify(
        tool_name=tool_name,
        tool_file=tool_file,
        verify_integrity=verify_integrity,
    )
