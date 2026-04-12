"""Wave 1 manifest-status tool."""

from __future__ import annotations

from pathlib import Path

from mcp_server._yaml import load_yaml_file


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "mcp" / "manifest" / "mcp_manifest.yaml"


def manifest_status() -> dict[str, object]:
    """Return a compact ownership and layer summary from the MCP manifest."""
    manifest = load_yaml_file(MANIFEST_PATH)
    workspace = manifest.get("workspace", {})
    rings = manifest.get("rings", [])
    tooling_layers = manifest.get("tooling_layers", [])

    return {
        "manifest_id": manifest.get("manifest_id"),
        "workspace": {
            "repository": workspace.get("repository"),
            "branch": workspace.get("branch"),
            "role": workspace.get("role"),
            "status": workspace.get("status"),
        },
        "active_ring_count": len(rings),
        "rings": [
            {
                "name": ring.get("name"),
                "status": ring.get("status"),
                "local_path": ring.get("local_path"),
            }
            for ring in rings
        ],
        "tooling_layers": [
            {
                "layer": layer.get("layer"),
                "local_path": layer.get("local_path"),
                "status": layer.get("status"),
            }
            for layer in tooling_layers
        ],
    }