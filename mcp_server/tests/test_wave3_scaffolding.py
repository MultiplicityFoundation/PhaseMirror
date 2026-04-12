from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from daemon.scheduler import run_heartbeat_once
from daemon.watchdog import DaemonWatchdog
from digital_twin.twin import DigitalTwin
from ensemble.ensemble_manager import EnsembleManager
from rollback.rollback_manager import RollbackManager
from mcp_server.server import build_server


def test_registry_loader_exposes_wave3_tools():
    server = build_server(prefer_fastmcp=False)
    tool_names = {tool["name"] for tool in server.list_tools()}

    assert {
        "daemon_heartbeat",
        "daemon_epsilon_adjust",
        "epsilon_adjust",
        "health_check",
        "agent_dispatch",
    }.issubset(tool_names)


def _make_persistent_watchdog(tmp_path) -> DaemonWatchdog:
    live_state_path = tmp_path / "state" / "live_state.yaml"
    epoch_index_path = tmp_path / "state" / "epoch_index.yaml"
    snapshot_store = tmp_path / "digital_twin" / "snapshot_store"
    latest_snapshot_path = tmp_path / "digital_twin" / "latest_snapshot.yaml"
    heartbeat_path = tmp_path / "daemon" / "latest_heartbeat.yaml"
    epsilon_state_path = tmp_path / "state" / "epsilon_runtime.yaml"

    twin = DigitalTwin(
        live_state_path=live_state_path,
        epoch_index_path=epoch_index_path,
        snapshot_store=snapshot_store,
        latest_snapshot_path=latest_snapshot_path,
    )
    rollback_manager = RollbackManager(twin=twin)
    return DaemonWatchdog(
        twin=twin,
        rollback_manager=rollback_manager,
        heartbeat_path=heartbeat_path,
        epsilon_state_path=epsilon_state_path,
        persist_epsilon_state=True,
    )


def test_health_check_reports_wave3_indexes_present():
    server = build_server(prefer_fastmcp=False)
    report = server.call_tool("health_check")

    assert report["status"] in {"pass", "warn"}
    assert report["organ_indexes"]["state"]["exists"] is True
    assert "packages/witnesses/README.md" in report["missing_paths"] or report["organ_indexes"]["witnesses"]["exists"] is True


def test_daemon_heartbeat_tool_executes_watchdog_cycle():
    server = build_server(prefer_fastmcp=False)
    report = server.call_tool("daemon_heartbeat", label="wave3-smoke")

    assert report["status"] in {"baseline_established", "in_sync", "drift_detected"}
    assert "snapshot_id" in report


def test_daemon_epsilon_adjust_tool_persists_runtime_state(tmp_path, monkeypatch):
    import mcp_server.tools.tool_daemon_epsilon_adjust as epsilon_tool_module

    monkeypatch.setattr(
        epsilon_tool_module,
        "DaemonWatchdog",
        lambda **_: _make_persistent_watchdog(tmp_path),
    )

    server = build_server(prefer_fastmcp=False)
    first = server.call_tool("daemon_epsilon_adjust", delta="0.01", reason="wave3-adjust")
    second = server.call_tool("daemon_epsilon_adjust", delta="0.01", reason="wave3-adjust")

    assert first["status"] == "accepted"
    assert second["status"] == "rejected"
    assert second["reason"] == "rate_limit_exceeded"


def test_epsilon_adjust_tool_alias_uses_fast_path(tmp_path, monkeypatch):
    import mcp_server.tools.tool_epsilon_adjust as epsilon_tool_module

    monkeypatch.setattr(
        epsilon_tool_module,
        "DaemonWatchdog",
        lambda **_: _make_persistent_watchdog(tmp_path),
    )

    server = build_server(prefer_fastmcp=False)
    first = server.call_tool("epsilon_adjust", delta_epsilon="0.01", reason="watchdog")
    second = server.call_tool("epsilon_adjust", delta_epsilon="0.01", reason="watchdog")

    assert first["status"] == "accepted"
    assert second["status"] == "rejected"
    assert second["reason"] == "rate_limit_exceeded"


def test_agent_dispatch_routes_through_ensemble_manager():
    server = build_server(prefer_fastmcp=False)
    report = server.call_tool(
        "agent_dispatch",
        agent_id="Phase Mirror Coding Agent — System Instructions",
        task="run daemon heartbeat label=wave3-dispatch",
    )

    assert report["status"] == "dispatched"
    assert report["wrapper"] == "daemon_heartbeat"
    assert report["ensemble_route"]["member_id"] == "tooling"
    assert report["ensemble_route"]["role"] == "control_plane"
    assert report["ensemble_route"]["status"] == "handoff_completed"
    assert report["wrapper_result"]["status"] in {"baseline_established", "in_sync", "drift_detected"}


def test_agent_dispatch_routes_epsilon_adjustment(tmp_path, monkeypatch):
    import mcp_server.tools.tool_daemon_epsilon_adjust as epsilon_tool_module

    monkeypatch.setattr(
        epsilon_tool_module,
        "DaemonWatchdog",
        lambda **_: _make_persistent_watchdog(tmp_path),
    )

    server = build_server(prefer_fastmcp=False)
    report = server.call_tool(
        "agent_dispatch",
        agent_id="Phase Mirror Coding Agent — System Instructions",
        task="adjust epsilon delta=0.01 reason=runtime stabilization",
    )

    assert report["status"] == "dispatched"
    assert report["wrapper"] == "daemon_epsilon_adjust"
    assert report["wrapper_result"]["status"] == "accepted"


