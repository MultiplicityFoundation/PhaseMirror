from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence, Union

from contracts.shared.types import (
    PIRTMExpr,
    StateSnapshot,
    ExpressionPayload,
    StateTransitionPayload,
    PhaseMirrorPayload,
)
from governance.ledger import AuditLedger
from .tension_policy import (
    CURRENT_RUNTIME_GOVERNANCE_VERSION,
    get_tension_definition,
    normalize_trigger,
)


PhaseMirrorDecision = Literal["PASS", "FAIL", "REVIEW"]
RHO_STAR = 0.7
KAPPA_MIN = 0.3

# Gate D scorer weights (D-04, D-05)
EXPR_WEIGHTS = {
    "semantic_drift": 0.3,
    "contractivity_assertion_absent": 0.4,
    "governance_version_mismatch": 0.2,
    "unauthorized_prime_index": 0.3,
    "verbatim_echo": 0.1,
    "empty_expression": 1.0,  # auto-fail
}

STATE_WEIGHTS = {
    "bad_precedent": 0.5,
    "twin_desynced": 0.4,
    "governance_version_mismatch": 0.2,
    "stale_base_disabled": 0.3,
    "boundary_absent": 0.3,
    "rollback_trigger_active": 0.5,  # legacy signal retained for compatibility
}


@dataclass(frozen=True)
class DissonanceSignal:
    """Represents one tension or policy violation signal."""

    signal_id: str
    severity: Literal["low", "medium", "high", "auto_fail"]
    summary: str


