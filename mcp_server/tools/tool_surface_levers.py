"""Wave-3 lever branch manager: surface candidate levers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from mcp_server._yaml import dump_yaml_file, load_yaml_file


REPO_ROOT = Path(__file__).resolve().parents[2]
LEVER_MANIFEST_PATH = REPO_ROOT / "state" / "lever_manifest.yaml"


def _default_lever_manifest() -> dict[str, object]:
    return {
        "active_branches": [],
        "max_concurrent": 3,
        "policy": "five-branch-reorganization",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def surface_levers() -> dict[str, object]:
    """Return current lever manager surface state and persist baseline."""
    manifest_dir = LEVER_MANIFEST_PATH.parent
    manifest_dir.mkdir(parents=True, exist_ok=True)

    if LEVER_MANIFEST_PATH.exists():
        manifest = load_yaml_file(LEVER_MANIFEST_PATH) or {}
    else:
        manifest = _default_lever_manifest()
        dump_yaml_file(LEVER_MANIFEST_PATH, manifest)

    # ensure currency
    manifest.setdefault("updated_at", datetime.now(timezone.utc).isoformat())

    return {
        "method": "surface_levers",
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "lever_manifest": manifest,
    }
