"""Gate-level tool for Phase Mirror deployment authorization."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

from governance.ledger import get_phase_mirror_audit_ledger
from mcp_server._yaml import load_yaml_file

REPO_ROOT = Path(__file__).resolve().parents[2]
LEVER_MANIFEST_PATH = REPO_ROOT / "state" / "lever_manifest.yaml"


def deploy_authorization() -> dict[str, object]:
    """Evaluate production go/no-go gate using lever manifest and root hygiene.

    This tool implements the ADR-024 go/no-go criteria.
    """
    blockers: list[str] = []
    manifest = {}
    if LEVER_MANIFEST_PATH.exists():
        manifest = load_yaml_file(LEVER_MANIFEST_PATH) or {}
    else:
        blockers.append("missing_lever_manifest")

    active_branches = manifest.get("active_branches", [])
    max_concurrent = int(manifest.get("max_concurrent", 3))

    if len(active_branches) > max_concurrent:
        blockers.append("lever_branch_concurrency_exceeded")

    # Root hygiene check for .log files in git index
    from subprocess import check_output, CalledProcessError

    try:
        tracked_logs = check_output(["git", "ls-files", "*.log"], text=True).strip().splitlines()
        tracked_logs += check_output(["git", "ls-files", "**/*.log"], text=True).strip().splitlines()
        tracked_logs = [p for p in tracked_logs if p]
    except CalledProcessError:
        tracked_logs = []

    if tracked_logs:
        blockers.append("tracked_log_files_present")

    gitignore = Path(REPO_ROOT / ".gitignore").read_text() if (REPO_ROOT / ".gitignore").exists() else ""
    if "*.log" not in gitignore or "**/*.log" not in gitignore:
        blockers.append("gitignore_missing_log_pattern")

    status = "go" if not blockers else "no-go"
    result = {
        "method": "deploy_authorization",
        "event": "deploy_authorization",
        "status": status,
        "goals_met": len(blockers) == 0,
        "blockers": blockers,
        "manifest": manifest,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }

    ledger = get_phase_mirror_audit_ledger()
    # Persist a high-level audit record with explicit event metadata.
    ledger.append(result, timestamp=datetime.now(timezone.utc).isoformat() + "Z")

    return result
