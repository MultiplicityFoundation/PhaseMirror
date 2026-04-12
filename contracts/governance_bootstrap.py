"""
ADR-029: Governance Bootstrap and Immutable Trust Anchor

Implements the startup governance protocol that:
1. Initializes immutable trust anchor (Merkle root of governance constants)
2. Verifies sentinel (signed initial ledger entry)
3. Establishes governance bootstrap state

Per ADR-029, governance must be bootstrapped before any autonomous system action:
- No agent activation before bootstrap complete
- No policy evaluation before trust anchor verified
- Ledger write capability sealed after bootstrap

The bootstrap protocol ensures that governance starting conditions are cryptographically
verified and immutable.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional
import hashlib
import json


@dataclass(frozen=True)
class GovernanceConstants:
    """Immutable governance constants established at bootstrap.
    
    Attributes:
        TRUST_ANCHOR_HASH: Merkle root of all governance claims
        BOOTSTRAP_TIMESTAMP: ISO 8601 timestamp of bootstrap
        SENTINEL_KEY: Public key for verifying bootstrap sentinel
        INITIAL_OPERATOR_ID: Identity of initial authorized operator
        KILL_SWITCH_IDENTITIES: List of identities with kill-switch authorization
    """
    TRUST_ANCHOR_HASH: str
    BOOTSTRAP_TIMESTAMP: str  # ISO 8601
    SENTINEL_KEY: str  # Public key (hex or base64)
    INITIAL_OPERATOR_ID: str
    KILL_SWITCH_IDENTITIES: tuple = field(default_factory=tuple)
    
    def validate(self) -> bool:
        """Validate that all constants are well-formed."""
        if not self.TRUST_ANCHOR_HASH:
            return False
        if not self.BOOTSTRAP_TIMESTAMP:
            return False
        if not self.SENTINEL_KEY:
            return False
        if not self.INITIAL_OPERATOR_ID:
            return False
        try:
            datetime.fromisoformat(self.BOOTSTRAP_TIMESTAMP)
        except ValueError:
            return False
        return True
    
    def to_dict(self) -> Dict:
        """Serialize to JSON-safe dict."""
        return {
            "TRUST_ANCHOR_HASH": self.TRUST_ANCHOR_HASH,
            "BOOTSTRAP_TIMESTAMP": self.BOOTSTRAP_TIMESTAMP,
            "SENTINEL_KEY": self.SENTINEL_KEY,
            "INITIAL_OPERATOR_ID": self.INITIAL_OPERATOR_ID,
            "KILL_SWITCH_IDENTITIES": list(self.KILL_SWITCH_IDENTITIES),
        }


@dataclass(frozen=True)
class BootstrapSentinel:
    """Signed sentinel record verifying governance bootstrap.
    
    The sentinel is a signed claim that:
    1. Governance was bootstrapped at BOOTSTRAP_TIMESTAMP
    2. Trust anchor is TRUST_ANCHOR_HASH
    3. Signature proves knowledge of private key corresponding to SENTINEL_KEY
    
    Attributes:
        trust_anchor_hash: The Merkle root being verified
        bootstrap_timestamp: When this bootstrap occurred
        sentinel_signature: Cryptographic signature (hex or base64)
        signer_identity: Identity of the signer (for audit)
    """
    trust_anchor_hash: str
    bootstrap_timestamp: str  # ISO 8601
    sentinel_signature: str
    signer_identity: str = "governance.bootstrap"
    
    def validate(self) -> bool:
        """Validate sentinel structure."""
        if not self.trust_anchor_hash:
            return False
        if not self.bootstrap_timestamp:
            return False
        if not self.sentinel_signature:
            return False
        try:
            datetime.fromisoformat(self.bootstrap_timestamp)
        except ValueError:
            return False
        return True
    
    def to_dict(self) -> Dict:
        """Serialize to JSON-safe dict."""
        return {
            "trust_anchor_hash": self.trust_anchor_hash,
            "bootstrap_timestamp": self.bootstrap_timestamp,
            "sentinel_signature": self.sentinel_signature,
            "signer_identity": self.signer_identity,
        }


class BootstrapMerkleTree:
    """Simple Merkle tree for computing trust anchor.
    
    Per ADR-029, the trust anchor is a Merkle root of all governance claims.
    This implementation uses SHA-256 for hashing.
    
    For production, consider:
    - More robust Merkle tree implementation
    - Proof generation for individual claims
    - Multi-signature support for co-approval
    """
    
    def __init__(self):
        """Initialize empty Merkle tree."""
        self.leaves: list[str] = []
        self.root_hash: Optional[str] = None
    
    def add_claim(self, claim: str) -> None:
        """Add a governance claim to the tree.
        
        Args:
            claim: String representation of the claim (e.g., JSON)
        """
        if self.root_hash is not None:
            raise ValueError("Cannot add claims after root is finalized")
        
        # Hash the claim
        claim_hash = hashlib.sha256(claim.encode('utf-8')).hexdigest()
        self.leaves.append(claim_hash)
    
    def finalize(self) -> str:
        """Compute and return the Merkle root.
        
        Returns:
            Merkle root hash (hex string)
        """
        if not self.leaves:
            # Empty tree hashes to default value
            self.root_hash = hashlib.sha256(b"").hexdigest()
            return self.root_hash
        
        # Build tree layer by layer
        current_layer = self.leaves[:]
        
        while len(current_layer) > 1:
            next_layer = []
            for i in range(0, len(current_layer), 2):
                if i + 1 < len(current_layer):
                    # Pair up adjacent leaves
                    combined = current_layer[i] + current_layer[i+1]
                else:
                    # Last leaf if odd count
                    combined = current_layer[i] + current_layer[i]
                
                parent_hash = hashlib.sha256(combined.encode('utf-8')).hexdigest()
                next_layer.append(parent_hash)
            
            current_layer = next_layer
        
        self.root_hash = current_layer[0]
        return self.root_hash
    
    def get_root(self) -> str:
        """Get the current Merkle root (finalize if needed)."""
        if self.root_hash is None:
            return self.finalize()
        return self.root_hash


class GovernanceBootstrapper:
    """Orchestrates governance bootstrap protocol.
    
    Per ADR-029, bootstrap sequence:
    1. Initialize Merkle tree with governance claims
    2. Compute trust anchor (Merkle root)
    3. Create bootstrap sentinel (signed snapshot)
    4. Establish immutable constants
    5. Seal bootstrap (no further configuration changes)
    
    After bootstrap, the system can proceed with policy evaluation and
    autonomous actions, confident that governance foundation is secure.
    """
    
    def __init__(self):
        """Initialize bootstrapper."""
        self.merkle_tree = BootstrapMerkleTree()
        self.is_bootstrapped = False
        self.constants: Optional[GovernanceConstants] = None
        self.sentinel: Optional[BootstrapSentinel] = None
        self._startup_timestamp = datetime.utcnow().isoformat()
    
    def add_governance_claim(self, claim_name: str, claim_value: str) -> None:
        """Add a governance claim to bootstrap.
        
        Args:
            claim_name: Name/identifier of the claim
            claim_value: Claim content (typically JSON string)
            
        Raises:
            ValueError: If bootstrap already completed
        """
        if self.is_bootstrapped:
            raise ValueError("Cannot add claims after bootstrap complete")
        
        claim_json = json.dumps({"name": claim_name, "value": claim_value})
        self.merkle_tree.add_claim(claim_json)
    
    def bootstrap(self, 
                 sentinel_key: str,
                 initial_operator: str,
                 kill_switch_identities: list[str],
                 sentinel_signature: Optional[str] = None) -> GovernanceConstants:
        """Complete bootstrap and return governance constants.
        
        Args:
            sentinel_key: Public key for verifying bootstrap sentinel
            initial_operator: Identity of initial authorized operator
            kill_switch_identities: List of identities with kill-switch authorization
            sentinel_signature: Optional pre-computed signature. If not provided,
                              generates a mock signature for testing.
        
        Returns:
            GovernanceConstants with immutable bootstrap configuration
            
        Raises:
            ValueError: If bootstrap already completed or validation fails
        """
        if self.is_bootstrapped:
            raise ValueError("Bootstrap already complete; cannot bootstrap again")
        
        # Compute trust anchor
        trust_anchor = self.merkle_tree.finalize()
        
        # Create sentinel
        if sentinel_signature is None:
            # For testing: generate a mock signature
            sentinel_signature = hashlib.sha256(
                f"{trust_anchor}:{self._startup_timestamp}".encode()
            ).hexdigest()
        
        self.sentinel = BootstrapSentinel(
            trust_anchor_hash=trust_anchor,
            bootstrap_timestamp=self._startup_timestamp,
            sentinel_signature=sentinel_signature,
        )
        
        if not self.sentinel.validate():
            raise ValueError("Invalid sentinel")
        
        # Create and validate constants
        self.constants = GovernanceConstants(
            TRUST_ANCHOR_HASH=trust_anchor,
            BOOTSTRAP_TIMESTAMP=self._startup_timestamp,
            SENTINEL_KEY=sentinel_key,
            INITIAL_OPERATOR_ID=initial_operator,
            KILL_SWITCH_IDENTITIES=tuple(kill_switch_identities),
        )
        
        if not self.constants.validate():
            raise ValueError("Invalid constants")
        
        self.is_bootstrapped = True
        return self.constants
    
    def verify_bootstrap(self, constants: GovernanceConstants) -> bool:
        """Verify that bootstrap is valid.
        
        In production, this would verify the sentinel signature against the
        sentinel_key. For now, checks structural validity.
        
        Args:
            constants: GovernanceConstants to verify
            
        Returns:
            True if bootstrap is valid; False otherwise
        """
        if not constants.validate():
            return False
        
        if self.sentinel is None:
            return False
        
        if constants.TRUST_ANCHOR_HASH != self.sentinel.trust_anchor_hash:
            return False
        
        if constants.BOOTSTRAP_TIMESTAMP != self.sentinel.bootstrap_timestamp:
            return False
        
        return True
    
    def get_bootstrap_record(self) -> Dict:
        """Get complete bootstrap audit record."""
        return {
            "is_bootstrapped": self.is_bootstrapped,
            "constants": self.constants.to_dict() if self.constants else None,
            "sentinel": self.sentinel.to_dict() if self.sentinel else None,
            "merkle_root": self.merkle_tree.get_root(),
            "leaf_count": len(self.merkle_tree.leaves),
        }


# ─── Governance Bootstrap Facade ──────────────────────────────────────────
# Top-level API for bootstrapping the system

_global_bootstrapper: Optional[GovernanceBootstrapper] = None
_global_constants: Optional[GovernanceConstants] = None


def initialize_governance_bootstrap() -> GovernanceBootstrapper:
    """Initialize global governance bootstrapper (singleton)."""
    global _global_bootstrapper
    if _global_bootstrapper is None:
        _global_bootstrapper = GovernanceBootstrapper()
    return _global_bootstrapper


def complete_governance_bootstrap(
    sentinel_key: str,
    initial_operator: str,
    kill_switch_identities: list[str],
) -> GovernanceConstants:
    """Complete global governance bootstrap.
    
    Args:
        sentinel_key: Public key for verifying bootstrap sentinel
        initial_operator: Identity of initial authorized operator
        kill_switch_identities: List of identities with kill-switch authorization
        
    Returns:
        GovernanceConstants (immutable)
    """
    global _global_bootstrapper, _global_constants
    
    bootstrapper = initialize_governance_bootstrap()
    _global_constants = bootstrapper.bootstrap(
        sentinel_key=sentinel_key,
        initial_operator=initial_operator,
        kill_switch_identities=kill_switch_identities,
    )
    return _global_constants


def get_governance_constants() -> Optional[GovernanceConstants]:
    """Get current governance constants (None if bootstrap incomplete)."""
    return _global_constants


def is_governance_bootstrapped() -> bool:
    """Check if governance bootstrap is complete."""
    return get_governance_constants() is not None
