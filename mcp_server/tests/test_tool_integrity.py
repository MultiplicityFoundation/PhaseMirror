from __future__ import annotations

import pytest
from pathlib import Path

from contracts.shared.merkle_root import compute_governance_root
from governance.ledger import LedgerStore, create_governance_root_commit
from mcp_server.middleware.auth import (
    ToolIntegrityVerifier,
    ToolIntegrityViolation,
    verify_tool_before_dispatch,
)
from mcp_server.server import ToolSpec, build_server


def _write_invariants(tmp_path: Path, tool_path: Path) -> Path:
    invariants_path = tmp_path / "contracts" / "system_invariants.yaml"
    invariants_path.parent.mkdir(parents=True, exist_ok=True)
    invariants_path.write_text(
        "immutable_files:\n"
        "  - contracts/shared/constants.py\n"
        "governance_critical_tools:\n"
        f"  - {tool_path.relative_to(tmp_path).as_posix()}\n"
    )
    constants_path = tmp_path / "contracts" / "shared" / "constants.py"
    constants_path.parent.mkdir(parents=True, exist_ok=True)
    constants_path.write_text("GOVERNANCE_MERKLE_ROOT_TX_ID = 1\n")
    return invariants_path


def _seed_ledger(tmp_path: Path, invariants_path: Path, tool_path: Path) -> LedgerStore:
    ledger = LedgerStore(storage_path=tmp_path / "governance" / "ledger.json")
    immutable_files = [tmp_path / "contracts" / "shared" / "constants.py", invariants_path]
    governance_critical_tools = [tool_path]
    commit = create_governance_root_commit(
        merkle_root=compute_governance_root(immutable_files, governance_critical_tools),
        immutable_files=immutable_files,
        governance_critical_tools=governance_critical_tools,
        signed_by="test",
    )
    tx_id = ledger.create_entry(commit)
    assert tx_id == 1
    return ledger


def test_verifier_accepts_matching_governance_critical_tool(tmp_path):
    tool_path = tmp_path / "mcp_server" / "tools" / "tool_phase_mirror.py"
    tool_path.parent.mkdir(parents=True, exist_ok=True)
    tool_path.write_text("def phase_mirror():\n    return {'status': 'pass'}\n")
    invariants_path = _write_invariants(tmp_path, tool_path)
    ledger = _seed_ledger(tmp_path, invariants_path, tool_path)
    verifier = ToolIntegrityVerifier(
        ledger=ledger,
        invariants_path=invariants_path,
        workspace_root=tmp_path,
    )

    report = verify_tool_before_dispatch(
        verifier=verifier,
        tool_name="phase_mirror",
        tool_file="mcp_server/tools/tool_phase_mirror.py",
        verify_integrity=True,
    )

    assert report.status == "verified"
    assert report.actual_hash == report.expected_hash
    assert len(verifier.audit_ledger.entries) == 1


def test_verifier_engages_kill_switch_on_hash_mismatch(tmp_path):
    tool_path = tmp_path / "mcp_server" / "tools" / "tool_phase_mirror.py"
    tool_path.parent.mkdir(parents=True, exist_ok=True)
    tool_path.write_text("def phase_mirror():\n    return {'status': 'pass'}\n")
    invariants_path = _write_invariants(tmp_path, tool_path)
    ledger = _seed_ledger(tmp_path, invariants_path, tool_path)
    verifier = ToolIntegrityVerifier(
        ledger=ledger,
        invariants_path=invariants_path,
        workspace_root=tmp_path,
    )
    tool_path.write_text("def phase_mirror():\n    return {'status': 'bypassed'}\n")

    with pytest.raises(ToolIntegrityViolation) as exc_info:
        verify_tool_before_dispatch(
            verifier=verifier,
            tool_name="phase_mirror",
            tool_file="mcp_server/tools/tool_phase_mirror.py",
            verify_integrity=True,
        )

    assert exc_info.value.report.reason == "hash_mismatch"
    assert exc_info.value.kill_switch_result["status"] == "engaged"
    assert exc_info.value.kill_switch_result["trigger"] == "tool_integrity_failure:phase_mirror"


class StubVerifier:
    def __init__(self):
        self.calls: list[tuple[str, str, bool]] = []

    def verify(self, *, tool_name: str, tool_file: str, verify_integrity: bool):
        self.calls.append((tool_name, tool_file, verify_integrity))
        if verify_integrity:
            raise ToolIntegrityViolation(
                report=type(
                    "Report",
                    (),
                    {
                        "tool_name": tool_name,
                        "reason": "hash_mismatch",
                    },
                )(),
                kill_switch_result={"status": "engaged", "trigger": f"tool_integrity_failure:{tool_name}"},
            )
        return None


def test_server_dispatch_invokes_integrity_verifier_for_critical_tool():
    verifier = StubVerifier()
    server = build_server(prefer_fastmcp=False, integrity_verifier=verifier)

    with pytest.raises(ToolIntegrityViolation):
        server.call_tool("phase_mirror", input_text="x", output_expr="y")

    assert verifier.calls == [
        ("phase_mirror", "mcp_server/tools/tool_phase_mirror.py", True)
    ]


def test_server_dispatch_skips_verifier_for_noncritical_tool():
    verifier = StubVerifier()
    server = build_server(prefer_fastmcp=False, integrity_verifier=verifier)
    report = server.call_tool("health_check")

    assert report["status"] in {"pass", "warn"}
    assert verifier.calls == [
        ("health_check", "mcp_server/tools/tool_health_check.py", False)
    ]


def test_registry_marks_governance_critical_tools_for_verification():
    server = build_server(prefer_fastmcp=False)
    phase_mirror_spec = next(tool for tool in server.list_tools() if tool["name"] == "phase_mirror")
    health_check_spec = next(tool for tool in server.list_tools() if tool["name"] == "health_check")

    assert phase_mirror_spec["verify_integrity"] is True
    assert phase_mirror_spec["critical"] is True
    assert health_check_spec["verify_integrity"] is False