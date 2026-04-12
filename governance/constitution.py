"""
Phase Mirror — ConstitutionModel (Lever 1 of MVP Pivot)
ADR-MVP-001: Replaces Coq/Idris formal verification with Pydantic v2 validators.

Each @model_validator method corresponds directly to a numbered L0 invariant
defined in Ξ-Constitution.md and documented in artifacts/docs/adr/ADR-MVP-001.

Usage:
    from governance.constitution import ConstitutionModel, ConstitutionViolation

    try:
        state = ConstitutionModel(**proposed_state_dict)
    except ConstitutionViolation as e:
        # Proposal is unconstitutional — reject and log
        logger.error("L0 violation: %s", e)
    except ValidationError as e:
        # Schema-level failure — reject
        logger.error("Schema violation: %s", e)
"""

from __future__ import annotations

import math
from typing import Annotated, Optional

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# Constants (tunable without changing invariant logic)
# ---------------------------------------------------------------------------

# Art. II §2.2 — Λm threshold: maximum permissible drift rate
LAMBDA_M_THRESHOLD: float = 0.1

# Art. VIII §8.1 — contractivity score must be in (0, 1]
# A score of exactly 1.0 is the Lipschitz boundary; > 1.0 means expansion (illegal)
CONTRACTIVITY_UPPER: float = 1.0
CONTRACTIVITY_LOWER: float = 0.0  # exclusive

# Art. IX §9.4 — rollback window in days
ROLLBACK_WINDOW_DAYS: int = 30

# Circuit-breaker threshold (used by Lever 2; declared here as a constitutional constant)
CIRCUIT_BREAKER_THRESHOLD: int = 3


# ---------------------------------------------------------------------------
# Exception Hierarchy
# ---------------------------------------------------------------------------

class ConstitutionViolation(Exception):
    """Raised when a proposed state violates one or more L0 invariants."""

    def __init__(self, invariant: str, detail: str) -> None:
        self.invariant = invariant
        self.detail = detail
        super().__init__(f"[{invariant}] {detail}")


# ---------------------------------------------------------------------------
# Supporting Types
# ---------------------------------------------------------------------------

class CritiqueResult(BaseModel):
    """Result of a single critique gate (Art. III §3.2)."""
    critique_id: Annotated[int, Field(ge=0, le=9)]
    passed: bool
    reason: Optional[str] = None


class PrimeGate(BaseModel):
    """A declared prime-gated action (Art. VI)."""
    action_name: str
    gate_value: int = Field(gt=1, description="Must be a prime number")


# ---------------------------------------------------------------------------
# ConstitutionModel — the living law
# ---------------------------------------------------------------------------

