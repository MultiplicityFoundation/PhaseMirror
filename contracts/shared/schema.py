"""Shared domain schema for cross-module communication (D-040.1).

Canonical types for all data flowing between domain modules.
Every serialised message embeds ``SCHEMA_VERSION``.

Usage:
    from shared.schema import (
        RecurrenceState, Witness, CertificationResult,
        ACFLContext, CRMFContext, CCREContext, DHTContext,
        WKDContext, HCALCContext, PWCFLContext,
        AdapterEnvelope,
    )
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Literal, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Schema version — embedded in every AdapterEnvelope
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Core domain types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecurrenceState:
    """Prime-indexed state produced by the PIRTM recurrence engine."""

    state_index: int
    timestamp: float
    witness_path: Tuple[int, ...]
    trace_id: str
    state_vector: Tuple[float, ...] = ()
    q_t: float = 0.0
    margin: float = 0.0
    epsilon: float = 0.05

    def __post_init__(self) -> None:
        if self.epsilon <= 0:
            raise ValueError("epsilon must be positive")
        if self.state_index < 0:
            raise ValueError("state_index must be non-negative")


@dataclass(frozen=True)
class CertificationResult:
    """ACE certification output for a single state."""

    state_id: str
    certified: bool
    policy_applied: Tuple[str, ...] = ()
    confidence_score: float = 1.0
    spectral_radius: float = 0.0
    contraction_margin: float = 0.0
    audit_record: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence_score <= 1.0):
            raise ValueError("confidence_score must be in [0.0, 1.0]")


@dataclass(frozen=True)
class Witness:
    """Complete execution witness aggregating recurrence + certification."""

    witness_id: str
    recurrence_states: Tuple[RecurrenceState, ...]
    certification_results: Tuple[CertificationResult, ...]
    domain_context: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.recurrence_states:
            raise ValueError("Witness must contain at least one RecurrenceState")


# ---------------------------------------------------------------------------
# Per-domain context extensions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ACFLContext:
    """Augmented Compensatory Fuzzy Logic context."""

    control_flow_depth: int
    value_constraints: Tuple[float, ...] = ()
    path_conditions: Tuple[str, ...] = ()
    operator_type: str = "conjunction"

    def __post_init__(self) -> None:
        if self.control_flow_depth < 0:
            raise ValueError("control_flow_depth must be non-negative")


@dataclass(frozen=True)
class CRMFContext:
    """Cognitive Resonance Model Field context."""

    resonance_frequency: float
    multiplicity_level: int
    field_signature: str = ""

    def __post_init__(self) -> None:
        if self.resonance_frequency < 0:
            raise ValueError("resonance_frequency must be non-negative")
        if self.multiplicity_level < 1:
            raise ValueError("multiplicity_level must be >= 1")


@dataclass(frozen=True)
class CCREContext:
    """Contraction-based Cognitive Resonance Engine context."""

    contraction_factor: float
    recurrence_window: int
    convergence_status: Literal["converging", "diverging", "stable", "unknown"] = "unknown"

    def __post_init__(self) -> None:
        if self.recurrence_window < 1:
            raise ValueError("recurrence_window must be >= 1")


@dataclass(frozen=True)
class DHTContext:
    """Distributed Hash Trajectory context."""

    hash_chain_depth: int
    distribution_entropy: float
    peer_count: int = 0

    def __post_init__(self) -> None:
        if self.hash_chain_depth < 0:
            raise ValueError("hash_chain_depth must be non-negative")
        if self.distribution_entropy < 0:
            raise ValueError("distribution_entropy must be non-negative")


@dataclass(frozen=True)
class WKDContext:
    """Witness-Knowledge Distillation context."""

    knowledge_graph_nodes: int
    edge_density: float
    query_depth: int = 1

    def __post_init__(self) -> None:
        if self.knowledge_graph_nodes < 0:
            raise ValueError("knowledge_graph_nodes must be non-negative")
        if not (0.0 <= self.edge_density <= 1.0):
            raise ValueError("edge_density must be in [0.0, 1.0]")


@dataclass(frozen=True)
class HCALCContext:
    """Hypercomputation Analytic Lab Calculus context."""

    hypercompute_depth: int
    field_order: int
    stratification_level: int = 1

    def __post_init__(self) -> None:
        if self.hypercompute_depth < 0:
            raise ValueError("hypercompute_depth must be non-negative")
        if self.field_order < 1:
            raise ValueError("field_order must be >= 1")


@dataclass(frozen=True)
class PWCFLContext:
    """Parameterised Weighted Compensatory Fuzzy Logic context."""

    weight_vector: Tuple[float, ...] = ()
    constraint_satisfaction: float = 1.0
    flow_capacity: float = 0.0

    def __post_init__(self) -> None:
        if not (0.0 <= self.constraint_satisfaction <= 1.0):
            raise ValueError("constraint_satisfaction must be in [0.0, 1.0]")


# ---------------------------------------------------------------------------
# Adapter envelope — wraps every cross-module message
# ---------------------------------------------------------------------------

DOMAIN_CONTEXT_TYPES = {
    "acfl": ACFLContext,
    "crmf": CRMFContext,
    "ccre": CCREContext,
    "dht": DHTContext,
    "wkd": WKDContext,
    "hcalc": HCALCContext,
    "pwcfl": PWCFLContext,
}


@dataclass(frozen=True)
class AdapterEnvelope:
    """Cross-module message envelope with schema version and validation."""

    source_module: str
    target_module: str
    payload: Dict[str, Any]
    schema_version: str = SCHEMA_VERSION
    envelope_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if self.source_module == self.target_module:
            raise ValueError("source_module and target_module must differ")
        major_cur = int(self.schema_version.split(".")[0])
        major_expected = int(SCHEMA_VERSION.split(".")[0])
        if major_cur != major_expected:
            raise ValueError(
                f"Schema version mismatch: envelope has {self.schema_version}, "
                f"expected major version {major_expected}"
            )


# ---------------------------------------------------------------------------
# Validation utilities
# ---------------------------------------------------------------------------


def validate_domain_context(module: str, context: Any) -> bool:
    """Check that *context* is the correct type for *module*."""
    expected = DOMAIN_CONTEXT_TYPES.get(module.lower())
    if expected is None:
        raise ValueError(f"Unknown module: {module}")
    if not isinstance(context, expected):
        raise TypeError(
            f"Expected {expected.__name__} for module {module}, "
            f"got {type(context).__name__}"
        )
    return True


def envelope_to_dict(env: AdapterEnvelope) -> Dict[str, Any]:
    """Serialise an envelope to a plain dict (JSON-safe)."""
    return asdict(env)
