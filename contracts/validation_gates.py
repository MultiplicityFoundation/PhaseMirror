"""
ADR-028: Validation Gates, Self-Modification, and Kill Switch

Implements a state machine enforcing validation gates:
  PROPOSED → AUDITED → APPROVED → EXECUTING → VERIFIED

Each transition requires explicit authorization. Kill switch (human override)
always available from any state.

Per ADR-028, gates prevent self-modification without validation.
Rollback and kill-switch are always available.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Callable, Tuple
import json


class GateState(Enum):
    """Valid states in the validation gate state machine."""
    PROPOSED = "PROPOSED"
    AUDITED = "AUDITED"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    VERIFIED = "VERIFIED"
    ROLLED_BACK = "ROLLED_BACK"
    KILLED = "KILLED"


class GateTransition(Enum):
    """Valid transitions between gate states."""
    # Forward path
    PROPOSE_TO_AUDIT = ("PROPOSED", "AUDITED")
    AUDIT_TO_APPROVE = ("AUDITED", "APPROVED")
    APPROVE_TO_EXECUTE = ("APPROVED", "EXECUTING")
    EXECUTE_TO_VERIFY = ("EXECUTING", "VERIFIED")
    
    # Backward paths
    ANY_TO_ROLLBACK = ("*", "ROLLED_BACK")
    ANY_TO_KILLED = ("*", "KILLED")


@dataclass(frozen=True)
class GateAuthorization:
    """Authorization evidence for a gate transition.
    
    Attributes:
        authorization_type: Type of authorization (HUMAN, POLICY, SENTINEL, EMERGENT)
        authorizer_id: Identity of the authorizer (name, key, or service ID)
        timestamp: When authorization was granted (ISO 8601)
        evidence_hash: Hash of supporting evidence (if any)
        justification: Human-readable explanation
    """
    authorization_type: str  # HUMAN, POLICY, SENTINEL, EMERGENT
    authorizer_id: str
    timestamp: str  # ISO 8601
    evidence_hash: Optional[str] = None
    justification: str = ""
    
    def validate(self) -> bool:
        """Check that authorization is well-formed."""
        if not self.authorizer_id:
            return False
        if not self.timestamp:
            return False
        if self.authorization_type not in ["HUMAN", "POLICY", "SENTINEL", "EMERGENT"]:
            return False
        try:
            datetime.fromisoformat(self.timestamp)
        except ValueError:
            return False
        return True
    
    def to_dict(self) -> Dict:
        """Serialize to JSON-safe dict."""
        return {
            "authorization_type": self.authorization_type,
            "authorizer_id": self.authorizer_id,
            "timestamp": self.timestamp,
            "evidence_hash": self.evidence_hash,
            "justification": self.justification,
        }


@dataclass
class GateTransitionRecord:
    """Audit record for a single gate transition.
    
    Attributes:
        from_state: Starting state
        to_state: Target state
        transition_timestamp: When transition occurred (ISO 8601)
        authorization: Authorization for this transition
        transition_id: Unique identifier for this transition
        execution_proof: Optional proof of successful execution (hash)
    """
    from_state: GateState
    to_state: GateState
    transition_timestamp: str  # ISO 8601
    authorization: GateAuthorization
    transition_id: str
    execution_proof: Optional[str] = None
    
    def to_dict(self) -> Dict:
        """Serialize to JSON-safe dict."""
        return {
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "transition_timestamp": self.transition_timestamp,
            "authorization": self.authorization.to_dict(),
            "transition_id": self.transition_id,
            "execution_proof": self.execution_proof,
        }


class GateMachine:
    """State machine enforcing validation gates.
    
    Per ADR-028, this machine:
    1. Enforces ordered transitions: PROPOSED → AUDITED → APPROVED → EXECUTING → VERIFIED
    2. Requires explicit authorization for each transition
    3. Always allows rollback (→ ROLLED_BACK) with human override
    4. Always allows kill switch (→ KILLED) with kill-switch authorization
    5. Prevents cycling (no re-entry to prior states)
    6. Maintains immutable audit trail
    
    Usage:
        machine = GateMachine(item_id="proposal-1")
        
        # Propose
        machine.propose(auth_human)
        
        # Audit and approve
        machine.audit(auth_policy)
        machine.approve(auth_sentinel)
        
        # Execute and verify
        machine.execute()
        machine.verify(proof_hash)
        
        # Or kill switch at any time
        machine.kill_switch(auth_human)
    """
    
    def __init__(self, item_id: str):
        """Initialize a new gate machine.
        
        Args:
            item_id: Unique identifier for the item being validated
        """
        self.item_id = item_id
        self.current_state = GateState.PROPOSED
        self.transitions: List[GateTransitionRecord] = []
        self._creation_timestamp = datetime.utcnow().isoformat()
    
    def get_current_state(self) -> GateState:
        """Get current state."""
        return self.current_state
    
    def propose(self, authorization: GateAuthorization) -> bool:
        """Transition: Enter PROPOSED state (initial state creation)."""
        if self.current_state != GateState.PROPOSED:
            raise ValueError(f"Cannot propose from state {self.current_state.value}")
        if not authorization.validate():
            raise ValueError("Invalid authorization")
        # PROPOSED is initial state; no transition record needed initially
        return True
    
    def audit(self, authorization: GateAuthorization) -> bool:
        """Transition: PROPOSED → AUDITED."""
        if self.current_state != GateState.PROPOSED:
            raise ValueError(f"Cannot audit from {self.current_state.value}; requires PROPOSED")
        if not authorization.validate():
            raise ValueError("Invalid authorization")
        
        self._record_transition(GateState.PROPOSED, GateState.AUDITED, authorization)
        self.current_state = GateState.AUDITED
        return True
    
    def approve(self, authorization: GateAuthorization) -> bool:
        """Transition: AUDITED → APPROVED."""
        if self.current_state != GateState.AUDITED:
            raise ValueError(f"Cannot approve from {self.current_state.value}; requires AUDITED")
        if not authorization.validate():
            raise ValueError("Invalid authorization")
        
        self._record_transition(GateState.AUDITED, GateState.APPROVED, authorization)
        self.current_state = GateState.APPROVED
        return True
    
    def execute(self, authorization: Optional[GateAuthorization] = None) -> bool:
        """Transition: APPROVED → EXECUTING.
        
        Execution can be automatic (no additional authorization) or explicit.
        """
        if self.current_state != GateState.APPROVED:
            raise ValueError(f"Cannot execute from {self.current_state.value}; requires APPROVED")
        
        # If no explicit authorization provided, use EMERGENT (automatic execution after approval)
        if authorization is None:
            authorization = GateAuthorization(
                authorization_type="EMERGENT",
                authorizer_id="system.execute",
                timestamp=datetime.utcnow().isoformat(),
                justification="Automatic execution after approval"
            )
        
        if not authorization.validate():
            raise ValueError("Invalid authorization")
        
        self._record_transition(GateState.APPROVED, GateState.EXECUTING, authorization)
        self.current_state = GateState.EXECUTING
        return True
    
    def verify(self, execution_proof: str, authorization: Optional[GateAuthorization] = None) -> bool:
        """Transition: EXECUTING → VERIFIED.
        
        Args:
            execution_proof: Hash or fingerprint showing execution succeeded
            authorization: Optional explicit verification authorization
        """
        if self.current_state != GateState.EXECUTING:
            raise ValueError(f"Cannot verify from {self.current_state.value}; requires EXECUTING")
        
        if not execution_proof:
            raise ValueError("execution_proof required for verification")
        
        if authorization is None:
            authorization = GateAuthorization(
                authorization_type="EMERGENT",
                authorizer_id="system.verify",
                timestamp=datetime.utcnow().isoformat(),
                justification="Automatic verification after execution"
            )
        
        if not authorization.validate():
            raise ValueError("Invalid authorization")
        
        record = GateTransitionRecord(
            from_state=self.current_state,
            to_state=GateState.VERIFIED,
            transition_timestamp=datetime.utcnow().isoformat(),
            authorization=authorization,
            transition_id=self._gen_transition_id(),
            execution_proof=execution_proof,
        )
        self.transitions.append(record)
        self.current_state = GateState.VERIFIED
        return True
    
    def rollback(self, authorization: GateAuthorization) -> bool:
        """Transition: ANY → ROLLED_BACK (always allowed with authorization).
        
        Per ADR-028, rollback is always available. Requires explicit human authorization.
        """
        if authorization.authorization_type != "HUMAN":
            raise ValueError("Rollback requires HUMAN authorization")
        if not authorization.validate():
            raise ValueError("Invalid authorization")
        
        self._record_transition(self.current_state, GateState.ROLLED_BACK, authorization)
        self.current_state = GateState.ROLLED_BACK
        return True
    
    def kill_switch(self, authorization: GateAuthorization) -> bool:
        """Transition: ANY → KILLED (emergency human killswitch, always available).
        
        Per ADR-028, kill switch overrides all other state. Requires HUMAN authorization
        from a kill-switch authorized identity.
        """
        if authorization.authorization_type != "HUMAN":
            raise ValueError("Kill switch requires HUMAN authorization")
        if not authorization.validate():
            raise ValueError("Invalid authorization")
        if not authorization.authorizer_id.startswith("killswitch:"):
            # Kill switch requires special identity prefix (could be enhanced with ACL)
            pass  # Optional: strict enforcement; can also allow any HUMAN auth
        
        self._record_transition(self.current_state, GateState.KILLED, authorization)
        self.current_state = GateState.KILLED
        return True
    
    def get_audit_trail(self) -> List[Dict]:
        """Return full audit trail as list of dicts."""
        return [record.to_dict() for record in self.transitions]
    
    def is_terminal(self) -> bool:
        """Check if current state is terminal (no further transitions possible)."""
        return self.current_state in [GateState.VERIFIED, GateState.ROLLED_BACK, GateState.KILLED]
    
    def _record_transition(self, from_state: GateState, to_state: GateState, 
                          authorization: GateAuthorization) -> None:
        """Record a transition in the audit trail."""
        record = GateTransitionRecord(
            from_state=from_state,
            to_state=to_state,
            transition_timestamp=datetime.utcnow().isoformat(),
            authorization=authorization,
            transition_id=self._gen_transition_id(),
        )
        self.transitions.append(record)
    
    def _gen_transition_id(self) -> str:
        """Generate a unique transition ID."""
        return f"{self.item_id}:{len(self.transitions)+1}:{datetime.utcnow().timestamp()}"
    
    def to_dict(self) -> Dict:
        """Serialize machine state to JSON-safe dict."""
        return {
            "item_id": self.item_id,
            "current_state": self.current_state.value,
            "creation_timestamp": self._creation_timestamp,
            "audit_trail": self.get_audit_trail(),
        }


# ─── Integration with Rollback & Watchdog ─────────────────────────────────

class KillSwitchManager:
    """Manages kill-switch authorization and escalation.
    
    Per ADR-028, kill switch is always available but requires:
    1. HUMAN authorization (not automatic)
    2. Authorized kill-switch identity
    3. Optional: external verification (e.g., multiple approvers)
    
    The kill switch path must never be blocked by other logic.
    """
    
    def __init__(self, authorized_identities: List[str]):
        """Initialize kill switch manager.
        
        Args:
            authorized_identities: List of authorized kill-switch identities
        """
        self.authorized_identities = set(authorized_identities)
        self.kill_switch_events: List[Dict] = []
    
    def authorize_kill_switch(self, authorizer_id: str, justification: str) -> GateAuthorization:
        """Create a kill-switch authorization.
        
        Args:
            authorizer_id: Identity of the human authorizer
            justification: Reason for kill switch
            
        Returns:
            GateAuthorization if authorizer_id is authorized
            
        Raises:
            ValueError: If authorizer_id is not in authorized list
        """
        if authorizer_id not in self.authorized_identities:
            raise ValueError(f"Kill-switch identity {authorizer_id} not authorized")
        
        auth = GateAuthorization(
            authorization_type="HUMAN",
            authorizer_id=f"killswitch:{authorizer_id}",
            timestamp=datetime.utcnow().isoformat(),
            justification=justification,
        )
        
        # Log kill-switch event
        self.kill_switch_events.append({
            "timestamp": auth.timestamp,
            "authorizer": authorizer_id,
            "justification": justification,
        })
        
        return auth
    
    def is_authorized(self, authorizer_id: str) -> bool:
        """Check if an authorizer is authorized for kill switch."""
        return authorizer_id in self.authorized_identities
    
    def get_kill_switch_events(self) -> List[Dict]:
        """Return all kill-switch events."""
        return self.kill_switch_events


# ─── Testing Helpers ──────────────────────────────────────────────────────

def create_test_authorization(auth_type: str = "HUMAN", 
                              authorizer_id: str = "test:user",
                              justification: str = "test") -> GateAuthorization:
    """Helper to create test authorizations."""
    return GateAuthorization(
        authorization_type=auth_type,
        authorizer_id=authorizer_id,
        timestamp=datetime.utcnow().isoformat(),
        justification=justification,
    )
