"""Wave-3 lever branch manager: remove/cleanup lever branch."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from mcp_server._yaml import dump_yaml_file, load_yaml_file

REPO_ROOT = Path(__file__).resolve().parents[2]
LEVER_MANIFEST_PATH = REPO_ROOT / "state" / "lever_manifest.yaml"


def remove_lever_branch(branch_name: str) -> dict[str, object]:
    if not branch_name:
        raise ValueError("branch_name is required")

    if not LEVER_MANIFEST_PATH.exists():
        return {
            "method": "remove_lever_branch",
            "status": "not_found",
            "reason": "missing_lever_manifest",
            "branch_name": branch_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    manifest = load_yaml_file(LEVER_MANIFEST_PATH) or {}
    active_branches = manifest.get("active_branches", [])

    if branch_name not in active_branches:
        return {
            "method": "remove_lever_branch",
            "status": "not_found",
            "reason": "branch_not_active",
            "branch_name": branch_name,
            "active_branches": active_branches,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    active_branches.remove(branch_name)

    archived = manifest.get("archived_branches", [])
    if branch_name not in archived:
        archived.append(branch_name)

    manifest["active_branches"] = active_branches
    manifest["archived_branches"] = archived
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    dump_yaml_file(LEVER_MANIFEST_PATH, manifest)

    return {
        "method": "remove_lever_branch",
        "status": "removed",
        "branch_name": branch_name,
        "active_branches": active_branches,
        "archived_branches": archived,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
