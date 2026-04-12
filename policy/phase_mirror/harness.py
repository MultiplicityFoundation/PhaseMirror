from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from contracts.shared.types import PIRTMExpr, StateSnapshot, StateTransitionPayload
from governance.ledger import AuditLedger
from policy.phase_mirror import KILL_SWITCH_THRESHOLD, evaluate


EXPRESSIONS_DIR = Path("examples/transitions")
TRANSITIONS_DIR = Path("examples/queries")
DEFAULT_HARNESS_LEDGER_PATH = Path("state/phase_mirror_harness_ledger.json")
DEFAULT_HARNESS_REPORT_PATH = Path("state/phase_mirror_harness_report.json")


class PhaseGoldenVectorLoader:
    """Load and parse Phase Mirror golden test vectors."""

    @staticmethod
    def load_expressions(path: Path = EXPRESSIONS_DIR) -> list[dict[str, Any]]:
        vectors: list[dict[str, Any]] = []
        for file_path in sorted(path.glob("*_expressions.json")):
            data = json.loads(file_path.read_text(encoding="utf-8"))
            vectors.extend(data.get("expressions", []))
        return vectors

    @staticmethod
    def load_state_transitions(path: Path = TRANSITIONS_DIR) -> list[dict[str, Any]]:
        vectors: list[dict[str, Any]] = []
        for file_path in sorted(path.glob("*_transitions.json")):
            data = json.loads(file_path.read_text(encoding="utf-8"))
            vectors.extend(data.get("transitions", []))
        return vectors


@dataclass(frozen=True)
class TestHarnessResult:
    vector_id: str
    payload_type: str
    expected: bool
    actual: bool
    rho: float

    @property
    def passed(self) -> bool:
        return self.expected == self.actual

    def to_dict(self) -> dict[str, Any]:
        return {
            "vector_id": self.vector_id,
            "payload_type": self.payload_type,
            "expected": self.expected,
            "actual": self.actual,
            "rho": self.rho,
            "passed": self.passed,
        }


class PhaseMirrorTestHarness:
    """Integration harness for the evaluation pipeline."""

    def __init__(self, *, ledger_output: Path, report_output: Path):
        self.ledger = AuditLedger(location=ledger_output)
        self.report_output = report_output
        self.results: list[TestHarnessResult] = []
        self.start_time: datetime | None = None
        self.end_time: datetime | None = None

    def run_all(self) -> dict[str, Any]:
        self.start_time = datetime.now(timezone.utc)

        loader = PhaseGoldenVectorLoader()
        expr_vectors = loader.load_expressions()
        transition_vectors = loader.load_state_transitions()

        for vector in expr_vectors:
            self._test_expression(vector)

        for vector in transition_vectors:
            self._test_state_transition(vector)

        self.end_time = datetime.now(timezone.utc)
        self.ledger.save()
        summary = self._summarize(expr_vectors=len(expr_vectors), transitions=len(transition_vectors))

        self.report_output.parent.mkdir(parents=True, exist_ok=True)
        self.report_output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        return summary

    def _test_expression(self, vector: dict[str, Any]) -> None:
        expected = bool(vector.get("should_execute", True))
        vector_id = str(vector.get("id", f"expr_{len(self.results)}"))

        try:
            input_text = str(vector.get("input_text", ""))
            body = str(vector.get("output_expr", ""))
            expr = PIRTMExpr(f"\x00MLIR\x00MAGIC{body}".encode("utf-8"))

            report = evaluate(
                input_text=input_text,
                output_expr=expr,
                rollback_trigger=str(vector.get("rollback_trigger", "none")),
                audit_ledger=self.ledger,
            )
            actual = report.execute
            rho = report.rho
        except Exception:
            actual = False
            rho = float("nan")

        self.results.append(
            TestHarnessResult(
                vector_id=vector_id,
                payload_type="expression",
                expected=expected,
                actual=actual,
                rho=rho,
            )
        )

    def _test_state_transition(self, vector: dict[str, Any]) -> None:
        expected = bool(vector.get("should_execute", True))
        vector_id = str(vector.get("id", f"trans_{len(self.results)}"))

        try:
            snapshot_id = str(vector.get("snapshot_id", f"snapshot-{len(self.results)}"))
            payload = {
                "snapshot_id": snapshot_id,
                "state": vector.get("state", {"ok": True}),
                "governance_version": vector.get("governance_version", "phase-mirror-stub-v0"),
                "enforcement_bits": vector.get("bits", {}),
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }
            snapshot = StateSnapshot(json.dumps(payload).encode("utf-8"), snapshot_id=snapshot_id)

            derived = StateTransitionPayload.from_snapshot(
                input_text=str(vector.get("input_text", "rollback:vector")),
                snapshot=snapshot,
                rollback_trigger=str(vector.get("rollback_trigger", "none")),
            )
            typed = StateTransitionPayload(
                input_text=derived.input_text,
                snapshot=derived.snapshot,
                enforcement_bits=derived.enforcement_bits,
                rollback_trigger=derived.rollback_trigger,
                governance_version=derived.governance_version,
                twin_desynced=bool(vector.get("twin_desynced", False)),
                stale_base_disabled=bool(vector.get("stale_base_disabled", False)),
                boundary_absent=bool(vector.get("boundary_absent", False)),
            )

            report = evaluate(typed, audit_ledger=self.ledger)
            actual = report.execute
            rho = report.rho
        except Exception:
            actual = False
            rho = float("nan")

        self.results.append(
            TestHarnessResult(
                vector_id=vector_id,
                payload_type="state_transition",
                expected=expected,
                actual=actual,
                rho=rho,
            )
        )

    def _summarize(self, *, expr_vectors: int, transitions: int) -> dict[str, Any]:
        passed = sum(1 for result in self.results if result.passed)
        failed = len(self.results) - passed
        rho_values = [result.rho for result in self.results if result.rho == result.rho]

        try:
            self.ledger.validate()
            ledger_valid = True
            ledger_error = None
        except Exception as exc:
            ledger_valid = False
            ledger_error = str(exc)

        duration_seconds = 0.0
        if self.start_time is not None and self.end_time is not None:
            duration_seconds = (self.end_time - self.start_time).total_seconds()

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": duration_seconds,
            "vector_counts": {
                "expressions": expr_vectors,
                "state_transitions": transitions,
                "total": len(self.results),
            },
            "passed": passed,
            "failed": failed,
            "pass_rate_percent": (passed / len(self.results) * 100.0) if self.results else 0.0,
            "kill_switch_threshold": KILL_SWITCH_THRESHOLD,
            "ledger_entries": len(self.ledger.entries),
            "ledger_valid": ledger_valid,
            "ledger_error": ledger_error,
            "rho_statistics": {
                "min": min(rho_values) if rho_values else None,
                "max": max(rho_values) if rho_values else None,
                "mean": (sum(rho_values) / len(rho_values)) if rho_values else None,
            },
            "failed_tests": [result.to_dict() for result in self.results if not result.passed],
        }


def run_phase_mirror_harness(
    *,
    ledger_output: Path = DEFAULT_HARNESS_LEDGER_PATH,
    report_output: Path = DEFAULT_HARNESS_REPORT_PATH,
) -> dict[str, Any]:
    harness = PhaseMirrorTestHarness(
        ledger_output=ledger_output,
        report_output=report_output,
    )
    return harness.run_all()
