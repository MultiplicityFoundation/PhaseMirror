from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp_server.server import build_server
from rollback.checkpoint import CheckpointManager
from rollback.normative_record import NormativeRecordWriter


def test_registry_loader_exposes_wave1_tools():
    server = build_server(prefer_fastmcp=False)
    tool_names = {tool["name"] for tool in server.list_tools()}

    assert {
        "health_check",
        "manifest_status",
        "phase_mirror",
        "agent_dispatch",
        "checkpoint_inventory",
        "checkpoint_prune",
        "checkpoint_write",
        "ledger_query",
        "rollback_execute",
        "rollback_status",
        "normative_record_query",
    }.issubset(tool_names)


def test_health_check_tool_reports_wave1_roots_present():
    server = build_server(prefer_fastmcp=False)
    report = server.call_tool("health_check")

    assert report["status"] in {"pass", "warn"}
    assert report["roots"]["mcp_server"]["exists"] is True
    assert report["roots"]["digital_twin"]["exists"] is True
    assert report["runtime_surfaces"]["rollback_checkpoint"]["exists"] is True


def test_manifest_status_tool_reads_manifest_summary():
    server = build_server(prefer_fastmcp=False)
    report = server.call_tool("manifest_status")

    assert report["manifest_id"] == "tooling-mcp-manifest"
    assert report["workspace"]["repository"] == "MultiplicityFoundation/Tooling"
    assert any(ring["name"] == "policy" for ring in report["rings"])


def test_agent_dispatch_invokes_explicit_wrapper():
    server = build_server(prefer_fastmcp=False)
    report = server.call_tool(
        "agent_dispatch",
        agent_id="Phase Mirror Coding Agent — System Instructions",
        task="report manifest ownership",
    )

    assert report["status"] == "dispatched"
    assert report["wrapper"] == "manifest_status"
    assert report["wrapper_result"]["manifest_id"] == "tooling-mcp-manifest"


def test_checkpoint_write_tool_returns_metadata(tmp_path, monkeypatch):
    checkpoint_manager = CheckpointManager(
        live_state_path=tmp_path / "state" / "live_state.yaml",
        checkpoint_store=tmp_path / "rollback" / "checkpoints",
        latest_checkpoint_path=tmp_path / "rollback" / "latest_checkpoint.yaml",
        retention_policy_path=tmp_path / "rollback" / "retention_policy.yaml",
        normative_record_writer=NormativeRecordWriter(
            records_path=tmp_path / "rollback" / "normative_records.yaml",
            latest_record_path=tmp_path / "rollback" / "latest_normative_record.yaml",
        ),
    )
    checkpoint_manager.live_state_path.parent.mkdir(parents=True, exist_ok=True)
    from mcp_server._yaml import dump_yaml_file

    dump_yaml_file(
        checkpoint_manager.live_state_path,
        {
            "governance_version": "phase-mirror-stub-v0",
            "system": "tooling-pmd",
            "status": "bootstrap",
            "updated_at": None,
        },
    )
    dump_yaml_file(
        checkpoint_manager.retention_policy_path,
        {
            "version": 2,
            "max_checkpoints": 5,
            "max_age_seconds": 2592000,
            "tiers": {"hot": {"count": 2, "ttl_seconds": 86400}, "warm": {"count": 5, "ttl_seconds": 604800}},
        },
    )

    import mcp_server.tools.tool_checkpoint_write as checkpoint_write_module

    monkeypatch.setattr(checkpoint_write_module, "CheckpointManager", lambda: checkpoint_manager)

    server = build_server(prefer_fastmcp=False)
    report = server.call_tool(
        "checkpoint_write",
        label="smoke",
        governance_version="gov-v1",
        ledger_reference="ledger://smoke/1",
        metadata_json='{"source":"mcp-smoke"}',
    )

    assert report["metadata"]["governance_version"] == "gov-v1"
    assert report["metadata"]["ledger_reference"] == "ledger://smoke/1"
    assert report["metadata"]["source"] == "mcp-smoke"
    assert report["metadata"]["reconciliation_status"] == "authoritative"
    assert report["metadata"]["normative_record_id"].startswith("normrec-")


