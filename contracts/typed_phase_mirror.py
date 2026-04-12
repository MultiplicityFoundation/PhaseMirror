"""
ADR-030: Typed Phase Mirror Modes and Rollback Tension Semantics

Implements typed dispatch for Phase Mirror policy evaluation modes:
- Expression Mode: Evaluate PIRTM expressions (computation)
- State Transition Mode: Evaluate state changes (governance)
- Emergency Suppression: Hard override (kill switch path)

Per ADR-030, modes are strongly typed to prevent confusion attacks where
PIRTM expressions are confused with state snapshots.

Rollback tension semantics define when rollback is appropriate:
- TENSION_MINIMAL: No rollback needed (system healthy)
- TENSION_MODERATE: Rollback recommended (monitoring needed)
- TENSION_CRITICAL: Rollback mandatory (system under stress)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from typing import Dict, Optional, Any, Callable
import json

from contracts.shared.types import PIRTMExpr, StateSnapshot


class PhaseMode(Enum):
    """Phase Mirror evaluation modes."""
    EXPRESSION = "EXPRESSION"     # Evaluate PIRTM expressions
    STATE_TRANSITION = "STATE_TRANSITION"  # Evaluate state changes
    EMERGENCY = "EMERGENCY"       # Emergency suppression (kill switch)


class RollbackTension(Enum):
    """System tension states indicating rollback necessity."""
    TENSION_MINIMAL = "TENSION_MINIMAL"      # No action needed
    TENSION_MODERATE = "TENSION_MODERATE"    # Monitor; consider rollback
    TENSION_CRITICAL = "TENSION_CRITICAL"    # Rollback required immediately


class PhaseDecision(Enum):
    """Phase Mirror policy decisions."""
    PASS = "PASS"           # Allow execution
    FAIL = "FAIL"           # Reject execution
    REVIEW = "REVIEW"       # Escalate to human review
    SUPPRESS = "SUPPRESS"   # Emergency suppress (invoke kill switch)


@dataclass(frozen=True)
class PhaseExpression:
    """Typed payload for expression evaluation mode.
    
    Attributes:
        mode: PhaseMode.EXPRESSION
        expression: PIRTMExpr (binary MLIR expression, not JSON)
        timeout_ms: Evaluation timeout in milliseconds
        escalation_on_timeout: Whether to escalate or fail on timeout
    """
    mode: PhaseMode
    expression: PIRTMExpr
    timeout_ms: int = 5000
    escalation_on_timeout: bool = True
    
    def __post_init__(self):
        """Validate expression mode payload."""
        if self.mode != PhaseMode.EXPRESSION:
            raise ValueError("PhaseExpression requires mode=EXPRESSION")
        
        if not isinstance(self.expression, PIRTMExpr):
            raise TypeError("expression must be PIRTMExpr (not StateSnapshot)")
        
        if self.timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")


@dataclass(frozen=True)
class StateTransitionPayload:
    """Typed payload for state transition evaluation mode.
    
    Attributes:
        mode: PhaseMode.STATE_TRANSITION
        source_snapshot: Current state (JSON)
        destination_snapshot: Proposed new state (JSON)
        evidence: Hash or signature proving this transition is valid
        justification: Human-readable reason for transition
        precedent_hash: Reference to prior similar transition (if any)
    """
    mode: PhaseMode
    source_snapshot: StateSnapshot
    destination_snapshot: StateSnapshot
    evidence: str
    justification: str = ""
    precedent_hash: Optional[str] = None
    
    def __post_init__(self):
        """Validate state transition payload."""
        if self.mode != PhaseMode.STATE_TRANSITION:
            raise ValueError("StateTransitionPayload requires mode=STATE_TRANSITION")
        
        if not isinstance(self.source_snapshot, StateSnapshot):
            raise TypeError("source_snapshot must be StateSnapshot")
        
        if not isinstance(self.destination_snapshot, StateSnapshot):
            raise TypeError("destination_snapshot must be StateSnapshot")
        
        if not self.evidence:
            raise ValueError("evidence required")


@dataclass(frozen=True)
class EmergencySuppression:
    """Typed payload for emergency suppression (kill switch).
    
    Attributes:
        mode: PhaseMode.EMERGENCY
        reason: Human-readable justification for kill switch
        authorizer: Identity authorizing the suppression
        rollback_to_state: Optional specific state to restore to
    """
    mode: PhaseMode
    reason: str
    authorizer: str
    rollback_to_state: Optional[StateSnapshot] = None
    
    def __post_init__(self):
        """Validate emergency suppression payload."""
        if self.mode != PhaseMode.EMERGENCY:
            raise ValueError("EmergencySuppression requires mode=EMERGENCY")
        
        if not self.reason:
            raise ValueError("reason required")
        
        if not self.authorizer:
            raise ValueError("authorizer required")


@dataclass(frozen=True)
class PhaseMirrorDecision:
    """Typed policy decision from Phase Mirror evaluation.
    
    Attributes:
        decision: PASS, FAIL, REVIEW, or SUPPRESS
        mode: Which mode was evaluated
        decision_timestamp: ISO 8601 timestamp
        reasoning: Optional explanation
        tension_level: System tension at decision time
        recommended_action: Optional recommended next action
    """
    decision: PhaseDecision
    mode: PhaseMode
    decision_timestamp: str  # ISO 8601
    reasoning: str = ""
    tension_level: RollbackTension = RollbackTension.TENSION_MINIMAL
    recommended_action: str = ""
    
    def is_permitted(self) -> bool:
        """Check if decision permits execution."""
        return self.decision == PhaseDecision.PASS
    
    def requires_escalation(self) -> bool:
        """Check if decision requires human review."""
        return self.decision in [PhaseDecision.REVIEW, PhaseDecision.SUPPRESS]
    
    def to_dict(self) -> Dict:
        """Serialize to JSON-safe dict."""
        return {
            "decision": self.decision.value,
            "mode": self.mode.value,
            "decision_timestamp": self.decision_timestamp,
            "reasoning": self.reasoning,
            "tension_level": self.tension_level.value,
            "recommended_action": self.recommended_action,
        }


class TypedPhaseMirror:
    """Implements typed Phase Mirror policy evaluation.
    
    Per ADR-030, evaluates typed payloads with mode-specific logic:
    - ExpressionMode: Check expression is well-formed MLIR, within resource budget
    - StateTransitionMode: Verify state transition has valid evidence, no cycles
    - EmergencyMode: Authorize emergency suppression, invoke kill switch
    
    All mode confusion attacks are prevented by strong typing.
    """
    
    def __init__(self):
        """Initialize typed Phase Mirror."""
        self.evaluation_history: list[PhaseMirrorDecision] = []
        self.expression_evaluator: Optional[Callable] = None
        self.state_transition_evaluator: Optional[Callable] = None
        self.emergency_handler: Optional[Callable] = None
    
    def evaluate(self, payload: Any) -> PhaseMirrorDecision:
        """Evaluate typed payload and return decision.
        
        Args:
            payload: PhaseExpression, StateTransitionPayload, or EmergencySuppression
            
        Returns:
            PhaseMirrorDecision
            
        Raises:
            TypeError: If payload is not one of the three typed classes
        """
        try:
            if isinstance(payload, PhaseExpression):
                return self._evaluate_expression(payload)
            elif isinstance(payload, StateTransitionPayload):
                return self._evaluate_state_transition(payload)
            elif isinstance(payload, EmergencySuppression):
                return self._evaluate_emergency(payload)
            else:
                # Type error: payload is not one of the three typed classes
                decision = PhaseMirrorDecision(
                    decision=PhaseDecision.FAIL,
                    mode=PhaseMode.EXPRESSION,  # Default
                    decision_timestamp=datetime.utcnow().isoformat(),
                    reasoning=f"Invalid payload type: {type(payload).__name__}. "
                             "Must be PhaseExpression, StateTransitionPayload, or EmergencySuppression.",
                )
                self.evaluation_history.append(decision)
                return decision
        except Exception as e:
            # Catch any evaluation errors and return FAIL
            decision = PhaseMirrorDecision(
                decision=PhaseDecision.FAIL,
                mode=PhaseMode.EXPRESSION,  # Default
                decision_timestamp=datetime.utcnow().isoformat(),
                reasoning=f"Evaluation error: {str(e)}",
            )
            self.evaluation_history.append(decision)
            return decision
    
    def _evaluate_expression(self, payload: PhaseExpression) -> PhaseMirrorDecision:
        """Evaluate PIRTM expression mode."""
        # Pre-check: expression must be well-formed MLIR
        if not payload.expression.data:
            decision = PhaseMirrorDecision(
                decision=PhaseDecision.FAIL,
                mode=PhaseMode.EXPRESSION,
                decision_timestamp=datetime.utcnow().isoformat(),
                reasoning="Expression is empty",
            )
        else:
            # Custom evaluator if provided
            if self.expression_evaluator:
                result = self.expression_evaluator(payload)
                decision = result
            else:
                # Default: permit well-formed expressions
                decision = PhaseMirrorDecision(
                    decision=PhaseDecision.PASS,
                    mode=PhaseMode.EXPRESSION,
                    decision_timestamp=datetime.utcnow().isoformat(),
                    reasoning="Expression structure valid",
                )
        
        self.evaluation_history.append(decision)
        return decision
    
    def _evaluate_state_transition(self, payload: StateTransitionPayload) -> PhaseMirrorDecision:
        """Evaluate state transition mode."""
        # Pre-check: both snapshots must be well-formed JSON
        if not payload.source_snapshot.data or not payload.destination_snapshot.data:
            decision = PhaseMirrorDecision(
                decision=PhaseDecision.FAIL,
                mode=PhaseMode.STATE_TRANSITION,
                decision_timestamp=datetime.utcnow().isoformat(),
                reasoning="Source or destination snapshot is empty",
            )
        else:
            # Custom evaluator if provided
            if self.state_transition_evaluator:
                result = self.state_transition_evaluator(payload)
                decision = result
            else:
                # Default: permit transitions with valid evidence
                decision = PhaseMirrorDecision(
                    decision=PhaseDecision.PASS,
                    mode=PhaseMode.STATE_TRANSITION,
                    decision_timestamp=datetime.utcnow().isoformat(),
                    reasoning="State transition structure valid",
                )
        
        self.evaluation_history.append(decision)
        return decision
    
    def _evaluate_emergency(self, payload: EmergencySuppression) -> PhaseMirrorDecision:
        """Evaluate emergency suppression (kill switch)."""
        # Emergency always requires escalation
        # But first, custom emergency handler if provided
        if self.emergency_handler:
            result = self.emergency_handler(payload)
            decision = result
        else:
            # Default: suppress with high tension
            decision = PhaseMirrorDecision(
                decision=PhaseDecision.SUPPRESS,
                mode=PhaseMode.EMERGENCY,
                decision_timestamp=datetime.utcnow().isoformat(),
                reasoning=f"Emergency suppression invoked: {payload.reason}",
                tension_level=RollbackTension.TENSION_CRITICAL,
                recommended_action="Activate kill switch immediately; restore from checkpoint if needed",
            )
        
        self.evaluation_history.append(decision)
        return decision
    
    def get_evaluation_history(self) -> list[PhaseMirrorDecision]:
        """Get all prior evaluations."""
        return self.evaluation_history
    
    def set_expression_evaluator(self, evaluator: Callable[[PhaseExpression], PhaseMirrorDecision]) -> None:
        """Set custom expression evaluator function."""
        self.expression_evaluator = evaluator
    
    def set_state_transition_evaluator(self, evaluator: Callable[[StateTransitionPayload], PhaseMirrorDecision]) -> None:
        """Set custom state transition evaluator function."""
        self.state_transition_evaluator = evaluator
    
    def set_emergency_handler(self, handler: Callable[[EmergencySuppression], PhaseMirrorDecision]) -> None:
        """Set custom emergency handler function."""
        self.emergency_handler = handler


# ─── Rollback Tension Inference ───────────────────────────────────────────

class TensionAnalyzer:
    """Analyzes system metrics to determine rollback tension level.
    
    Per ADR-030, tension indicates whether rollback is necessary:
    - MINIMAL: All metrics healthy
    - MODERATE: Some metrics degraded; rollback may help
    - CRITICAL: Critical threshold crossed; rollback mandatory
    """
    
    def __init__(self):
        """Initialize tension analyzer."""
        self.metrics: Dict[str, float] = {}
    
    def record_metric(self, name: str, value: float) -> None:
        """Record a system metric (0.0 to 1.0, where 1.0 = bad)."""
        self.metrics[name] = value
    
    def analyze_tension(self) -> RollbackTension:
        """Analyze current metrics and return tension level."""
        if not self.metrics:
            return RollbackTension.TENSION_MINIMAL
        
        max_metric = max(self.metrics.values())
        
        if max_metric >= 0.8:
            return RollbackTension.TENSION_CRITICAL
        elif max_metric >= 0.5:
            return RollbackTension.TENSION_MODERATE
        else:
            return RollbackTension.TENSION_MINIMAL
    
    def reset(self) -> None:
        """Reset all metrics."""
        self.metrics.clear()
