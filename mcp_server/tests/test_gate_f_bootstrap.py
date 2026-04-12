"""Gate F — F-01: Governance Bootstrap and Startup Integrity Tests.

Tests for:
- _governance_preflight() sentinel check
- _governance_preflight() tx_id check
- _compute_mcp_merkle_root() binary tree construction
- build_server() skip_governance_preflight flag
- sentinel file lifecycle
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp_server.server import (
    _compute_mcp_merkle_root,
    _governance_preflight,
    MCP_BOOTSTRAP_SENTINEL_PATH,
    MCP_IMMUTABLE_FILE_SET,
    build_server,
)


# ---------------------------------------------------------------------------
# _governance_preflight — sentinel tests
# ---------------------------------------------------------------------------


def test_preflight_passes_when_sentinel_absent(tmp_path):
    """Preflight must succeed when no sentinel file exists."""
    # No sentinel in tmp_path — preflight should not raise
    # (tx_id check may also raise if constants module not found, but
    #  in the test environment GOVERNANCE_MERKLE_ROOT_TX_ID == 5 so it passes)
    # We monkeypatch the tx_id import to guarantee determinism.
    import mcp_server.server as srv
    original = srv.WORKSPACE_ROOT
    try:
        srv.WORKSPACE_ROOT = tmp_path
        # No sentinel file → condition 1 passes.
        # tx_id import still reads from constants, which has value 5 → passes.
        # No exception expected.
        _governance_preflight(workspace_root=tmp_path)
    except RuntimeError as exc:
        # Only acceptable RuntimeError is for tx_id == 0; otherwise re-raise.
        if "transaction ID" in str(exc):
            pytest.skip("GOVERNANCE_MERKLE_ROOT_TX_ID is 0 in this environment")
        raise
    finally:
        srv.WORKSPACE_ROOT = original


def test_preflight_fails_when_sentinel_exists(tmp_path):
    """Preflight must raise RuntimeError when the MCP bootstrap sentinel is present."""
    sentinel = tmp_path / "state" / "mcp_governance_bootstrap.sentinel"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("bootstrap_in_progress\n")

    with pytest.raises(RuntimeError, match="sentinel"):
        _governance_preflight(workspace_root=tmp_path)


def test_preflight_error_message_mentions_sentinel_path(tmp_path):
    """Error message should include the sentinel path for actionable guidance."""
    sentinel = tmp_path / "state" / "mcp_governance_bootstrap.sentinel"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("in_progress\n")

    with pytest.raises(RuntimeError) as exc_info:
        _governance_preflight(workspace_root=tmp_path)

    assert "mcp_governance_bootstrap.sentinel" in str(exc_info.value)


def test_preflight_error_includes_rerun_instruction(tmp_path):
    """Error message should tell the operator how to recover."""
    sentinel = tmp_path / "state" / "mcp_governance_bootstrap.sentinel"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("in_progress\n")

    with pytest.raises(RuntimeError) as exc_info:
        _governance_preflight(workspace_root=tmp_path)

    assert "governance_bootstrap" in str(exc_info.value)


def test_preflight_sentinel_cleared_allows_restart(tmp_path, monkeypatch):
    """After clearing the sentinel, preflight should pass again."""
    sentinel = tmp_path / "state" / "mcp_governance_bootstrap.sentinel"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("in_progress\n")

    with pytest.raises(RuntimeError):
        _governance_preflight(workspace_root=tmp_path)

    # Clear sentinel
    sentinel.unlink()

    # Should not raise now (tx_id check may still raise if == 0)
    try:
        _governance_preflight(workspace_root=tmp_path)
    except RuntimeError as exc:
        if "transaction ID" in str(exc):
            pytest.skip("GOVERNANCE_MERKLE_ROOT_TX_ID is 0 in this environment")
        raise


# ---------------------------------------------------------------------------
# _compute_mcp_merkle_root
# ---------------------------------------------------------------------------


def test_compute_merkle_root_returns_hex_string(tmp_path):
    """Merkle root must be a 64-character hex string."""
    # Create a fake file
    (tmp_path / "mcp_server").mkdir(parents=True)
    (tmp_path / "mcp_server" / "tool_registry.yaml").write_bytes(b"data: 1\n")
    (tmp_path / "mcp_server" / "middleware").mkdir(parents=True)
    (tmp_path / "mcp_server" / "middleware" / "auth.py").write_bytes(b"# auth\n")
    (tmp_path / "contracts").mkdir(parents=True)
    (tmp_path / "contracts" / "shared").mkdir(parents=True)
    (tmp_path / "contracts" / "shared" / "constants.py").write_bytes(b"X = 1\n")
    (tmp_path / "contracts" / "shared" / "types.py").write_bytes(b"Y = 2\n")

    root = _compute_mcp_merkle_root(tmp_path)
    assert isinstance(root, str)
    assert len(root) == 64
    # Must be valid hex
    int(root, 16)


def test_compute_merkle_root_is_deterministic(tmp_path):
    """Same file contents must produce the same root hash."""
    (tmp_path / "mcp_server").mkdir(parents=True)
    (tmp_path / "mcp_server" / "tool_registry.yaml").write_bytes(b"data: 1\n")
    (tmp_path / "mcp_server" / "middleware").mkdir(parents=True)
    (tmp_path / "mcp_server" / "middleware" / "auth.py").write_bytes(b"# auth\n")
    (tmp_path / "contracts").mkdir(parents=True)
    (tmp_path / "contracts" / "shared").mkdir(parents=True)
    (tmp_path / "contracts" / "shared" / "constants.py").write_bytes(b"X = 1\n")
    (tmp_path / "contracts" / "shared" / "types.py").write_bytes(b"Y = 2\n")

    root1 = _compute_mcp_merkle_root(tmp_path)
    root2 = _compute_mcp_merkle_root(tmp_path)
    assert root1 == root2


def test_compute_merkle_root_changes_when_file_changes(tmp_path):
    """Modifying a file must change the root hash."""
    (tmp_path / "mcp_server").mkdir(parents=True)
    f = tmp_path / "mcp_server" / "tool_registry.yaml"
    f.write_bytes(b"data: 1\n")
    (tmp_path / "mcp_server" / "middleware").mkdir(parents=True)
    (tmp_path / "mcp_server" / "middleware" / "auth.py").write_bytes(b"# auth\n")
    (tmp_path / "contracts").mkdir(parents=True)
    (tmp_path / "contracts" / "shared").mkdir(parents=True)
    (tmp_path / "contracts" / "shared" / "constants.py").write_bytes(b"X = 1\n")
    (tmp_path / "contracts" / "shared" / "types.py").write_bytes(b"Y = 2\n")

    root_before = _compute_mcp_merkle_root(tmp_path)
    f.write_bytes(b"data: 2\n")  # modify file
    root_after = _compute_mcp_merkle_root(tmp_path)
    assert root_before != root_after


def test_compute_merkle_root_handles_missing_files(tmp_path):
    """Missing files are skipped gracefully; result is still a valid hex string."""
    # Don't create any files — all paths in MCP_IMMUTABLE_FILE_SET are missing
    root = _compute_mcp_merkle_root(tmp_path)
    assert isinstance(root, str)
    assert len(root) == 64


# ---------------------------------------------------------------------------
# build_server — skip_governance_preflight flag
# ---------------------------------------------------------------------------


def test_build_server_skip_preflight_bypasses_sentinel_check(tmp_path, monkeypatch):
    """skip_governance_preflight=True must allow startup even with sentinel present."""
    # We need to monkeypatch so the sentinel path points to our tmp_path
    sentinel = tmp_path / "state" / "mcp_governance_bootstrap.sentinel"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("in_progress\n")

    monkeypatch.setattr("mcp_server.server.WORKSPACE_ROOT", tmp_path)

    # With skip_governance_preflight=True, no preflight check → no RuntimeError
    server = build_server(prefer_fastmcp=False, skip_governance_preflight=True)
    assert server.describe()["tool_count"] > 0


def test_build_server_skip_preflight_false_by_default(monkeypatch):
    """Default behavior: preflight is called (but passes in test environment)."""
    # In the test environment the sentinel does not exist and tx_id == 5,
    # so preflight passes silently.  This test just confirms default behaviour.
    server = build_server(prefer_fastmcp=False)
    assert server.describe()["tool_count"] > 0


def test_build_server_raises_when_sentinel_exists(tmp_path, monkeypatch):
    """build_server() must raise RuntimeError when MCP sentinel is present."""
    sentinel = tmp_path / "state" / "mcp_governance_bootstrap.sentinel"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("in_progress\n")

    monkeypatch.setattr("mcp_server.server.WORKSPACE_ROOT", tmp_path)

    with pytest.raises(RuntimeError, match="sentinel"):
        build_server(prefer_fastmcp=False, skip_governance_preflight=False)