def test_checkpoint_inventory_tool_lists_checkpoints(tmp_path, monkeypatch):
    checkpoint_manager = CheckpointManager(
        live_state_path=tmp_path / "state" / "live_state.yaml",
        checkpoint_store=tmp_path / "rollback" / "checkpoints",
        latest_checkpoint_path=tmp_path / "rollback" / "latest_checkpoint.yaml",
        retention_policy_path=tmp_path / "rollback" / "retention_policy.yaml",
        normative_record_writer=NormativeRecordWriter(
            records_path=tmp_path / "rollback" / "normative_records.yaml",
            latest_record_path=tmp_path / "rollback" / "latest_normative_record.yaml",
        ),
    )
    checkpoint_manager.live_state_path.parent.mkdir(parents=True, exist_ok=True)
    from mcp_server._yaml import dump_yaml_file

    dump_yaml_file(
        checkpoint_manager.live_state_path,
        {
            "governance_version": "phase-mirror-stub-v0",
            "system": "tooling-pmd",
            "status": "bootstrap",
            "updated_at": None,
        },
    )
    dump_yaml_file(
        checkpoint_manager.retention_policy_path,
        {
            "version": 2,
            "max_checkpoints": 5,
            "max_age_seconds": 2592000,
            "tiers": {"hot": {"count": 1, "ttl_seconds": 86400}, "warm": {"count": 5, "ttl_seconds": 604800}},
        },
    )
    checkpoint_manager.write_checkpoint("inventory", ledger_reference="ledger://inventory/1")

    import mcp_server.tools.tool_checkpoint_inventory as checkpoint_inventory_module

    monkeypatch.setattr(checkpoint_inventory_module, "CheckpointManager", lambda: checkpoint_manager)

    server = build_server(prefer_fastmcp=False)
    report = server.call_tool("checkpoint_inventory")

    assert report["checkpoint_count"] == 1
    assert report["checkpoints"][0]["retention_tier"] == "hot"
    assert report["checkpoints"][0]["metadata"]["ledger_reference"] == "ledger://inventory/1"


def test_checkpoint_prune_tool_prunes_by_policy(tmp_path, monkeypatch):
    checkpoint_manager = CheckpointManager(
        live_state_path=tmp_path / "state" / "live_state.yaml",
        checkpoint_store=tmp_path / "rollback" / "checkpoints",
        latest_checkpoint_path=tmp_path / "rollback" / "latest_checkpoint.yaml",
        retention_policy_path=tmp_path / "rollback" / "retention_policy.yaml",
        normative_record_writer=NormativeRecordWriter(
            records_path=tmp_path / "rollback" / "normative_records.yaml",
            latest_record_path=tmp_path / "rollback" / "latest_normative_record.yaml",
        ),
    )
    checkpoint_manager.live_state_path.parent.mkdir(parents=True, exist_ok=True)
    from mcp_server._yaml import dump_yaml_file

    dump_yaml_file(
        checkpoint_manager.live_state_path,
        {
            "governance_version": "phase-mirror-stub-v0",
            "system": "tooling-pmd",
            "status": "bootstrap",
            "updated_at": None,
        },
    )
    dump_yaml_file(
        checkpoint_manager.retention_policy_path,
        {
            "version": 2,
            "max_checkpoints": 1,
            "max_age_seconds": 2592000,
            "tiers": {"hot": {"count": 1, "ttl_seconds": 86400}, "warm": {"count": 2, "ttl_seconds": 604800}},
        },
    )
    checkpoint_manager.write_checkpoint("one")
    checkpoint_manager.write_checkpoint("two")

    import mcp_server.tools.tool_checkpoint_prune as checkpoint_prune_module

    monkeypatch.setattr(checkpoint_prune_module, "CheckpointManager", lambda: checkpoint_manager)

    server = build_server(prefer_fastmcp=False)
    report = server.call_tool("checkpoint_prune")

    assert report["status"] == "pruned"
    assert report["kept_count"] == 1
    assert len(report["deleted_checkpoint_ids"]) == 1


def test_checkpoint_prune_tool_supports_dry_run(tmp_path, monkeypatch):
    checkpoint_manager = CheckpointManager(
        live_state_path=tmp_path / "state" / "live_state.yaml",
        checkpoint_store=tmp_path / "rollback" / "checkpoints",
        latest_checkpoint_path=tmp_path / "rollback" / "latest_checkpoint.yaml",
        retention_policy_path=tmp_path / "rollback" / "retention_policy.yaml",
    )
    checkpoint_manager.live_state_path.parent.mkdir(parents=True, exist_ok=True)
    from mcp_server._yaml import dump_yaml_file

    dump_yaml_file(
        checkpoint_manager.live_state_path,
        {
            "governance_version": "phase-mirror-stub-v0",
            "system": "tooling-pmd",
            "status": "bootstrap",
            "updated_at": None,
        },
    )
    dump_yaml_file(
        checkpoint_manager.retention_policy_path,
        {
            "version": 2,
            "max_checkpoints": 1,
            "max_age_seconds": 2592000,
            "tiers": {"hot": {"count": 1, "ttl_seconds": 86400}, "warm": {"count": 2, "ttl_seconds": 604800}},
        },
    )
    checkpoint_manager.write_checkpoint("one")
    checkpoint_manager.write_checkpoint("two")

    import mcp_server.tools.tool_checkpoint_prune as checkpoint_prune_module

    monkeypatch.setattr(checkpoint_prune_module, "CheckpointManager", lambda: checkpoint_manager)

    server = build_server(prefer_fastmcp=False)
    report = server.call_tool("checkpoint_prune", dry_run="true")

    assert report["status"] == "dry_run"
    assert report["deleted_checkpoint_ids"] == []
    assert len(report["would_delete_checkpoint_ids"]) == 1