class ConstitutionModel(BaseModel):
    """
    The constitutional state of a Phase Mirror agent proposal.

    Instantiate with a proposed state dict. If validation succeeds, the
    proposal is constitutionally lawful. If it raises, the proposal is
    rejected — no exceptions.

    Maps to Ξ-Constitution.md v1.0 (Articles II, III, VI, VIII, IX).
    See ADR-MVP-001 for the formal mapping table.
    """

    # --- Core state fields ---

    # Art. II §2.1 — the L2 norm of the system state vector
    state_norm: float = Field(
        gt=0.0,
        description="L2 norm of the system state. Must be finite and positive."
    )

    # Art. II §2.2 — rate of change of the ethical drift metric
    drift_rate: float = Field(
        ge=0.0,
        description="Current drift rate dΨ/dt. Must be < LAMBDA_M_THRESHOLD."
    )

    # Art. III §3.2 — all 10 critique gate results
    critique_results: list[CritiqueResult] = Field(
        min_length=10,
        max_length=10,
        description="Exactly 10 critique results (i=0..9) per Art. III §3.2."
    )

    # Art. VI — declared prime gates
    prime_gates: list[PrimeGate] = Field(
        default_factory=list,
        description="Prime-gated actions declared in artifacts/manifests/prime-gates.yml."
    )

    # Art. VIII §8.1 — Λm contractivity score (Lipschitz constant)
    contractivity_score: float = Field(
        description="Lipschitz contractivity score. Must be in (0, 1]."
    )

    # Art. IX §9.5 — kill-switch state
    kill_switch_active: bool = Field(
        default=False,
        description="If True, all system-level changes are halted immediately."
    )

    # Art. IX §9.4 — Git SHA of the most recent rollback anchor
    rollback_anchor_sha: Optional[str] = Field(
        default=None,
        description="Git commit SHA on the deploy branch. None if no prior commit."
    )

    # Consecutive failure counter (Lever 2 circuit breaker)
    consecutive_failures: int = Field(
        default=0,
        ge=0,
        description="Number of consecutive proposal failures. >= CIRCUIT_BREAKER_THRESHOLD triggers halt."
    )

    # ---------------------------------------------------------------------------
    # L0-1: State norm bounded (Art. II §2.1)
    # ---------------------------------------------------------------------------
    @model_validator(mode='after')
    def l0_1_state_norm_bounded(self) -> 'ConstitutionModel':
        """||S(t)|| < ∞  (Art. II §2.1)"""
        if not math.isfinite(self.state_norm):
            raise ConstitutionViolation(
                "L0-1",
                f"state_norm is not finite: {self.state_norm}. "
                "System state has diverged."
            )
        return self

    # ---------------------------------------------------------------------------
    # L0-2: No exponential ethical drift (Art. II §2.2)
    # ---------------------------------------------------------------------------
    @model_validator(mode='after')
    def l0_2_drift_rate_bounded(self) -> 'ConstitutionModel':
        """δ_c(t) < Λm  (Art. II §2.2 — prevents e^t divergence)"""
        if self.drift_rate >= LAMBDA_M_THRESHOLD:
            raise ConstitutionViolation(
                "L0-2",
                f"drift_rate {self.drift_rate:.6f} >= Λm threshold {LAMBDA_M_THRESHOLD}. "
                "Exponential ethical drift detected."
            )
        return self

    # ---------------------------------------------------------------------------
    # L0-3: All critique gates passed (Art. III §3.3)
    # ---------------------------------------------------------------------------
    @model_validator(mode='after')
    def l0_3_critique_gates_passed(self) -> 'ConstitutionModel':
        """Code ⊢ ∧_{i=0}^{9} Critique_i  (Art. III §3.3)"""
        failed = [r for r in self.critique_results if not r.passed]
        if failed:
            ids = [r.critique_id for r in failed]
            reasons = [r.reason or "(no reason given)" for r in failed]
            raise ConstitutionViolation(
                "L0-3",
                f"Critique gates {ids} failed. Reasons: {reasons}."
            )
        return self

    # ---------------------------------------------------------------------------
    # L0-4: Prime-gate values are prime (Art. VI)
    # ---------------------------------------------------------------------------
    @model_validator(mode='after')
    def l0_4_prime_gates_satisfied(self) -> 'ConstitutionModel':
        """All gate_value fields must be prime (Art. VI)."""
        def is_prime(n: int) -> bool:
            if n < 2:
                return False
            if n == 2:
                return True
            if n % 2 == 0:
                return False
            for i in range(3, int(math.isqrt(n)) + 1, 2):
                if n % i == 0:
                    return False
            return True

        violations = [
            gate.action_name
            for gate in self.prime_gates
            if not is_prime(gate.gate_value)
        ]
        if violations:
            raise ConstitutionViolation(
                "L0-4",
                f"Actions {violations} have non-prime gate values. "
                "All gates must be declared with prime-indexed keys (Art. VI)."
            )
        return self

    # ---------------------------------------------------------------------------
    # L0-5: Λm contractivity (Art. VIII §8.1)
    # ---------------------------------------------------------------------------
    @model_validator(mode='after')
    def l0_5_lambda_m_compliant(self) -> 'ConstitutionModel':
        """0 < contractivity_score ≤ 1.0  (Lipschitz boundary, Art. VIII §8.1)"""
        if self.contractivity_score <= CONTRACTIVITY_LOWER:
            raise ConstitutionViolation(
                "L0-5",
                f"contractivity_score {self.contractivity_score} <= 0. "
                "System is non-contractive (degenerate)."
            )
        if self.contractivity_score > CONTRACTIVITY_UPPER:
            raise ConstitutionViolation(
                "L0-5",
                f"contractivity_score {self.contractivity_score} > {CONTRACTIVITY_UPPER}. "
                "System is expansive — Lipschitz contractivity violated."
            )
        return self

    # ---------------------------------------------------------------------------
    # L0-6: Kill-switch honoured (Art. IX §9.5)
    # ---------------------------------------------------------------------------
    @model_validator(mode='after')
    def l0_6_kill_switch_not_active(self) -> 'ConstitutionModel':
        """If kill_switch_active is True, halt unconditionally (Art. IX §9.5)."""
        if self.kill_switch_active:
            raise ConstitutionViolation(
                "L0-6",
                "Kill-switch is ACTIVE. All system-level changes are halted. "
                "Requires 3/5 Multiplicity Foundation council vote to clear."
            )
        return self

    # ---------------------------------------------------------------------------
    # L0-7: Circuit breaker (Lever 2 interface, Art. IX §9.4 spirit)
    # ---------------------------------------------------------------------------
    @model_validator(mode='after')
    def l0_7_circuit_breaker_not_tripped(self) -> 'ConstitutionModel':
        """consecutive_failures < CIRCUIT_BREAKER_THRESHOLD before human escalation."""
        if self.consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            raise ConstitutionViolation(
                "L0-7",
                f"consecutive_failures={self.consecutive_failures} >= "
                f"threshold={CIRCUIT_BREAKER_THRESHOLD}. "
                "Circuit breaker tripped — human escalation required before proceeding."
            )
        return self

    # ---------------------------------------------------------------------------
    # Utility
    # ---------------------------------------------------------------------------

    def constitutional_summary(self) -> dict:
        """Returns a human-readable summary for audit logs and MCP tool responses."""
        return {
            "status": "LAWFUL",
            "state_norm": self.state_norm,
            "drift_rate": self.drift_rate,
            "contractivity_score": self.contractivity_score,
            "critiques_passed": len(self.critique_results),
            "prime_gates_declared": len(self.prime_gates),
            "rollback_anchor_sha": self.rollback_anchor_sha,
            "consecutive_failures": self.consecutive_failures,
            "kill_switch_active": self.kill_switch_active,
        }
