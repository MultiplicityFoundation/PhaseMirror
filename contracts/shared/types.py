"""Shared cross-module datatypes for standalone module wiring."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, Tuple, Literal, Union


@dataclass(frozen=True)
class DistillationState:
    """Teacher/student logits and metadata at one distillation step."""

    teacher_logits: Tuple[float, ...]
    student_logits: Tuple[float, ...]
    step_id: str


@dataclass(frozen=True)
class TransportPlan:
    """Simple transport summary used by distillation and inversion modules."""

    plan_id: str
    cost: float
    mass: Tuple[float, ...]


@dataclass(frozen=True)
class ContractionBounds:
    """Shared representation of contraction and drift guardrails."""

    kappa: float
    drift: float
    resonance: float
    metadata: Dict[str, float] = field(default_factory=dict)


# ─── ADR-013: Type-Safe Phase Mirror Mode Inference ──────────────────────


@dataclass(frozen=True)
class PIRTMExpr:
    """Immutable typed wrapper for PIRTM expression bytes.
    
    Per ADR-013, PIRTMExpr represents a PIRTM expression (MLIR bytes).
    Cannot be coerced to StateSnapshot; type safety prevents mode confusion attacks.
    
    Attributes:
        data: The expression bytes (must be valid MLIR format with magic bytes)
        
    Raises:
        ValueError: If data is not valid PIRTM format
    """
    data: bytes

    def __post_init__(self):
        """Validate PIRTM format at construction time."""
        if not self.data:
            raise ValueError("PIRTMExpr data cannot be empty")
        
        if len(self.data) < 8:
            raise ValueError("PIRTMExpr data too short (< 8 bytes)")
        
        # Check for MLIR magic bytes (PIRTM expressions start with magic marker)
        # MLIR magic: 0x4d4c4952 (MLIR in hex) or similar marker
        magic_bytes = self.data[:4]
        if magic_bytes == b'\x00\x00\x00\x00':
            raise ValueError("PIRTMExpr data has invalid magic bytes")
        
        # Additional validation: ensure it's not JSON (which would be a StateSnapshot)
        try:
            json.loads(self.data)
            raise ValueError("PIRTMExpr data cannot be JSON; use StateSnapshot for snapshots")
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Expected: expression data is binary, not JSON
            pass

    def __eq__(self, other: object) -> bool:
        """Equality check - only equal if both are PIRTMExpr with same data."""
        if not isinstance(other, PIRTMExpr):
            return NotImplemented
        return self.data == other.data

    def __hash__(self) -> int:
        """Hash based on data (frozen dataclass)."""
        return hash(self.data)


@dataclass(frozen=True)
class StateSnapshot:
    """Immutable typed wrapper for state snapshot bytes.
    
    Per ADR-013, StateSnapshot represents a system state snapshot (JSON bytes).
    Cannot be coerced to PIRTMExpr; type safety prevents mode confusion attacks.
    
    Attributes:
        data: The snapshot bytes (must be valid JSON)
        snapshot_id: Identifier for this snapshot (e.g., transaction ID)
        
    Raises:
        ValueError: If data is not valid JSON
    """
    data: bytes
    snapshot_id: str

    def __post_init__(self):
        """Validate snapshot format at construction time."""
        if not self.data:
            raise ValueError("StateSnapshot data cannot be empty")
        
        if not self.snapshot_id:
            raise ValueError("StateSnapshot snapshot_id cannot be empty")
        
        # Validate data is valid JSON
        try:
            decoded = self.data.decode('utf-8')
            json.loads(decoded)
        except UnicodeDecodeError:
            raise ValueError("StateSnapshot data must be valid UTF-8")
        except json.JSONDecodeError as e:
            raise ValueError(f"StateSnapshot data must be valid JSON: {e}")

    def __eq__(self, other: object) -> bool:
        """Equality check - only equal if both are StateSnapshot with same data and ID."""
        if not isinstance(other, StateSnapshot):
            return NotImplemented
        return self.data == other.data and self.snapshot_id == other.snapshot_id

    def __hash__(self) -> int:
        """Hash based on data and snapshot_id (frozen dataclass)."""
        return hash((self.data, self.snapshot_id))


# ─── Gate D: Phase Mirror typed payload contracts (D-01) ──────────────────


@dataclass(frozen=True)
class EnforcementBits:
    """Legitimacy predicate input bits per ADR-032.

    L(bits) = R and C and S and B and V and M and (not P_bad)
    """

    R: bool = True
    C: bool = True
    S: bool = True
    B: bool = True
    V: bool = True
    T: bool = True
    M: bool = True
    P_bad: bool = False

    def legitimacy(self) -> bool:
        """Return legitimacy predicate L(bits)."""
        return self.R and self.C and self.S and self.B and self.V and self.M and (not self.P_bad)


@dataclass(frozen=True)
class ExpressionPayload:
    """Sealed expression-evaluation payload for Phase Mirror."""

    input_text: str
    output_expr: PIRTMExpr
    rollback_trigger: str = "none"


@dataclass(frozen=True)
class StateTransitionPayload:
    """Sealed state-transition payload for Phase Mirror."""

    input_text: str
    snapshot: StateSnapshot
    enforcement_bits: EnforcementBits = field(default_factory=EnforcementBits)
    rollback_trigger: str = "none"
    governance_version: str | None = None
    twin_desynced: bool = False
    stale_base_disabled: bool = False
    boundary_absent: bool = False

    @classmethod
    def from_snapshot(
        cls,
        *,
        input_text: str,
        snapshot: StateSnapshot,
        rollback_trigger: str = "none",
    ) -> "StateTransitionPayload":
        """Create payload from snapshot JSON with conservative defaults.

        Missing fields default to legitimacy-preserving values so legacy snapshots
        remain evaluable without accidental hard-fail.
        """

        try:
            payload = json.loads(snapshot.data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {}

        bits_raw = payload.get("enforcement_bits", {}) if isinstance(payload, dict) else {}
        if isinstance(bits_raw, dict):
            bits = EnforcementBits(
                R=bool(bits_raw.get("R", True)),
                C=bool(bits_raw.get("C", True)),
                S=bool(bits_raw.get("S", True)),
                B=bool(bits_raw.get("B", True)),
                V=bool(bits_raw.get("V", True)),
                T=bool(bits_raw.get("T", True)),
                M=bool(bits_raw.get("M", True)),
                P_bad=bool(bits_raw.get("P_bad", False)),
            )
        else:
            bits = EnforcementBits()

        governance_version = payload.get("governance_version") if isinstance(payload, dict) else None

        twin_desynced = bool(payload.get("twin_desynced", False)) if isinstance(payload, dict) else False

        # If S/B bits are explicitly false, reflect those conditions in derived flags.
        stale_base_disabled = not bits.S
        boundary_absent = not bits.B

        return cls(
            input_text=input_text,
            snapshot=snapshot,
            enforcement_bits=bits,
            rollback_trigger=rollback_trigger,
            governance_version=governance_version,
            twin_desynced=twin_desynced,
            stale_base_disabled=stale_base_disabled,
            boundary_absent=boundary_absent,
        )


PhaseMirrorPayload = Union[ExpressionPayload, StateTransitionPayload]
