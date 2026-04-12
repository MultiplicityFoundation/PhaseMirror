"""Wave-3 lever branch manager: create lever branch."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from mcp_server._yaml import dump_yaml_file, load_yaml_file

REPO_ROOT = Path(__file__).resolve().parents[2]
LEVER_MANIFEST_PATH = REPO_ROOT / "state" / "lever_manifest.yaml"


def create_lever_branch(
    branch_name: str,
    base_branch: str = "main",
    existing_branch_count: int = 0,
    max_concurrent: int = 3,
    dependencies: list[str] | None = None,
    metadata_json: str | None = None,
) -> dict[str, object]:
    """Create a new lever branch if the strategy permits."""
    if not branch_name:
        raise ValueError("branch_name is required")

    if existing_branch_count >= max_concurrent:
        return {
            "method": "create_lever_branch",
            "status": "blocked",
            "reason": "max_concurrent_exceeded",
            "existing_branch_count": existing_branch_count,
            "max_concurrent": max_concurrent,
            "branch_name": branch_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    unresolved = [d for d in (dependencies or []) if not d.strip()]
    if unresolved:
        return {
            "method": "create_lever_branch",
            "status": "blocked",
            "reason": "unresolved_dependencies",
            "unresolved_dependencies": unresolved,
            "branch_name": branch_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # Persist branch creation to lever manifest
    manifest_dir = LEVER_MANIFEST_PATH.parent
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_yaml_file(LEVER_MANIFEST_PATH) or {
        "active_branches": [],
        "max_concurrent": max_concurrent,
        "policy": "five-branch-reorganization",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    active_branches = manifest.get("active_branches", [])
    if branch_name in active_branches:
        return {
            "method": "create_lever_branch",
            "status": "blocked",
            "reason": "branch_already_exists",
            "branch_name": branch_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    if len(active_branches) >= max_concurrent:
        return {
            "method": "create_lever_branch",
            "status": "blocked",
            "reason": "max_concurrent_exceeded",
            "existing_branch_count": len(active_branches),
            "max_concurrent": max_concurrent,
            "branch_name": branch_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    active_branches.append(branch_name)
    manifest["active_branches"] = active_branches
    manifest["max_concurrent"] = max_concurrent
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    dump_yaml_file(LEVER_MANIFEST_PATH, manifest)

    return {
        "method": "create_lever_branch",
        "status": "approved",
        "branch_name": branch_name,
        "base_branch": base_branch,
        "existing_branch_count": len(active_branches),
        "max_concurrent": max_concurrent,
        "dependencies": dependencies or [],
        "metadata_json": metadata_json,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "lever_manifest": manifest,
    }
