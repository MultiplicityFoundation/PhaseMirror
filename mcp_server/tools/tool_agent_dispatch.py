"""Wave 1 AGENTS.md dispatch tool."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ensemble.ensemble_manager import EnsembleManager
from .tool_daemon_epsilon_adjust import daemon_epsilon_adjust
from .tool_daemon_heartbeat import daemon_heartbeat
from .tool_health_check import health_check
from .tool_ledger_query import ledger_query
from .tool_manifest_status import manifest_status
from .tool_phase_mirror import phase_mirror
from .tool_multiplicity_execute_article import multiplicity_execute_article


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_PATH = REPO_ROOT / "AGENTS.md"
HEADING_PATTERN = re.compile(r"^##\s+(?P<name>.+?)\s*$")


def agent_dispatch(agent_id: str, task: str, wrapper_name: str | None = None) -> dict[str, Any]:
    """Dispatch an AGENTS.md-declared agent through an explicit MCP tool wrapper."""
    available_agents = _discover_agents()
    if agent_id not in available_agents:
        return {
            "status": "unknown_agent",
            "agent_id": agent_id,
            "task": task,
            "available_agents": available_agents,
            "wrapper": None,
            "recommended_action": "Register the agent in AGENTS.md before binding dispatch.",
        }

    selected_wrapper_name = wrapper_name or _select_wrapper(task)
    wrapper_spec = _WRAPPER_REGISTRY.get(selected_wrapper_name)
    if wrapper_spec is None:
        return {
            "status": "unknown_wrapper",
            "agent_id": agent_id,
            "task": task,
            "available_agents": available_agents,
            "available_wrappers": sorted(_WRAPPER_REGISTRY),
            "wrapper": selected_wrapper_name,
        }

    ensemble_manager = EnsembleManager()
    ensemble_route = ensemble_manager.handoff_task(
        role=wrapper_spec["role"],
        capability=wrapper_spec["capability"],
        task=task,
        local_handler=wrapper_spec.get("callable"),
        external_handler=wrapper_spec.get("external_callable"),
        local_wrapper=selected_wrapper_name,
    )
    handoff = ensemble_route.get("handoff", {})
    wrapper_result = handoff.get("result")

    return {
        "status": "dispatched",
        "agent_id": agent_id,
        "task": task,
        "wrapper": selected_wrapper_name,
        "dispatch_surface": "explicit_mcp_tool_wrapper",
        "dispatch_trace": f"agent_dispatch -> {selected_wrapper_name}",
        "constraints_source": str(AGENTS_PATH.relative_to(REPO_ROOT)),
        "ensemble_route": ensemble_route,
        "available_agents": available_agents,
        "available_wrappers": sorted(_WRAPPER_REGISTRY),
        "wrapper_result": wrapper_result,
    }


def _discover_agents() -> list[str]:
    agents: list[str] = []
    for line in AGENTS_PATH.read_text(encoding="utf-8").splitlines():
        match = HEADING_PATTERN.match(line.strip())
        if match:
            agents.append(match.group("name"))
    return agents


def _select_wrapper(task: str) -> str:
    normalized_task = task.lower()
    if "epsilon" in normalized_task and any(
        token in normalized_task for token in ("adjust", "increase", "decrease", "raise", "lower")
    ):
        return "daemon_epsilon_adjust"
    if any(token in normalized_task for token in ("contractivity", "dialect verification", "dialect verify")):
        return "contractivity_check"
    if any(token in normalized_task for token in ("canary", "rollout")):
        return "canary_rollout"
    if "multiplicity" in normalized_task and "execute" in normalized_task:
        return "multiplicity_execute_article"
    if any(token in normalized_task for token in ("actuation", "execution handoff", "execute")):
        return "actuation_dispatch"
    if "heartbeat" in normalized_task:
        return "daemon_heartbeat"
    if any(token in normalized_task for token in ("health", "roots")):
        return "health_check"
    if any(token in normalized_task for token in ("manifest", "ring", "layer", "ownership")):
        return "manifest_status"
    if any(token in normalized_task for token in ("ledger", "surplus", "element")):
        return "ledger_query"
    return "phase_mirror"


def _wrap_health_check(task: str) -> dict[str, Any]:
    return health_check()


def _wrap_daemon_heartbeat(task: str) -> dict[str, Any]:
    label_match = re.search(r"label[:=]\s*([\w-]+)", task, flags=re.IGNORECASE)
    return daemon_heartbeat(label=label_match.group(1) if label_match else None)


def _wrap_daemon_epsilon_adjust(task: str) -> dict[str, Any]:
    delta_match = re.search(r"delta[:=]\s*([-+]?\d*\.?\d+)", task, flags=re.IGNORECASE)
    if delta_match is None:
        delta_match = re.search(r"epsilon[^\d+-]*([-+]?\d*\.?\d+)", task, flags=re.IGNORECASE)
    reason_match = re.search(r"reason[:=]\s*([^,]+)$", task, flags=re.IGNORECASE)
    if delta_match is None:
        return {
            "status": "error",
            "reason": "missing_delta",
            "task": task,
        }
    return daemon_epsilon_adjust(
        delta=delta_match.group(1),
        reason=reason_match.group(1).strip() if reason_match else "agent_dispatch_runtime_adjustment",
    )


def _wrap_manifest_status(task: str) -> dict[str, Any]:
    return manifest_status()


def _wrap_multiplicity_execute_article(task: str) -> dict[str, Any]:
    from .tool_multiplicity_execute_article import multiplicity_execute_article

    article_path = _extract_file_argument(task, ('.md',))
    if not article_path:
        return {'status': 'error', 'reason': 'missing_article_path', 'task': task}

    return multiplicity_execute_article(article_path=article_path, context_json='{}')


def _wrap_phase_mirror(task: str) -> dict[str, Any]:
    return phase_mirror(
        input_text=task,
        output_expr=f"dispatch:{task}",
        context_json='{"source": "agent_dispatch"}',
    )


def _wrap_ledger_query(task: str) -> dict[str, Any]:
    element_id_match = re.search(r"\b(\d+)\b", task)
    if element_id_match:
        return ledger_query(element_id=element_id_match.group(1))
    return ledger_query()


def _run_external_command(
    *,
    repo_root: Path,
    args: list[str],
    pythonpath: list[Path] | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    env = dict(os.environ)
    pythonpath_entries = [str(path) for path in pythonpath or []]
    if pythonpath_entries:
        existing_pythonpath = env.get("PYTHONPATH")
        if existing_pythonpath:
            pythonpath_entries.append(existing_pythonpath)
        env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)

    completed = subprocess.run(
        args,
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    return {
        "command": args,
        "cwd": str(repo_root),
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _extract_file_argument(task: str, suffixes: tuple[str, ...]) -> str | None:
    for token in re.findall(r"[^\s]+", task):
        cleaned = token.strip('"\' ,')
        if cleaned.endswith(suffixes):
            return cleaned
    return None


def _wrap_external_contractivity_check(task: str, repo_root: Path) -> dict[str, Any]:
    target = _extract_file_argument(task, (".mlir", ".pirtm.bc", ".bc"))
    loader_script = "\n".join(
        [
            "import importlib.util",
            "import sys",
            "from pathlib import Path",
            "repo_root = Path(sys.argv[1])",
            "package_root = repo_root if (repo_root / '__init__.py').exists() else repo_root / 'pirtm'",
            "workspace_root = package_root.parent",
            "spec = importlib.util.spec_from_file_location(",
            '    "pirtm", package_root / "__init__.py", submodule_search_locations=[str(package_root)]',
            ")",
            "if spec is None or spec.loader is None:",
            '    raise SystemExit("Unable to load PIRTM package alias")',
            "module = importlib.util.module_from_spec(spec)",
            'sys.modules["pirtm"] = module',
            "spec.loader.exec_module(module)",
            "if len(sys.argv) > 2:",
            "    target = Path(sys.argv[2])",
            "    if not target.is_absolute():",
            "        if target.parts and target.parts[0] == package_root.name:",
            "            target = workspace_root / target",
            "        else:",
            "            target = package_root / target",
            '    if target.suffixes[-2:] == [".pirtm", ".bc"] or target.suffix == ".bc":',
            "        from pirtm.tools.pirtm_inspect import PIRTMInspector",
            "        raise SystemExit(PIRTMInspector.inspect_file(target, verbose=False))",
            "    from pirtm.transpiler.cli import PirtmCLI",
            "    cli = PirtmCLI()",
            '    parsed = cli.parser.parse_args(["inspect", sys.argv[2]])',
            "    raise SystemExit(parsed.func(parsed))",
            'print("No PIRTM target provided")',
        ]
    )
    command = [sys.executable, "-c", loader_script, str(repo_root.resolve())]
    if target is not None:
        command.append(target)
        mode = "inspect"
    else:
        mode = "probe"

    result = _run_external_command(
        repo_root=repo_root,
        args=command,
    )
    result["mode"] = mode
    result["target"] = target
    result["status"] = "ok" if result["returncode"] == 0 else "error"
    return result


def _run_ace_pipeline(task: str, repo_root: Path, *, mode: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="tooling-ace-") as tmpdir:
        output_path = Path(tmpdir) / "cas_commitment.json"
        command = [
            sys.executable,
            "-m",
            "ace.main",
            "--mode",
            mode,
            "--profile",
            "known-good",
            "--output",
            str(output_path),
        ]
        result = _run_external_command(
            repo_root=repo_root,
            args=command,
            pythonpath=[repo_root / "src"],
            timeout_seconds=60,
        )
        result["mode"] = mode
        result["task"] = task
        result["output_path"] = str(output_path)
        if output_path.exists():
            result["artifact"] = json.loads(output_path.read_text(encoding="utf-8"))
        result["status"] = "ok" if result["returncode"] == 0 else "error"
        return result


def _wrap_external_actuation_dispatch(task: str, repo_root: Path) -> dict[str, Any]:
    return _run_ace_pipeline(task, repo_root, mode="strict")


def _wrap_external_canary_rollout(task: str, repo_root: Path) -> dict[str, Any]:
    return _run_ace_pipeline(task, repo_root, mode="tolerance")


_WRAPPER_REGISTRY: dict[str, dict[str, Any]] = {
    "daemon_heartbeat": {
        "callable": _wrap_daemon_heartbeat,
        "role": "control_plane",
        "capability": "runtime_health",
    },
    "daemon_epsilon_adjust": {
        "callable": _wrap_daemon_epsilon_adjust,
        "role": "control_plane",
        "capability": "runtime_health",
    },
    "contractivity_check": {
        "callable": None,
        "external_callable": _wrap_external_contractivity_check,
        "role": "contract_runtime",
        "capability": "dialect_verification",
    },
    "actuation_dispatch": {
        "callable": None,
        "external_callable": _wrap_external_actuation_dispatch,
        "role": "actuation",
        "capability": "execution_handoff",
    },
    "canary_rollout": {
        "callable": None,
        "external_callable": _wrap_external_canary_rollout,
        "role": "actuation",
        "capability": "canary_rollout",
    },
    "health_check": {
        "callable": _wrap_health_check,
        "role": "control_plane",
        "capability": "runtime_health",
    },
    "manifest_status": {
        "callable": _wrap_manifest_status,
        "role": "control_plane",
        "capability": "mcp_dispatch",
    },
    "phase_mirror": {
        "callable": _wrap_phase_mirror,
        "role": "governance_gate",
        "capability": "policy_review",
    },
    "multiplicity_execute_article": {
        "callable": _wrap_multiplicity_execute_article,
        "role": "control_plane",
        "capability": "knowledge_execution",
    },
    "ledger_query": {
        "callable": _wrap_ledger_query,
        "role": "control_plane",
        "capability": "mcp_dispatch",
    },
}