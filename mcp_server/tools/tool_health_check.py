"""Wave 3 PMD health-check tool."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_ROOTS = {
    "mcp_server": "mcp_server/",
    "daemon": "daemon/",
    "digital_twin": "packages/digital_twin/",
    "rollback": "rollback/",
    "ensemble": "ensemble/",
}


def health_check() -> dict[str, object]:
    """Report whether the canonical PMD roots and Wave 2 runtime files are present."""
    roots: dict[str, dict[str, object]] = {}
    missing_paths: list[str] = []
    runtime_surfaces = {
        "daemon_watchdog": "daemon/watchdog.py",
        "daemon_scheduler": "daemon/scheduler.py",
        "digital_twin": "packages/digital_twin/twin.py",
        "rollback_checkpoint": "rollback/checkpoint.py",
        "rollback_manager": "rollback/rollback_manager.py",
        "ensemble_manager": "ensemble/ensemble_manager.py",
        "ensemble_registry": "ensemble/member_registry.yaml",
    }
    organ_indexes = {
        "state": "state/README.md",
        "contracts": "contracts/README.md",
        "automata": "packages/automata/README.md",
        "controllers": "packages/controllers/README.md",
        "simulators": "packages/simulators/README.md",
        "witnesses": "packages/witnesses/README.md",
        "zeta": "packages/zeta/README.md",
    }
    surfaces: dict[str, dict[str, object]] = {}
    indexes: dict[str, dict[str, object]] = {}

    for name, relative_path in CANONICAL_ROOTS.items():
        exists = (REPO_ROOT / relative_path).exists()
        roots[name] = {
            "path": relative_path,
            "exists": exists,
        }
        if not exists:
            missing_paths.append(relative_path)

    for name, relative_path in runtime_surfaces.items():
        exists = (REPO_ROOT / relative_path).exists()
        surfaces[name] = {
            "path": relative_path,
            "exists": exists,
        }
        if not exists:
            missing_paths.append(relative_path)

    for name, relative_path in organ_indexes.items():
        exists = (REPO_ROOT / relative_path).exists()
        indexes[name] = {
            "path": relative_path,
            "exists": exists,
        }
        if not exists:
            missing_paths.append(relative_path)

    return {
        "server": "tooling-pmd",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if not missing_paths else "warn",
        "missing_paths": missing_paths,
        "roots": roots,
        "runtime_surfaces": surfaces,
        "organ_indexes": indexes,
    }