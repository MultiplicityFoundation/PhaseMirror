"""
Test suite for ConstitutionModel (ADR-MVP-001).
100% coverage of all 7 L0 invariants is required by ADR amendment process.
"""
import pytest
from pydantic import ValidationError

from governance.constitution import (
    ConstitutionModel,
    ConstitutionViolation,
    CritiqueResult,
    PrimeGate,
    LAMBDA_M_THRESHOLD,
    CIRCUIT_BREAKER_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def all_passing_critiques() -> list[dict]:
    return [{"critique_id": i, "passed": True} for i in range(10)]


def lawful_state(**overrides) -> dict:
    """Returns a minimal lawful state. Override any field to test violations."""
    base = {
        "state_norm": 1.0,
        "drift_rate": 0.01,
        "critique_results": all_passing_critiques(),
        "prime_gates": [],
        "contractivity_score": 0.9,
        "kill_switch_active": False,
        "rollback_anchor_sha": "abc1234",
        "consecutive_failures": 0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestLawfulState:
    def test_lawful_state_validates(self):
        state = ConstitutionModel(**lawful_state())
        assert state.constitutional_summary()["status"] == "LAWFUL"

    def test_lawful_state_with_prime_gates(self):
        state = ConstitutionModel(**lawful_state(
            prime_gates=[
                {"action_name": "deploy_model", "gate_value": 7},
                {"action_name": "self_modify", "gate_value": 13},
            ]
        ))
        assert len(state.prime_gates) == 2

    def test_constitutional_summary_keys(self):
        state = ConstitutionModel(**lawful_state())
        summary = state.constitutional_summary()
        expected_keys = {
            "status", "state_norm", "drift_rate", "contractivity_score",
            "critiques_passed", "prime_gates_declared",
            "rollback_anchor_sha", "consecutive_failures", "kill_switch_active"
        }
        assert expected_keys.issubset(summary.keys())


# ---------------------------------------------------------------------------
# L0-1: state_norm_bounded
# ---------------------------------------------------------------------------

class TestL0_1_StateNormBounded:
    def test_infinite_norm_raises(self):
        with pytest.raises(ConstitutionViolation, match="L0-1"):
            ConstitutionModel(**lawful_state(state_norm=float('inf')))

    def test_nan_norm_raises(self):
        with pytest.raises((ConstitutionViolation, ValidationError)):
            ConstitutionModel(**lawful_state(state_norm=float('nan')))

    def test_zero_norm_raises_schema(self):
        """state_norm=0 is excluded by Field(gt=0) before L0-1 fires."""
        with pytest.raises(ValidationError):
            ConstitutionModel(**lawful_state(state_norm=0.0))


# ---------------------------------------------------------------------------
# L0-2: drift_rate_bounded
# ---------------------------------------------------------------------------

class TestL0_2_DriftRateBounded:
    def test_drift_at_threshold_raises(self):
        with pytest.raises(ConstitutionViolation, match="L0-2"):
            ConstitutionModel(**lawful_state(drift_rate=LAMBDA_M_THRESHOLD))

    def test_drift_above_threshold_raises(self):
        with pytest.raises(ConstitutionViolation, match="L0-2"):
            ConstitutionModel(**lawful_state(drift_rate=1.0))

    def test_drift_just_below_threshold_passes(self):
        state = ConstitutionModel(**lawful_state(drift_rate=LAMBDA_M_THRESHOLD - 1e-9))
        assert state.drift_rate < LAMBDA_M_THRESHOLD


# ---------------------------------------------------------------------------
# L0-3: critique_gates_passed
# ---------------------------------------------------------------------------

class TestL0_3_CritiqueGatesPassed:
    def test_single_failing_critique_raises(self):
        critiques = all_passing_critiques()
        critiques[3]["passed"] = False
        critiques[3]["reason"] = "Semantic drift detected"
        with pytest.raises(ConstitutionViolation, match="L0-3"):
            ConstitutionModel(**lawful_state(critique_results=critiques))

    def test_multiple_failing_critiques_raises(self):
        critiques = all_passing_critiques()
        critiques[0]["passed"] = False
        critiques[9]["passed"] = False
        with pytest.raises(ConstitutionViolation, match="L0-3"):
            ConstitutionModel(**lawful_state(critique_results=critiques))

    def test_wrong_number_of_critiques_raises(self):
        with pytest.raises(ValidationError):
            ConstitutionModel(**lawful_state(
                critique_results=[{"critique_id": 0, "passed": True}]
            ))


# ---------------------------------------------------------------------------
# L0-4: prime_gates_satisfied
# ---------------------------------------------------------------------------

class TestL0_4_PrimeGatesSatisfied:
    def test_non_prime_gate_raises(self):
        with pytest.raises(ConstitutionViolation, match="L0-4"):
            ConstitutionModel(**lawful_state(
                prime_gates=[{"action_name": "deploy", "gate_value": 4}]
            ))

    def test_gate_value_1_raises(self):
        """1 is not prime."""
        with pytest.raises((ConstitutionViolation, ValidationError)):
            ConstitutionModel(**lawful_state(
                prime_gates=[{"action_name": "deploy", "gate_value": 1}]
            ))

    def test_valid_prime_gates_pass(self):
        state = ConstitutionModel(**lawful_state(
            prime_gates=[
                {"action_name": "a", "gate_value": 2},
                {"action_name": "b", "gate_value": 97},
                {"action_name": "c", "gate_value": 1009},
            ]
        ))
        assert len(state.prime_gates) == 3


# ---------------------------------------------------------------------------
# L0-5: lambda_m_compliant
# ---------------------------------------------------------------------------

class TestL0_5_LambdaMCompliant:
    def test_contractivity_above_one_raises(self):
        with pytest.raises(ConstitutionViolation, match="L0-5"):
            ConstitutionModel(**lawful_state(contractivity_score=1.0001))

    def test_contractivity_zero_raises(self):
        with pytest.raises(ConstitutionViolation, match="L0-5"):
            ConstitutionModel(**lawful_state(contractivity_score=0.0))

    def test_contractivity_exactly_one_passes(self):
        """Score of 1.0 is the Lipschitz boundary — lawful."""
        state = ConstitutionModel(**lawful_state(contractivity_score=1.0))
        assert state.contractivity_score == 1.0

    def test_contractivity_negative_raises(self):
        with pytest.raises(ConstitutionViolation, match="L0-5"):
            ConstitutionModel(**lawful_state(contractivity_score=-0.1))


# ---------------------------------------------------------------------------
# L0-6: kill_switch_not_active
# ---------------------------------------------------------------------------

class TestL0_6_KillSwitch:
    def test_kill_switch_active_raises(self):
        with pytest.raises(ConstitutionViolation, match="L0-6"):
            ConstitutionModel(**lawful_state(kill_switch_active=True))

    def test_kill_switch_false_passes(self):
        state = ConstitutionModel(**lawful_state(kill_switch_active=False))
        assert not state.kill_switch_active


# ---------------------------------------------------------------------------
# L0-7: circuit_breaker_not_tripped
# ---------------------------------------------------------------------------

class TestL0_7_CircuitBreaker:
    def test_at_threshold_raises(self):
        with pytest.raises(ConstitutionViolation, match="L0-7"):
            ConstitutionModel(**lawful_state(
                consecutive_failures=CIRCUIT_BREAKER_THRESHOLD
            ))

    def test_above_threshold_raises(self):
        with pytest.raises(ConstitutionViolation, match="L0-7"):
            ConstitutionModel(**lawful_state(consecutive_failures=99))

    def test_one_below_threshold_passes(self):
        state = ConstitutionModel(**lawful_state(
            consecutive_failures=CIRCUIT_BREAKER_THRESHOLD - 1
        ))
        assert state.consecutive_failures == CIRCUIT_BREAKER_THRESHOLD - 1


# ---------------------------------------------------------------------------
# Violation exception interface
# ---------------------------------------------------------------------------

class TestConstitutionViolation:
    def test_violation_has_invariant_attribute(self):
        try:
            ConstitutionModel(**lawful_state(kill_switch_active=True))
        except ConstitutionViolation as e:
            assert e.invariant == "L0-6"
            assert "Kill-switch" in e.detail

    def test_violation_str_includes_invariant(self):
        try:
            ConstitutionModel(**lawful_state(drift_rate=1.0))
        except ConstitutionViolation as e:
            assert "L0-2" in str(e)