def test_normative_record_query_tool_lists_records(tmp_path, monkeypatch):
    checkpoint_manager = CheckpointManager(
        live_state_path=tmp_path / "state" / "live_state.yaml",
        checkpoint_store=tmp_path / "rollback" / "checkpoints",
        latest_checkpoint_path=tmp_path / "rollback" / "latest_checkpoint.yaml",
        retention_policy_path=tmp_path / "rollback" / "retention_policy.yaml",
        normative_record_writer=NormativeRecordWriter(
            records_path=tmp_path / "rollback" / "normative_records.yaml",
            latest_record_path=tmp_path / "rollback" / "latest_normative_record.yaml",
        ),
    )
    checkpoint_manager.live_state_path.parent.mkdir(parents=True, exist_ok=True)
    from mcp_server._yaml import dump_yaml_file

    dump_yaml_file(
        checkpoint_manager.live_state_path,
        {
            "governance_version": "phase-mirror-stub-v0",
            "system": "tooling-pmd",
            "status": "bootstrap",
            "updated_at": None,
        },
    )
    dump_yaml_file(
        checkpoint_manager.retention_policy_path,
        {
            "version": 2,
            "max_checkpoints": 5,
            "max_age_seconds": 2592000,
            "tiers": {"hot": {"count": 2, "ttl_seconds": 86400}, "warm": {"count": 5, "ttl_seconds": 604800}},
        },
    )
    checkpoint_manager.write_checkpoint("inventory", ledger_reference="ledger://inventory/1")

    import mcp_server.tools.tool_normative_record_query as normative_record_query_module

    monkeypatch.setattr(
        normative_record_query_module,
        "NormativeRecordWriter",
        lambda: checkpoint_manager.normative_record_writer,
    )

    server = build_server(prefer_fastmcp=False)
    report = server.call_tool("normative_record_query")

    assert report["record_count"] == 1
    assert report["records"][0]["ledger_reference"] == "ledger://inventory/1"


def test_rollback_status_tool_reports_triggered_sample():
    server = build_server(prefer_fastmcp=False)
    report = server.call_tool("rollback_status", L_Phi="1.05")

    assert report["status"] == "triggered"
    assert report["trigger"] == "L_Phi_breach"
    assert "kill_switch" in report["triggers"]


def test_rollback_execute_tool_preserves_kill_switch_fallback():
    server = build_server(prefer_fastmcp=False)
    report = server.call_tool(
        "rollback_execute",
        operator_halt="true",
        checkpoint_id="missing-checkpoint",
    )

    assert report["status"] == "kill_switch_engaged"
    assert report["trigger"] == "kill_switch"
    assert report["kill_switch"]["status"] == "engaged"


def test_lever_branch_tools_are_registered():
    server = build_server(prefer_fastmcp=False)
    tool_names = {tool["name"] for tool in server.list_tools()}

    assert {"surface_levers", "create_lever_branch", "certify_lever_convergence"}.issubset(tool_names)


def test_surface_levers_returns_legacy_manifest():
    server = build_server(prefer_fastmcp=False)
    report = server.call_tool("surface_levers")

    assert report["status"] == "ok"
    assert report["lever_manifest"]["policy"] == "five-branch-reorganization"


def test_create_lever_branch_respects_max_concurrency():
    server = build_server(prefer_fastmcp=False)
    report = server.call_tool(
        "create_lever_branch",
        branch_name="fix/known-issues-001",
        existing_branch_count=3,
        max_concurrent=3,
    )

    assert report["status"] == "blocked"
    assert report["reason"] == "max_concurrent_exceeded"


def test_certify_lever_convergence_fails_on_high_wac():
    server = build_server(prefer_fastmcp=False)
    report = server.call_tool("certify_lever_convergence", branch_name="test/integration", wac=0.92, wac_threshold=0.7)

    assert report["certified"] is False
    assert report["wac"] == 0.92


def test_surface_levers_persists_state_manifest():
    server = build_server(prefer_fastmcp=False)
    report = server.call_tool("surface_levers")

    assert report["status"] == "ok"
    assert "lever_manifest" in report
    assert report["lever_manifest"]["policy"] == "five-branch-reorganization"


def test_deploy_authorization_returns_go_or_no_go():
    server = build_server(prefer_fastmcp=False)
    report = server.call_tool("deploy_authorization")

    assert report["status"] in {"go", "no-go"}
    assert "blockers" in report


def test_remove_lever_branch_updates_manifest():
    server = build_server(prefer_fastmcp=False)

    # create a branch to remove
    create_report = server.call_tool(
        "create_lever_branch",
        branch_name="fix/test-branch",
        existing_branch_count=0,
        max_concurrent=3,
    )
    assert create_report["status"] == "approved"

    remove_report = server.call_tool("remove_lever_branch", branch_name="fix/test-branch")
    assert remove_report["status"] == "removed"
    assert "fix/test-branch" not in remove_report["active_branches"]