@dataclass(frozen=True)
class DissonanceReport:
    """Per ADR-013 and ADR-015: Complete tension audit report."""

    execute: bool
    rho: float
    rho_star: float = RHO_STAR
    kappa_min: float = KAPPA_MIN
    rho_threshold: float = 1.0
    tensions: tuple[DissonanceSignal, ...] = field(default_factory=tuple)
    suppressed_tensions: tuple[DissonanceSignal, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        signal_ids = [signal.signal_id for signal in self.get_all_tensions()]
        if len(signal_ids) != len(set(signal_ids)):
            raise ValueError("Duplicate tension signal_id in tensions + suppressed_tensions")

    @property
    def suppressed(self) -> tuple[DissonanceSignal, ...]:
        """Backward-compatible alias for pre-ADR-015 callers."""

        return self.suppressed_tensions

    @property
    def governance_version(self) -> str:
        """Version string for backward compatibility."""

        return "phase-mirror-v0.2-adr013"

    @property
    def decision(self) -> PhaseMirrorDecision:
        """Backward-compatible decision surface (PASS/FAIL)."""

        return "PASS" if self.execute else "FAIL"

    @property
    def tension_count(self) -> int:
        """Total tension count (decision-relevant + suppressed)."""

        return len(self.tensions) + len(self.suppressed_tensions)

    def get_all_tensions(self) -> tuple[DissonanceSignal, ...]:
        """Return all evaluated tensions for audit use."""

        return self.tensions + self.suppressed_tensions

    @property
    def highest_risk_tension(self) -> str | None:
        """Highest severity tension summary."""

        all_tensions = self.get_all_tensions()
        if not all_tensions:
            return None
        severity_rank = {"low": 0, "medium": 1, "high": 2, "auto_fail": 3}
        highest = max(all_tensions, key=lambda s: severity_rank[s.severity])
        return highest.summary

    @property
    def recommended_action(self) -> str:
        """Action based on report state."""

        if not self.execute:
            if any(s.signal_id == "TYPE_CONFUSION" for s in self.tensions):
                return "TYPE_CONFUSION detected; payload type does not match mode. Kill-switch engagement recommended."
            auto_fail_signal = next((s for s in self.tensions if s.severity == "auto_fail"), None)
            if auto_fail_signal is not None:
                return f"Auto-fail tension {auto_fail_signal.signal_id} triggered; execution blocked and full audit retained."
            return "Kill-switch threshold exceeded; emergency rollback recommended."
        if self.tensions:
            return f"{len(self.tensions)} tension(s) detected but below threshold; monitor."
        return "No tensions detected; operation approved."

    @property
    def safe(self) -> bool:
        """Operator-facing invariant surface: safe is always equivalent to execute."""

        return self.execute

    @property
    def margin_to_fail(self) -> float:
        """Positive means headroom remains; non-positive means threshold reached/breached."""

        return self.rho_threshold - self.rho

    @property
    def threshold_state(self) -> str:
        if self.rho >= self.rho_threshold:
            return "at_or_above_threshold"
        return "below_threshold"

    def to_dict(self) -> dict[str, Any]:
        """Serialize using the ADR-015 wire schema."""

        return {
            "execute": self.execute,
            "safe": self.safe,
            "rho": self.rho,
            "rho_star": self.rho_star,
            "kappa_min": self.kappa_min,
            "rho_threshold": self.rho_threshold,
            "margin_to_fail": self.margin_to_fail,
            "threshold_state": self.threshold_state,
            "decision": self.decision,
            "tensions": [
                {
                    "signal_id": signal.signal_id,
                    "severity": signal.severity,
                    "summary": signal.summary,
                }
                for signal in self.tensions
            ],
            "suppressed_tensions": [
                {
                    "signal_id": signal.signal_id,
                    "severity": signal.severity,
                    "summary": signal.summary,
                }
                for signal in self.suppressed_tensions
            ],
            "metadata": dict(self.metadata),
        }


KILL_SWITCH_THRESHOLD = 1.0


def _dedupe_signals(signals: Sequence[DissonanceSignal]) -> tuple[DissonanceSignal, ...]:
    unique_by_id: dict[str, DissonanceSignal] = {}
    for signal in signals:
        unique_by_id.setdefault(signal.signal_id, signal)
    return tuple(unique_by_id.values())


def evaluate(
    input_text: Union[str, PhaseMirrorPayload],
    output_expr: Union[PIRTMExpr, StateSnapshot, None] = None,
    rollback_trigger: str = "none",
    audit_ledger: AuditLedger | None = None,
    inject_tensions: Sequence[tuple[str, float]] | None = None,
) -> DissonanceReport:
    """Type-safe evaluation of Phase Mirror decision."""

    if isinstance(input_text, (ExpressionPayload, StateTransitionPayload)):
        report = _evaluate_payload(input_text, inject_tensions=inject_tensions)
        if audit_ledger is not None:
            audit_ledger.append(report)
        return report

    if not isinstance(input_text, str):
        report = _type_confusion_report(type(input_text).__name__, rollback_trigger)
        if audit_ledger is not None:
            audit_ledger.append(report)
        return report

    inferred_trigger = rollback_trigger
    if inferred_trigger == "none" and input_text.startswith("rollback:"):
        inferred_trigger = input_text.split(":", 1)[1].strip() or "none"
    inferred_trigger = normalize_trigger(inferred_trigger)

    if isinstance(output_expr, PIRTMExpr):
        payload = ExpressionPayload(
            input_text=input_text,
            output_expr=output_expr,
            rollback_trigger=inferred_trigger,
        )
        report = _evaluate_payload(
            payload,
            payload_type_override="PIRTMExpr",
            inject_tensions=inject_tensions,
        )
        if audit_ledger is not None:
            audit_ledger.append(report)
        return report

    if isinstance(output_expr, StateSnapshot):
        payload = StateTransitionPayload.from_snapshot(
            input_text=input_text,
            snapshot=output_expr,
            rollback_trigger=inferred_trigger,
        )
        report = _evaluate_payload(
            payload,
            payload_type_override="StateSnapshot",
            inject_tensions=inject_tensions,
        )
        if audit_ledger is not None:
            audit_ledger.append(report)
        return report

    report = _type_confusion_report(type(output_expr).__name__, inferred_trigger)
    if audit_ledger is not None:
        audit_ledger.append(report)
    return report


def _type_confusion_report(found_type: str, trigger: str) -> DissonanceReport:
    return DissonanceReport(
        execute=False,
        rho=2.0,
        rho_star=RHO_STAR,
        kappa_min=KAPPA_MIN,
        rho_threshold=KILL_SWITCH_THRESHOLD,
        tensions=(
            DissonanceSignal(
                signal_id="TYPE_CONFUSION",
                severity="auto_fail",
                summary=(
                    "payload type is "
                    f"{found_type}, expected ExpressionPayload/StateTransitionPayload "
                    "or legacy PIRTMExpr/StateSnapshot"
                ),
            ),
        ),
        suppressed_tensions=(),
        metadata={
            "rollback_trigger": normalize_trigger(trigger),
            "mode": "inferred_error",
            "error_type": found_type,
            "type_confusion_rejected": True,
        },
    )


def _evaluate_payload(
    payload: PhaseMirrorPayload,
    payload_type_override: str | None = None,
    inject_tensions: Sequence[tuple[str, float]] | None = None,
) -> DissonanceReport:
    if isinstance(payload, ExpressionPayload):
        inferred_trigger = normalize_trigger(payload.rollback_trigger)
        rho, tensions, suppressed = _score_expression(
            payload.input_text, payload.output_expr, inferred_trigger
        )
        mode = "expression"
        payload_type = "ExpressionPayload"
    elif isinstance(payload, StateTransitionPayload):
        inferred_trigger = normalize_trigger(payload.rollback_trigger)
        rho, tensions, suppressed = _score_state_transition_payload(payload)
        mode = "state_transition"
        payload_type = "StateTransitionPayload"
    else:
        return _type_confusion_report(type(payload).__name__, "none")

    if inject_tensions:
        for signal_id, weight in inject_tensions:
            tensions.append(
                DissonanceSignal(
                    signal_id=f"{signal_id}_injected",
                    severity="low",
                    summary=f"Injected test tension (weight={weight})",
                )
            )
            rho += max(0.0, float(weight))

    rho = min(rho, 2.0)
    auto_fail_tensions = tuple(signal for signal in tensions if signal.severity == "auto_fail")
    if auto_fail_tensions:
        audit_only_tensions = _dedupe_signals([
            *(signal for signal in tensions if signal.severity != "auto_fail"),
            *suppressed,
        ])
        return DissonanceReport(
            execute=False,
            rho=rho,
            rho_star=RHO_STAR,
            kappa_min=KAPPA_MIN,
            rho_threshold=KILL_SWITCH_THRESHOLD,
            tensions=auto_fail_tensions,
            suppressed_tensions=audit_only_tensions,
            metadata={
                "rollback_trigger": inferred_trigger,
                "mode": mode,
                "payload_type": payload_type_override or payload_type,
                "auto_fail": [signal.signal_id for signal in auto_fail_tensions],
            },
        )

    execute = rho < KILL_SWITCH_THRESHOLD
    return DissonanceReport(
        execute=execute,
        rho=rho,
        rho_star=RHO_STAR,
        kappa_min=KAPPA_MIN,
        rho_threshold=KILL_SWITCH_THRESHOLD,
        tensions=tuple(tensions),
        suppressed_tensions=tuple(suppressed),
        metadata={
            "rollback_trigger": inferred_trigger,
            "mode": mode,
            "payload_type": payload_type_override or payload_type,
        },
    )


def _score_state_transition_payload(
    payload: StateTransitionPayload,
) -> tuple[float, list[DissonanceSignal], list[DissonanceSignal]]:
    """Gate D typed state-transition scorer with legacy-signal compatibility."""

    tensions: list[DissonanceSignal] = []
    suppressed: list[DissonanceSignal] = []
    rho = 0.0
    resolved_trigger = normalize_trigger(payload.rollback_trigger)

    if not payload.input_text.strip():
        tensions.append(
            DissonanceSignal(
                signal_id="empty_transition_input",
                severity="high",
                summary="Input text is empty for state transition evaluation",
            )
        )
        rho += 0.3

    if not payload.enforcement_bits.legitimacy():
        rho += _record_configured_tension(
            "LEGITIMACY_PREDICATE_FAILED",
            trigger=resolved_trigger,
            tensions=tensions,
            suppressed=suppressed,
        )

    if resolved_trigger != "none":
        tensions.append(
            DissonanceSignal(
                signal_id="rollback_trigger_active",
                severity="high",
                summary=f"Rollback trigger detected: {resolved_trigger}. Emergency mode active; governance approval required.",
            )
        )
        rho += STATE_WEIGHTS["rollback_trigger_active"]

    snapshot_state: dict[str, Any] = {}
    try:
        parsed_snapshot = json.loads(payload.snapshot.data.decode("utf-8"))
        if isinstance(parsed_snapshot, dict):
            if isinstance(parsed_snapshot.get("state"), dict):
                snapshot_state = parsed_snapshot["state"]
            else:
                snapshot_state = parsed_snapshot
        else:
            tensions.append(
                DissonanceSignal(
                    signal_id="invalid_snapshot_format",
                    severity="high",
                    summary="Snapshot payload must be a JSON object",
                )
            )
            rho += 0.4
            parsed_snapshot = {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        parsed_snapshot = {}

    if payload.enforcement_bits.P_bad or bool(snapshot_state.get("is_bad_precedent", False)):
        tensions.append(
            DissonanceSignal(
                signal_id="bad_precedent",
                severity="high",
                summary="State transition references a bad precedent in history.",
            )
        )
        rho += STATE_WEIGHTS["bad_precedent"]

    if isinstance(payload.governance_version, str):
        is_rollback_context = payload.input_text.startswith("rollback:") or resolved_trigger != "none"
        if is_rollback_context and payload.governance_version != CURRENT_RUNTIME_GOVERNANCE_VERSION:
            rho += _record_configured_tension(
                "GOVERNANCE_VERSION_INCOMPATIBLE",
                trigger=resolved_trigger,
                tensions=tensions,
                suppressed=suppressed,
                summary_override=(
                    f"Target governance_version {payload.governance_version} is incompatible with "
                    f"current epoch {CURRENT_RUNTIME_GOVERNANCE_VERSION}"
                ),
            )

    if payload.twin_desynced:
        tensions.append(
            DissonanceSignal(
                signal_id="twin_desynced",
                severity="medium",
                summary="Digital twin is not synchronized with live state.",
            )
        )
        rho += STATE_WEIGHTS["twin_desynced"]

    if payload.stale_base_disabled:
        tensions.append(
            DissonanceSignal(
                signal_id="stale_base_disabled",
                severity="medium",
                summary="Strict stale-base enforcement is disabled.",
            )
        )
        rho += STATE_WEIGHTS["stale_base_disabled"]

    if payload.boundary_absent:
        tensions.append(
            DissonanceSignal(
                signal_id="boundary_absent",
                severity="medium",
                summary="Canonical boundary is absent for this transition.",
            )
        )
        rho += STATE_WEIGHTS["boundary_absent"]

    for immutable_path in ("policy/phase_mirror/", "contracts/system_invariants.yaml"):
        if immutable_path in payload.input_text.lower():
            tensions.append(
                DissonanceSignal(
                    signal_id="immutable_path_access",
                    severity="high",
                    summary=f"Attempted state transition on immutable path: {immutable_path}",
                )
            )
            rho += 0.7

    try:
        snapshot_dict = parsed_snapshot
        snapshot_id_in_data = snapshot_dict.get("snapshot_id") if isinstance(snapshot_dict, dict) else None
        if snapshot_id_in_data and snapshot_id_in_data != payload.snapshot.snapshot_id:
            suppressed.append(
                DissonanceSignal(
                    signal_id="snapshot_id_mismatch",
                    severity="medium",
                    summary="Snapshot ID in data does not match wrapper snapshot_id",
                )
            )
            rho += 0.05
        if isinstance(snapshot_dict, dict) and "state" not in snapshot_dict:
            suppressed.append(
                DissonanceSignal(
                    signal_id="missing_state_field",
                    severity="medium",
                    summary="Snapshot missing required 'state' field",
                )
            )
            rho += 0.1
        if isinstance(snapshot_dict, dict) and "captured_at" not in snapshot_dict:
            suppressed.append(
                DissonanceSignal(
                    signal_id="missing_timestamp",
                    severity="low",
                    summary="Snapshot missing 'captured_at' timestamp",
                )
            )
            rho += 0.02
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        tensions.append(
            DissonanceSignal(
                signal_id="snapshot_decode_error",
                severity="high",
                summary=f"Snapshot payload is not valid JSON: {str(e)}",
            )
        )
        rho += 0.6

    return (min(rho, 2.0), tensions, suppressed)


def _extract_prime_index(expression_body: str) -> int | None:
    lowered = expression_body.lower()
    for marker in ("prime_index=", "mod="):
        if marker not in lowered:
            continue
        tail = lowered.split(marker, 1)[1]
        digits = []
        for ch in tail:
            if ch.isdigit():
                digits.append(ch)
            elif digits:
                break
        if not digits:
            continue
        try:
            return int("".join(digits))
        except ValueError:
            continue
    return None


def _is_prime(value: int) -> bool:
    if value < 2:
        return False
    if value in (2, 3):
        return True
    if value % 2 == 0:
        return False
    i = 3
    while i * i <= value:
        if value % i == 0:
            return False
        i += 2
    return True


def _record_configured_tension(
    tension_id: str,
    *,
    trigger: str,
    tensions: list[DissonanceSignal],
    suppressed: list[DissonanceSignal],
    summary_override: str | None = None,
) -> float:
    tension_def = get_tension_definition(tension_id)
    summary = summary_override or tension_def.get("description", tension_id)
    severity = tension_def.get("severity", "high")
    signal = DissonanceSignal(
        signal_id=tension_id,
        severity=severity,
        summary=summary,
    )
    suppress_triggers = tension_def.get("suppress_on_triggers", []) or []
    if suppress_triggers and normalize_trigger(trigger) in suppress_triggers:
        suppressed.append(signal)
        return 0.0

    tensions.append(signal)
    return float(tension_def.get("score_weight", 0.0))


def _score_expression(
    input_text: str,
    output_expr: PIRTMExpr,
    rollback_trigger: str,
) -> tuple[float, list[DissonanceSignal], list[DissonanceSignal]]:
    """Score an expression payload using expression scorer."""

    tensions: list[DissonanceSignal] = []
    suppressed: list[DissonanceSignal] = []
    rho = 0.0

    decoded_output = output_expr.data.decode("utf-8", errors="ignore")
    expression_body = decoded_output.split("MAGIC", 1)[-1].strip("\x00 ")
    input_clean = input_text.strip()

    if not input_clean or not expression_body:
        tensions.append(
            DissonanceSignal(
                signal_id="empty_expression",
                severity="auto_fail",
                summary="Expression payload is empty or input expression is blank.",
            )
        )
        if not input_clean:
            tensions.append(
                DissonanceSignal(
                    signal_id="empty_expression_input",
                    severity="auto_fail",
                    summary="Input text is empty for expression evaluation",
                )
            )
        if not expression_body:
            tensions.append(
                DissonanceSignal(
                    signal_id="empty_expression_payload",
                    severity="auto_fail",
                    summary="Expression payload is empty",
                )
            )
        rho += EXPR_WEIGHTS["empty_expression"]
        return (min(rho, 2.0), tensions, suppressed)

    if input_clean == expression_body.strip():
        tensions.append(
            DissonanceSignal(
                signal_id="verbatim_echo",
                severity="medium",
                summary="Expression output matches input exactly.",
            )
        )
        tensions.append(
            DissonanceSignal(
                signal_id="untransformed_expression",
                severity="medium",
                summary="Expression output matches input exactly",
            )
        )
        rho += EXPR_WEIGHTS["verbatim_echo"]

    input_tokens = {tok for tok in input_clean.replace("(", " ").replace(")", " ").split() if tok}
    output_tokens = {tok for tok in expression_body.replace("(", " ").replace(")", " ").split() if tok}
    if input_tokens and output_tokens and input_tokens.isdisjoint(output_tokens):
        tensions.append(
            DissonanceSignal(
                signal_id="semantic_drift",
                severity="high",
                summary="Expression output is semantically disconnected from input tokens.",
            )
        )
        rho += EXPR_WEIGHTS["semantic_drift"]

    contractivity_markers = ("contractive", "contractivity", "epsilon", "op_norm_t")
    body_lower = expression_body.lower()
    if not any(marker in body_lower for marker in contractivity_markers):
        tensions.append(
            DissonanceSignal(
                signal_id="contractivity_assertion_absent",
                severity="high",
                summary="Expression lacks explicit contractivity markers (epsilon/op_norm_T).",
            )
        )
        rho += EXPR_WEIGHTS["contractivity_assertion_absent"]

    if "governance_version=" in body_lower:
        candidate = body_lower.split("governance_version=", 1)[1].split()[0].strip("\"' ,")
        if candidate and candidate != CURRENT_RUNTIME_GOVERNANCE_VERSION.lower():
            tensions.append(
                DissonanceSignal(
                    signal_id="governance_version_mismatch",
                    severity="medium",
                    summary=(
                        f"Expression governance_version {candidate} mismatches "
                        f"runtime {CURRENT_RUNTIME_GOVERNANCE_VERSION}."
                    ),
                )
            )
            rho += EXPR_WEIGHTS["governance_version_mismatch"]

    prime_index = _extract_prime_index(expression_body)
    if prime_index is not None and not _is_prime(prime_index):
        tensions.append(
            DissonanceSignal(
                signal_id="unauthorized_prime_index",
                severity="high",
                summary=f"prime_index={prime_index} is not prime; unauthorized index.",
            )
        )
        rho += EXPR_WEIGHTS["unauthorized_prime_index"]

    return (min(rho, 2.0), tensions, suppressed)


def _score_state_transition(
    input_text: str,
    output_expr: StateSnapshot,
    rollback_trigger: str,
) -> tuple[float, list[DissonanceSignal], list[DissonanceSignal]]:
    """Legacy state-transition scorer retained for compatibility."""

    tensions: list[DissonanceSignal] = []
    suppressed: list[DissonanceSignal] = []
    rho = 0.0
    resolved_trigger = normalize_trigger(rollback_trigger)

    if not input_text.strip():
        tensions.append(
            DissonanceSignal(
                signal_id="empty_transition_input",
                severity="high",
                summary="Input text is empty for state transition evaluation",
            )
        )
        rho += 0.3

    if not output_expr.data:
        tensions.append(
            DissonanceSignal(
                signal_id="empty_snapshot_payload",
                severity="high",
                summary="Snapshot payload is empty",
            )
        )
        rho += 0.3

    if not output_expr.snapshot_id:
        tensions.append(
            DissonanceSignal(
                signal_id="missing_snapshot_id",
                severity="high",
                summary="Snapshot ID is missing",
            )
        )
        rho += 0.3

    if resolved_trigger != "none":
        tensions.append(
            DissonanceSignal(
                signal_id="rollback_trigger_active",
                severity="high",
                summary=f"Rollback trigger detected: {resolved_trigger}. Emergency mode active; governance approval required.",
            )
        )
        rho += 0.5

    try:
        snapshot_dict = json.loads(output_expr.data.decode("utf-8"))
        if not isinstance(snapshot_dict, dict):
            tensions.append(
                DissonanceSignal(
                    signal_id="invalid_snapshot_format",
                    severity="high",
                    summary="Snapshot payload must be a JSON object",
                )
            )
            rho += 0.4
        else:
            enforcement_bits = snapshot_dict.get("enforcement_bits")
            if isinstance(enforcement_bits, dict) and any(value is False for value in enforcement_bits.values()):
                rho += _record_configured_tension(
                    "LEGITIMACY_PREDICATE_FAILED",
                    trigger=resolved_trigger,
                    tensions=tensions,
                    suppressed=suppressed,
                )

            governance_version = snapshot_dict.get("governance_version")
            is_rollback_context = input_text.startswith("rollback:") or resolved_trigger != "none"
            if is_rollback_context and isinstance(governance_version, str):
                if governance_version != CURRENT_RUNTIME_GOVERNANCE_VERSION:
                    rho += _record_configured_tension(
                        "GOVERNANCE_VERSION_INCOMPATIBLE",
                        trigger=resolved_trigger,
                        tensions=tensions,
                        suppressed=suppressed,
                        summary_override=(
                            f"Target governance_version {governance_version} is incompatible with "
                            f"current epoch {CURRENT_RUNTIME_GOVERNANCE_VERSION}"
                        ),
                    )

            snapshot_id_in_data = snapshot_dict.get("snapshot_id")
            if snapshot_id_in_data and snapshot_id_in_data != output_expr.snapshot_id:
                suppressed.append(
                    DissonanceSignal(
                        signal_id="snapshot_id_mismatch",
                        severity="medium",
                        summary="Snapshot ID in data does not match wrapper snapshot_id",
                    )
                )
                rho += 0.05

            if "state" not in snapshot_dict:
                suppressed.append(
                    DissonanceSignal(
                        signal_id="missing_state_field",
                        severity="medium",
                        summary="Snapshot missing required 'state' field",
                    )
                )
                rho += 0.1

            if "captured_at" not in snapshot_dict:
                suppressed.append(
                    DissonanceSignal(
                        signal_id="missing_timestamp",
                        severity="low",
                        summary="Snapshot missing 'captured_at' timestamp",
                    )
                )
                rho += 0.02

    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        tensions.append(
            DissonanceSignal(
                signal_id="snapshot_decode_error",
                severity="high",
                summary=f"Snapshot payload is not valid JSON: {str(e)}",
            )
        )
        rho += 0.6

    for immutable_path in ("policy/phase_mirror/", "contracts/system_invariants.yaml"):
        if immutable_path in input_text.lower():
            tensions.append(
                DissonanceSignal(
                    signal_id="immutable_path_access",
                    severity="high",
                    summary=f"Attempted state transition on immutable path: {immutable_path}",
                )
            )
            rho += 0.7

    return (min(rho, 2.0), tensions, suppressed)