def test_agent_dispatch_executes_external_actuation_surface():
    server = build_server(prefer_fastmcp=False)
    report = server.call_tool(
        "agent_dispatch",
        agent_id="Phase Mirror Coding Agent — System Instructions",
        task="run actuation dispatch for remote surface",
    )

    assert report["status"] == "dispatched"
    assert report["wrapper"] == "actuation_dispatch"
    assert report["ensemble_route"]["member_id"] == "ace"
    assert report["ensemble_route"]["status"] in {"handoff_completed", "handoff_unavailable"}
    if report["ensemble_route"]["status"] == "handoff_completed":
        assert report["wrapper_result"]["status"] == "ok"
        assert report["wrapper_result"]["artifact"]["certificate"]["certified"] is True
    else:
        assert report["wrapper_result"] is None


def test_agent_dispatch_executes_external_contractivity_probe():
    server = build_server(prefer_fastmcp=False)
    report = server.call_tool(
        "agent_dispatch",
        agent_id="Phase Mirror Coding Agent — System Instructions",
        task="run contractivity check for remote surface",
    )

    assert report["status"] == "dispatched"
    assert report["wrapper"] == "contractivity_check"
    assert report["ensemble_route"]["member_id"] == "pirtm"
    assert report["ensemble_route"]["status"] in {"handoff_completed", "handoff_unavailable"}
    if report["ensemble_route"]["status"] == "handoff_completed":
        assert report["wrapper_result"]["status"] == "ok"
        assert report["wrapper_result"]["mode"] == "probe"
    else:
        assert report["wrapper_result"] is None


def test_multiplicity_tools_tables_and_execution(tmp_path, monkeypatch):
    server = build_server(skip_governance_preflight=True, prefer_fastmcp=False)

    tool_names = {tool["name"] for tool in server.list_tools()}
    assert {"multiplicity_create_article", "multiplicity_validate_article", "multiplicity_execute_article"}.issubset(tool_names)

    article_path = tmp_path / "multiplicity_article.md"
    content = (
        "metadata:\n"
        "  id: test-multiplicity-tool\n"
        "  title: Test Multiplicity Tool\n"
        "  status: draft\n"
        "\n"
        "## Logic\n"
        "hello"
    )

    create_report = server.call_tool("multiplicity_create_article", article_path=str(article_path), content=content)
    assert create_report["status"] == "created"
    assert article_path.exists()

    import mcp_server.tools.tool_multiplicity_validate_article as validate_module

    class FakeResult:
        returncode = 0
        stdout = "OK"
        stderr = ""

    monkeypatch.setattr(validate_module.subprocess, "run", lambda *args, **kwargs: FakeResult())

    validate_report = server.call_tool("multiplicity_validate_article", article_path=str(article_path))
    assert validate_report["status"] == "valid"
    assert validate_report["path"] == str(article_path)

    execute_report = server.call_tool("multiplicity_execute_article", article_path=str(article_path), context_json='{}')
    assert execute_report["status"] == "executed"
    assert execute_report["path"] == str(article_path)

    dispatch_report = server.call_tool(
        "agent_dispatch",
        agent_id="Phase Mirror Coding Agent — System Instructions",
        task=f"multiplicity execute {article_path}",
        wrapper_name="multiplicity_execute_article",
    )

    assert dispatch_report["status"] == "dispatched"
    assert dispatch_report["wrapper"] == "multiplicity_execute_article"
    assert dispatch_report["dispatch_trace"] == "agent_dispatch -> multiplicity_execute_article"
    assert dispatch_report["wrapper_result"]["status"] == "executed"


def test_scheduler_entrypoint_runs_once_without_extra_wiring(monkeypatch):
    monkeypatch.setattr("daemon.scheduler._immutability_verified", True)
    report = run_heartbeat_once(label="wave3-cli")

    assert report["status"] in {"baseline_established", "in_sync", "drift_detected"}


def test_ensemble_manager_routes_governance_tasks():
    manager = EnsembleManager()
    report = manager.route_task(
        role="governance_gate",
        capability="policy_review",
        task="phase mirror review",
    )

    assert report["member_id"] == "phase_mirror"


def test_agent_dispatch_calls_multiplicity_execute_article(tmp_path, monkeypatch):
    server = build_server(skip_governance_preflight=True, prefer_fastmcp=False)

    article_path = tmp_path / "multiplicity_article.md"
    article_path.write_text(
        "metadata:\n  id: policy-callback\n  title: Policy Callback\n  status: draft\n\n## Logic\nhello", encoding='utf-8'
    )

    # stub out the underlying execute action to avoid requiring full node-based engine
    import mcp_server.tools.tool_multiplicity_execute_article as execute_module

    def fake_execute_article(article_path: str, context_json: str | None = None) -> dict:
        return {"status": "executed", "path": article_path, "context": context_json}

    monkeypatch.setattr(execute_module, "multiplicity_execute_article", fake_execute_article)

    report = server.call_tool(
        "agent_dispatch",
        agent_id="Phase Mirror Coding Agent — System Instructions",
        task=f"multiplicity execute {article_path}",
        wrapper_name="multiplicity_execute_article",
    )

    assert report["status"] == "dispatched"
    assert report["wrapper"] == "multiplicity_execute_article"
    assert report["wrapper_result"]["status"] == "executed"
    assert report["wrapper_result"]["path"] == str(article_path)
